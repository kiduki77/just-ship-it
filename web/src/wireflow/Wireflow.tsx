import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { post } from "../api";
import type { Row as Card } from "../api";
import { PlainKey, t } from "../i18n";
import { Badge } from "../components/ui";
import { Blocks } from "./blocks";
import { displayOrder, looseFrames, stepFrames } from "./order";
import type { Asked, Landing, Plan, PlanScreen, Redraft, Row, Story } from "./types";
import "./wireflow.css";

/** Why a spec card's screens are not on screen: the detail has not arrived, or the server could not draw them. A
 * card that promises screens (`action_params.planned`) cannot be approved while they are missing. */
export function screensUnread(r: Card): "loading" | "failed" | null {
  if (r.action_kind !== "dev.spec_approve" || !(r.action_params?.planned > 0) || r.screens) return null;
  return r.screens === undefined ? "loading" : "failed";
}

/** The server's limit on one note (contracts/requests.py SCREEN_NOTE_CHARS). */
const NOTE_CHARS = 1000;

const STATE_WORD = new Map<string, PlainKey>([
  ["empty", "decide.wf.state.empty"], ["loading", "decide.wf.state.loading"], ["error", "decide.wf.state.error"],
  ["success", "decide.wf.state.success"], ["warning", "decide.wf.state.warning"],
]);
const STATE_IF = new Map<string, PlainKey>([
  ["empty", "decide.wf.if.empty"], ["loading", "decide.wf.if.loading"], ["error", "decide.wf.if.error"],
  ["success", "decide.wf.if.success"], ["warning", "decide.wf.if.warning"],
]);
const known = (m: Map<string, PlainKey>, state: string) => m.get(state.trim().toLowerCase());
// The server sends one of five states or "" for a screen in normal use (wireflow.state_word); a writer's own word
// never reaches the card.
const stateWord = (state: string) => { const k = known(STATE_WORD, state); return k ? t(k) : ""; };
const stateIf = (state: string) => t(known(STATE_IF, state) ?? "decide.wf.if.other");
export const named = (name: string, state: string) => (stateWord(state) ? `${name} · ${stateWord(state)}` : name);
/** A main step says its state only when the state is not the ordinary one. */
const worthSaying = (state: string) => ["loading", "error", "warning"].includes(state.trim().toLowerCase());

const CHANGE: Record<"new" | "changed", PlainKey> = { new: "decide.wf.change.new", changed: "decide.wf.change.changed" };
const OUTCOME: Record<Landing["outcome"], { word: PlainKey; tone: string }> = {
  changed: { word: "decide.wf.landed.changed", tone: "ok" },
  moved: { word: "decide.wf.landed.moved", tone: "ok" },
  dropped: { word: "decide.wf.landed.dropped", tone: "warn" },
  missed: { word: "decide.wf.landed.missed", tone: "bad" },
};

interface View {
  plan: Plan;
  open: string | null;
  toggle: (slot: string) => void;
  close: (slot: string) => void;
  frames: Map<string, HTMLButtonElement>;
  /** What the owner is typing on each screen of an open card; null on a card that is decided. */
  notes: Record<string, string> | null;
  onNote: ((screen: string, note: string) => void) | null;
  /** The number on each screen that has a note, typed or sent, in the order the frames appear on the card. */
  pins: Map<string, number>;
}

/** The owner's note on a screen: what they are typing on an open card, what they sent on one they sent back. */
const noteOn = (view: Pick<View, "plan" | "notes">, id: string) =>
  (view.notes ? view.notes[id]?.trim() : view.plan.screens[id]?.note) || "";

/** A screen by name, whether this draft still has it or it was taken out. */
function screenName(plan: Plan, id: string): string {
  const here = plan.screens[id] ?? plan.removed.find((x) => x.id === id);
  return here ? named(here.name, here.state) : id;
}

export function Sketch({ screen }: { screen: Pick<PlanScreen, "name" | "state" | "shows" | "blocks"> }) {
  return (
    <span className="wf-sk" data-state={screen.state.trim().toLowerCase()} aria-hidden="true">
      <span className="wf-bar">{screen.name}</span>
      <span className="wf-in">
        {screen.blocks.length > 0 ? <Blocks blocks={screen.blocks} /> : <span className="wf-shows">{screen.shows}</span>}
      </span>
    </span>
  );
}

/** One planned screen as a button: its accessible name is the screen and its state, its description is what it
 * shows and the owner's note on it, and the sketch itself is hidden from screen readers. */
function FrameButton({ slot, id, size, view }: { slot: string; id: string; size: "main" | "branch"; view: View }) {
  const desc = useId();
  const screen = view.plan.screens[id];
  const pin = view.pins.get(id);
  return (
    <>
      <button type="button" className={`wf-f wf-f-${size}`} aria-expanded={view.open === slot} aria-describedby={desc}
        aria-label={stateWord(screen.state) ? t("decide.wf.frame", { name: screen.name, state: stateWord(screen.state) }) : screen.name}
        ref={(el) => { if (el) view.frames.set(slot, el); else view.frames.delete(slot); }}
        onClick={() => view.toggle(slot)}>
        <Sketch screen={screen} />
        {pin && <span className="wf-pin" aria-hidden="true">{pin}</span>}
      </button>
      <span id={desc} className="sr-only">
        {screen.shows}{pin && ` ${t("decide.wf.note.pinned", { n: pin, note: noteOn(view, id) })}`}
      </span>
    </>
  );
}

function Answers({ asks }: { asks: Asked[] }) {
  const answered = asks.filter((a) => a.answer);
  if (!answered.length) return null;
  return (
    <>
      {answered.map((a) => (
        <p key={a.question} className="wf-asked">
          <span className="wf-mark">{t("decide.wf.asked")}</span>
          <span>{a.answer}</span>
        </p>
      ))}
    </>
  );
}

/** Where one note landed, as the frame `here` shows it: its own note with what became of it, or a note from another
 * screen that changed this one. With no frame to sit under (`here` null), the line names the screen it was written on. */
function LandingLine({ l, here, plan }: { l: Landing; here: string | null; plan: Plan }) {
  const own = l.screen === (here ?? l.screen);
  const elsewhere = l.on.filter((id) => id !== l.screen);
  const why = [
    here !== l.screen && t("decide.wf.landed.was_on", { screen: plan.screens[l.screen] ? screenName(plan, l.screen) : l.name }),
    own && elsewhere.length > 0 && t("decide.wf.landed.on", { screens: elsewhere.map((id) => screenName(plan, id)).join(", ") }),
    own && l.reason,
  ].filter(Boolean).join(" ");
  return (
    <li className="wf-land">
      <Badge value={t(own ? OUTCOME[l.outcome].word : "decide.wf.landed.from")} tone={own ? OUTCOME[l.outcome].tone : "ok"} raw />
      <span className="wf-land-note">{l.note}</span>
      {why && <span className="wf-land-why">{why}</span>}
    </li>
  );
}

/** What the owner said on a screen and what came of it: the note being typed or sent, then each landing. `typed`
 * is off where the note box itself is on screen. */
function Notes({ id, view, typed = true }: { id: string; view: View; typed?: boolean }) {
  const screen = view.plan.screens[id];
  const pin = view.pins.get(id);
  return (
    <>
      {pin && (typed || !view.onNote) && (
        <p className="wf-noted">
          <span className="wf-pin" aria-hidden="true">{pin}</span>
          <span><span className="sr-only">{t("decide.wf.note.sent")} </span>{noteOn(view, id)}</span>
        </p>
      )}
      {screen.landed.length > 0 && (
        <ul className="wf-landed">
          {screen.landed.map((l) => <LandingLine key={l.screen} l={l} here={id} plan={view.plan} />)}
        </ul>
      )}
    </>
  );
}

const Changed = ({ screen }: { screen: PlanScreen }) =>
  (screen.change ? <Badge value={t(CHANGE[screen.change])} tone="info" raw /> : null);

/** A frame opened large in place, under its step: every block, the full words, what came of the owner's notes on it,
 * and on an open card the box to leave one. Esc or Close shuts it; what was typed stays. */
function Big({ slot, id, view }: { slot: string; id: string; view: View }) {
  const box = useRef<HTMLElement>(null);
  const noteId = useId();
  const screen = view.plan.screens[id];
  useEffect(() => box.current?.querySelector("button")?.focus(), []);
  return (
    <figure className="wf-big" ref={box} onKeyDown={(e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      view.close(slot);
    }}>
      <figcaption className="wf-big-h">
        <span className="wf-big-t">{screen.name}{stateWord(screen.state) && <span className="muted"> · {stateWord(screen.state)}</span>}</span>
        <button type="button" className="btn-ghost small" onClick={() => view.close(slot)}>{t("decide.wf.close")}</button>
      </figcaption>
      <Sketch screen={screen} />
      <span className="sr-only">{screen.shows}</span>
      <Notes id={id} view={view} typed={false} />
      {view.onNote && (
        <div className="field wf-notebox">
          <label htmlFor={noteId}>{t("decide.wf.note.label")}</label>
          <textarea id={noteId} rows={2} maxLength={NOTE_CHARS} value={view.notes?.[id] ?? ""}
            aria-describedby={`${noteId}-hint`} onChange={(e) => view.onNote!(id, e.target.value)} />
          <p id={`${noteId}-hint`} className="muted small">{t("decide.wf.note.hint")}</p>
        </div>
      )}
    </figure>
  );
}

/** A frame with the words that say when the user sees it. */
function Alt({ slot, id, caption, view }: { slot: string; id: string; caption: string; view: View }) {
  const screen = view.plan.screens[id];
  return (
    <div className="wf-alt">
      <FrameButton slot={slot} id={id} size="branch" view={view} />
      <p className="wf-cap">{caption}</p>
      <Changed screen={screen} />
      <Answers asks={screen.asks} />
      {screen.landed.length > 0 && (
        <ul className="wf-landed">
          {screen.landed.map((l) => <LandingLine key={l.screen} l={l} here={id} plan={view.plan} />)}
        </ul>
      )}
    </div>
  );
}

function StepItem({ row, ri, si, view }: { row: Row; ri: number; si: number; view: View }) {
  const step = row.steps[si];
  const shown = stepFrames(view.plan, row, si);
  if (!shown) return null;
  const screen = view.plan.screens[step.screen];
  const last = si === row.steps.length - 1;
  const main = `${ri}.${si}`;
  const branches = shown.branches.map((id) => ({ slot: `${main}.${id}`, id }));
  const empty = last && row.empty ? row.empty : null;
  const emptyId = shown.empty;
  const emptySlot = `${ri}.empty`;
  const here = [{ slot: main, id: step.screen }, ...branches].find((x) => x.slot === view.open);
  return (
    <li className="wf-step">
      <div className="wf-line">
        <FrameButton slot={main} id={step.screen} size="main" view={view} />
        <div className="wf-txt">
          <p className="wf-n">{t("decide.wf.step", { n: si + 1 })}</p>
          <p className="wf-name">
            {screen.name}{worthSaying(screen.state) && <span className="muted"> · {stateWord(screen.state)}</span>}
            <Changed screen={screen} />
          </p>
          <Answers asks={screen.asks} />
          <Notes id={step.screen} view={view} />
          {last && row.then && <p className="wf-then"><b>{t("decide.wf.then")}</b> {row.then}</p>}
          {branches.length > 0 && (
            <div className="wf-alts">
              {branches.map((b) => (
                <Alt key={b.slot} slot={b.slot} id={b.id} caption={stateIf(view.plan.screens[b.id].state)} view={view} />
              ))}
            </div>
          )}
        </div>
      </div>
      {here && <Big key={here.slot} slot={here.slot} id={here.id} view={view} />}
      {empty && (
        <>
          <div className="wf-line wf-line-empty">
            {emptyId ? <FrameButton slot={emptySlot} id={emptyId} size="branch" view={view} /> : <span />}
            <div className="wf-txt">
              <p className="wf-cap">{t("decide.wf.if.empty")}</p>
              {empty.then && <p className="wf-then"><b>{t("decide.wf.then")}</b> {empty.then}</p>}
              {emptyId && <Answers asks={view.plan.screens[emptyId].asks} />}
              {emptyId && <Notes id={emptyId} view={view} />}
            </div>
          </div>
          {emptyId && view.open === emptySlot && <Big key={emptySlot} slot={emptySlot} id={emptyId} view={view} />}
        </>
      )}
      {!last && (
        <div className="wf-arrow" aria-hidden="true">
          <span className="wf-down" />
          <span className="wf-do">{step.do}</span>
        </div>
      )}
    </li>
  );
}

function FlowRow({ row, ri, story, view }: { row: Row; ri: number; story: Story | undefined; view: View }) {
  const id = useId();
  return (
    <div className="wf-row">
      <p className="wf-head" id={id}>
        <Badge value={story?.priority ?? "P1"} tone={(story?.priority ?? "P1") === "P1" ? "info" : "muted"} raw />
        {story && <span className="wf-says">{story.says}</span>}
      </p>
      <ol className="wf-steps" aria-labelledby={id}>
        {row.steps.map((_, si) => <StepItem key={si} row={row} ri={ri} si={si} view={view} />)}
      </ol>
    </div>
  );
}

/** Where the redraft a sent-back card asked for stands. A failed one says so, a stopped one (the owner cancelled it)
 * only says it stopped, and either can be run again or given up on: dropping the notes declines the spec for good, so
 * it asks twice. The owner's notes are on the frames below either way. */
function RedraftState({ redraft, requestId, onChange }: {
  redraft: Redraft; requestId: number; onChange: (() => void) | null;
}) {
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const whatId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const wasArmed = useRef(false);
  useEffect(() => {
    if (armed) cancel.current?.focus();
    else if (wasArmed.current) trigger.current?.focus();
    wasArmed.current = armed;
  }, [armed]);
  if (redraft.state === "done") {
    return (
      <p className="wf-redraft muted">
        {t("decide.wf.redraft.done")}
        {redraft.card && <> <Link to={`/inbox?req=${redraft.card}`}>{t("decide.wf.redraft.open")}</Link></>}
      </p>
    );
  }
  if (redraft.state === "writing") return <p className="wf-redraft muted" role="status">{t("decide.wf.redraft.writing")}</p>;
  if (redraft.state === "dropped") return <p className="wf-redraft muted">{t("decide.wf.redraft.dropped")}</p>;
  const run = async (go: () => Promise<unknown>, failed: (error: string) => string) => {
    setBusy(true);
    setError(null);
    try {
      await go();
      setArmed(false);
      onChange?.();
    } catch (e: any) {
      setError(failed(e.message));
    } finally {
      setBusy(false);
    }
  };
  const again = () => run(() => post(`/jobs/${redraft.retry_job}/retry`, {}),
    (error) => t("decide.wf.redraft.retry_failed", { error }));
  const drop = () => run(() => post(`/requests/${requestId}/drop-notes`, {}),
    (error) => t("decide.wf.redraft.drop_failed", { error }));
  const stopped = redraft.state === "stopped";
  return (
    <div className={stopped ? "wf-redraft muted" : "dp-warn wf-redraft"} role="note">
      {t(stopped ? "decide.wf.redraft.stopped" : "decide.wf.redraft.failed")}
      {armed ? (
        <div className="act-armed dp-armed" role="group" aria-labelledby={whatId}
          onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.preventDefault(); e.stopPropagation(); setArmed(false); } }}>
          <p id={whatId} className="act-what">{t("decide.wf.redraft.drop_what")}</p>
          <div className="row wrap sp-actions">
            <button ref={cancel} type="button" className="btn-ghost" disabled={busy} onClick={() => setArmed(false)}>{t("common.cancel")}</button>
            <button type="button" className="danger-solid" disabled={busy} onClick={drop}>{t(busy ? "common.working" : "decide.wf.redraft.drop_confirm")}</button>
          </div>
        </div>
      ) : (
        <>
          <span className="wf-redraft-acts">
            {redraft.retry_job && <button type="button" className="link" disabled={busy} onClick={again}>{t(busy ? "common.working" : "common.retry")}</button>}
            <button ref={trigger} type="button" className="link" disabled={busy} onClick={() => setArmed(true)}>{t("decide.wf.redraft.drop")}</button>
          </span>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

/** The planned screens of a spec card: a flow per P1 story, in the order a user meets the screens, then the screens
 * no flow walks. A sketch of what each screen holds, not of how it looks. On an open card the owner leaves a note on
 * a screen in its enlarged view (`notes`, `onNote`); the card of a redraft shows where each note landed. */
export function Wireflow({ plan, requestId, stories, notes, onNote, onChange }: {
  plan: Plan; requestId: number; stories: Story[]; notes: Record<string, string> | null;
  onNote: ((screen: string, note: string) => void) | null; onChange: (() => void) | null;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const frames = useRef(new Map<string, HTMLButtonElement>()).current;
  const titleId = useId();
  const pins = new Map(displayOrder(plan).filter((id) => noteOn({ plan, notes }, id)).map((id, i) => [id, i + 1]));
  const view: View = {
    plan, open, frames, notes, onNote, pins,
    toggle: (slot) => setOpen((o) => (o === slot ? null : slot)),
    close: (slot) => { setOpen(null); frames.get(slot)?.focus(); },
  };
  const loose = looseFrames(plan);
  if (!plan.rows.length && !loose.length) return null;
  const openLoose = loose.find((id) => view.open === `loose.${id}`);
  const answered = new Set([...Object.values(plan.screens), ...plan.removed].flatMap((x) => x.landed.map((l) => l.screen))
    .concat(plan.unplaced.map((l) => l.screen))).size;
  return (
    <section className="dp-sec wf" aria-labelledby={titleId}>
      <h3 id={titleId}>{t("decide.wf.title")}</h3>
      {(plan.redraft || answered > 0) && (
        <div className="wf-top">
          {plan.redraft && <RedraftState redraft={plan.redraft} requestId={requestId} onChange={onChange} />}
          {answered > 0 && <p>{t("decide.wf.redrawn", { n: answered })}</p>}
          {plan.unplaced.length > 0 && (
            <>
              <p className="wf-other">{t("decide.wf.unplaced")}</p>
              <ul className="wf-landed">
                {plan.unplaced.map((l) => <LandingLine key={l.screen} l={l} here={null} plan={plan} />)}
              </ul>
            </>
          )}
        </div>
      )}
      {plan.rows.map((row, ri) => (
        <FlowRow key={ri} row={row} ri={ri} story={stories.find((s) => s.id === row.story)} view={view} />
      ))}
      {loose.length > 0 && (
        <div className="wf-loose">
          <p className="wf-other">{t("decide.wf.other")}</p>
          <div className="wf-alts">
            {loose.map((id) => (
              <Alt key={id} slot={`loose.${id}`} id={id}
                caption={named(plan.screens[id].name, plan.screens[id].state)} view={view} />
            ))}
          </div>
          {openLoose && <Big key={view.open!} slot={view.open!} id={openLoose} view={view} />}
        </div>
      )}
      {plan.removed.length > 0 && (
        <div className="wf-loose">
          <p className="wf-other">{t("decide.wf.removed")}</p>
          <ul className="wf-gone">
            {plan.removed.map((x) => (
              <li key={x.id}>
                <span className="wf-gone-name">{named(x.name, x.state)}</span>
                {x.landed.length > 0 && (
                  <ul className="wf-landed">
                    {x.landed.map((l) => <LandingLine key={l.screen} l={l} here={x.id} plan={plan} />)}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {plan.dropped > 0 && <p className="muted small">{t("decide.wf.dropped", { n: plan.dropped })}</p>}
      <p className="muted small">{t("decide.wf.note")}</p>
    </section>
  );
}

/** The wireflow on a spec card, or why it is not there yet. `notes` and `onNote` are the open card's draft; both are
 * null on a card that is decided. */
export function SpecScreens({ r, stories, onRetry, notes, onNote }: {
  r: Card; stories: Story[]; onRetry: (() => void) | null; notes: Record<string, string> | null;
  onNote: ((screen: string, note: string) => void) | null;
}) {
  const unread = screensUnread(r);
  if (unread) {
    return r.status === "PENDING" ? (
      <p className="dp-warn" role="note">
        {t(unread === "loading" ? "decide.wf.loading" : "decide.wf.missing")}
        {unread === "failed" && onRetry && <> <button type="button" className="link" onClick={onRetry}>{t("common.retry")}</button></>}
      </p>
    ) : null;
  }
  return r.screens?.mode === "plan"
    ? <Wireflow plan={r.screens} requestId={r.id} stories={stories} notes={notes} onNote={onNote} onChange={onRetry} /> : null;
}
