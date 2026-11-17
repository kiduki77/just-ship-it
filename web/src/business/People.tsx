import { FormEvent, ReactNode, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, post } from "../api";
import { Icon } from "../components/icons";
import { Box, Chip, Hint, Section, Tone } from "../components/kit";
import { announce, settleFocus } from "../components/ui";
import { PlainKey, lookup, t } from "../i18n";
import { dueText } from "../today";
import { CustomersLink } from "./Customers";
import { Heard, HeardField, heardBody, heardChanged, heardStart } from "./HeardField";
import { ConfirmBox, Fail, useDo } from "./parts";
import { HeardChoice, People, Person } from "./types";

const STAGES = ["found", "contacted", "talked", "first_value", "repeat", "dropped"] as const;
const LADDER = STAGES.filter((s) => s !== "dropped");
const SOURCES = ["warm", "public_problem", "similar_tool", "participation", "inbound"] as const;
const STAGE_TONE: Record<string, Tone> = { found: "muted", contacted: "run", talked: "run", first_value: "ok", repeat: "ok", dropped: "muted" };
const PAGE = 20;
const MAX_QUOTE = 400;
const MAX_WHY = 300;
const MAX_NEXT = 300;
const web = (u: string | null | undefined) => (u && /^https?:\/\//.test(u) ? u : null);

// The server sends codes; a code this console has no words for shows as sent.
const stageText = (code: string) => lookup(`business.stage.${code}`) ?? code;
const sourceText = (code: string) => lookup(`business.source.${code}`) ?? code;
const platformText = (code: string) => lookup(`business.platform.${code}`) ?? code;

const isoDay = (v: string | null) => (v ? new Date(v).toLocaleDateString("sv-SE") : "");
const dayAt = (day: string) => (day ? new Date(`${day}T09:00:00`).toISOString() : null);

type Step = "stage" | "quote" | "heard" | "next";
const STEP_NAME: Record<Step, PlainKey> = {
  stage: "business.people.record_step.stage", quote: "business.people.record_step.quote", heard: "business-tracking.heard.step",
  next: "business.people.record_step.next",
};

/**
 * The owner's record of a conversation: where the person is now, what they said, and what comes next. The server
 * takes them as separate calls (stage, quote, how they heard, next), so they run in that order and stop at the first refusal;
 * the form then says what was saved and keeps what was not, so a retry sends only the rest.
 */
function RecordForm({ p, name, heardChoices, close, saved }: { p: Person; name: string; heardChoices: HeardChoice[] | null; close: () => void; saved: () => void }) {
  const uid = useId();
  // what the server holds now: a step that saved moves this at once, so a retry before the list reloads sends only the rest
  const [base, setBase] = useState({ stage: p.stage, next: p.next_action ?? "", day: isoDay(p.next_action_at) });
  const [first] = useState(() => ({ stage: p.stage, next: p.next_stages ?? [] }));
  // the list's own next_stages once it has reloaded; until then the first answer, minus the stage just left
  const forward = p.stage === base.stage ? (p.next_stages ?? []) : first.next.filter((s) => s !== first.stage);
  const choices = [base.stage, ...forward.filter((s) => s !== base.stage)];
  // what the server holds of the answer, moved at once by a save like the rest of `base`
  const [heardBase, setHeardBase] = useState(() => ({ heard_via: p.heard_via, heard_words: p.heard_words, heard_row_label: p.heard_row_label }));
  const [heard, setHeard] = useState<Heard>(() => heardStart(p));
  const [stage, setStage] = useState(p.stage);
  const [quote, setQuote] = useState("");
  const [next, setNext] = useState(p.next_action ?? "");
  const [day, setDay] = useState(isoDay(p.next_action_at));
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState<{ done: Step[]; failed: Step; text: string } | null>(null);
  const dropping = stage === "dropped" && base.stage !== "dropped";
  const nextChanged = !dropping && (next.trim() !== base.next || day !== base.day);
  const todo: Step[] = [...(stage !== base.stage ? ["stage" as const] : []), ...(quote.trim() ? ["quote" as const] : []),
    ...(heardChanged(heard, heardBase) ? ["heard" as const] : []), ...(nextChanged ? ["next" as const] : [])];
  const quoteOver = quote.length > MAX_QUOTE;
  const nextOver = next.length > MAX_NEXT;
  const reason = !todo.length ? t("business.people.record_nothing") : quoteOver || nextOver ? t("business.people.record_too_long") : null;
  const tomorrow = new Date(Date.now() + 86_400_000).toLocaleDateString("sv-SE");

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (reason || busy) return;
    setBusy(true);
    setFail(null);
    const done: Step[] = [];
    const call: Record<Step, () => Promise<unknown>> = {
      stage: () => post(`/people/${p.id}/stage`, { stage }),
      quote: () => post(`/people/${p.id}/quote`, { text: quote.trim() }),
      heard: () => api(`/people/${p.id}/heard`, { method: "PUT", body: JSON.stringify(heardBody(heard)) }),
      next: () => post(`/people/${p.id}/next`, { next_action: next.trim() || null, next_action_at: dayAt(day) }),
    };
    for (const step of todo) {
      try {
        await call[step]();
        done.push(step);
        if (step === "stage") setBase((b) => ({ ...b, stage }));
        if (step === "quote") setQuote("");
        if (step === "heard") setHeardBase({ heard_via: heardBody(heard).heard_via ?? heardBase.heard_via, heard_words: heard.words.trim() || null, heard_row_label: heardBase.heard_row_label });
        if (step === "next") setBase((b) => ({ ...b, next: next.trim(), day }));
      } catch (err: any) {
        setFail({ done, failed: step, text: err.message ?? String(err) });
        setBusy(false);
        if (done.length) saved();
        return;
      }
    }
    setBusy(false);
    announce(t("business.people.record_saved", { name }));
    saved();
    close();
  };

  const stepList = (steps: Step[]) => steps.map((s) => t(STEP_NAME[s])).join(", ");
  return (
    <form className="biz-record" onSubmit={save} aria-label={t("business.people.record_label", { name })} aria-busy={busy}
      onKeyDown={(e) => e.key === "Escape" && !busy && (e.stopPropagation(), close())}>
      <div className="biz-fields">
        <div className="field">
          <label htmlFor={`${uid}-stage`}>{t("business.people.record_stage")}</label>
          <select id={`${uid}-stage`} value={stage} onChange={(e) => setStage(e.target.value)} autoFocus>
            {choices.map((s) => (
              <option key={s} value={s}>{s === base.stage ? t("business.people.record_stage_same", { stage: stageText(s) }) : stageText(s)}</option>
            ))}
          </select>
          {dropping && <p className="field-note">{t("business.people.record_drop_note")}</p>}
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-quote`}>{t("business.people.record_quote")} <span className="muted">{t("business.people.optional_max", { n: MAX_QUOTE })}</span></label>
        <textarea id={`${uid}-quote`} rows={2} value={quote} onChange={(e) => setQuote(e.target.value)}
          aria-invalid={quoteOver || undefined} aria-describedby={`${uid}-quote-n`} />
        <p id={`${uid}-quote-n`} className={`field-note ${quoteOver ? "bad" : ""}`}>{t("business.people.record_count", { n: quote.length, max: MAX_QUOTE })}</p>
      </div>
      <HeardField value={heard} onChange={setHeard} choices={heardChoices} now={heardBase} disabled={busy}
        error={fail?.failed === "heard" ? fail.text : null} />
      {!dropping && (
        <div className="biz-fields">
          <div className="field">
            <label htmlFor={`${uid}-next`}>{t("business.people.record_next")} <span className="muted">{t("business.people.optional")}</span></label>
            <input id={`${uid}-next`} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="off"
              aria-invalid={nextOver || undefined} placeholder={t("business.people.record_next_ph")} />
            {nextOver && <p className="field-note bad">{t("business.people.record_count", { n: next.length, max: MAX_NEXT })}</p>}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-day`}>{t("business.people.record_date")} <span className="muted">{t("business.people.optional")}</span></label>
            <input id={`${uid}-day`} type="date" min={tomorrow} value={day} onChange={(e) => setDay(e.target.value)} />
          </div>
        </div>
      )}
      <p className="biz-note-s">{t("business.people.record_rule")}</p>
      {fail && (fail.failed !== "heard" || fail.done.length > 0) && (
        <p className={`biz-fail ${fail.done.length ? "partial" : ""}`} role="alert">
          {fail.done.length ? t("business.people.record_partial", { saved: stepList(fail.done), failed: t(STEP_NAME[fail.failed]), reason: fail.text })
            : t("business.people.record_failed", { failed: t(STEP_NAME[fail.failed]), reason: fail.text })}
        </p>
      )}
      <div className="row wrap">
        <button className="btn-primary" type="submit" disabled={busy || !!reason} aria-describedby={reason ? `${uid}-why` : undefined}>
          {t(busy ? "business.people.record_saving" : "business.people.record_save")}
        </button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={close}>{t("business.people.record_close")}</button>
        {reason && !busy && <span id={`${uid}-why`} className="muted small">{reason}</span>}
        {!reason && !busy && <span className="muted small">{t("business.people.record_will", { steps: stepList(todo) })}</span>}
      </div>
    </form>
  );
}

/** The row's rare, permanent action, kept one click away from the everyday one. */
function MoreMenu({ label, children }: { label: string; children: (close: () => void) => ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  const close = () => {
    if (ref.current) ref.current.open = false;
  };
  return (
    <details ref={ref} className="biz-menu" onKeyDown={(e) => e.key === "Escape" && ref.current?.open && (e.stopPropagation(), close(), ref.current.querySelector("summary")?.focus())}>
      <summary className="btn-ghost small" aria-label={label}>{t("business.people.more_short")}<Icon name="chevron" size={12} /></summary>
      <div className="biz-menu-pop" role="menu">{children(close)}</div>
    </details>
  );
}

function PersonRow({ p, choices, reload }: { p: Person; choices: HeardChoice[] | null; reload: () => void }) {
  const [armed, setArmed] = useState(false);
  const [recording, setRecording] = useState(false);
  const recordBtn = useRef<HTMLButtonElement>(null);
  const d = useDo();
  const name = p.display_name && p.display_name !== p.handle ? `${p.display_name} (${p.handle})` : p.handle;
  const found = web(p.where_found_url);
  // what they first said (often in public, where they were found) stays; a later one, such as a talk the owner
  // recorded, shows under it rather than in its place
  const first = p.quotes[0] ?? null;
  const latest = p.quotes.length > 1 ? p.quotes[p.quotes.length - 1] : null;
  const due = dueText(p.next_action_at);
  const erase = async () => {
    if (await d.run(() => post(`/people/${p.id}/archive`, {}))) {
      announce(t("business.people.erased", { name }));
      reload();
    }
  };
  const closeRecord = () => {
    setRecording(false);
    settleFocus(recordBtn.current);
  };
  return (
    <li className="biz-r">
      <div className="biz-r-h">
        <p className="biz-r-t">
          <Chip tone={STAGE_TONE[p.stage] ?? "muted"}>{stageText(p.stage)}</Chip>
          <span className="biz-r-title">{name}</span>
        </p>
        {!armed && (
          <div className="biz-r-a">
            <button ref={recordBtn} type="button" className="btn-ghost small" aria-expanded={recording}
              onClick={() => (recording ? closeRecord() : setRecording(true))}>
              {t("business.people.record")}
            </button>
            <MoreMenu label={t("business.people.more_actions", { name })}>
              {(close) => (
                <button type="button" role="menuitem" className="biz-menu-i bad" onClick={() => { close(); setRecording(false); setArmed(true); }}>
                  {t("business.people.erase")}
                </button>
              )}
            </MoreMenu>
          </div>
        )}
      </div>
      <p className="biz-sub">
        {sourceText(p.source)} · {platformText(p.platform)}
        {found && <> · <a href={found} target="_blank" rel="noopener noreferrer">{t("business.people.found_post")}</a></>}
        {p.why && <> · {p.why}</>}
        {p.heard_via && p.heard_row_label && <> · {t("business-tracking.heard.on_row", { label: p.heard_row_label })}</>}
      </p>
      {first && <p className="biz-quote">“{first.text}”</p>}
      {latest && <p className="biz-quote"><span className="k">{t("business.people.latest_quote")}</span> “{latest.text}”</p>}
      <p className="biz-lead">
        {p.next_action ? <><span className="k">{t("business.people.next")}</span> {p.next_action}{due && ` · ${due}`}</>
          : t(p.stage === "dropped" ? "business.people.dropped" : "business.people.no_next")}
      </p>
      {recording && <RecordForm p={p} name={name} heardChoices={choices} close={closeRecord} saved={reload} />}
      {armed && (
        <ConfirmBox confirmLabel={t("business.people.erase")} busy={d.busy} error={d.error} tone="danger" onConfirm={erase} onCancel={() => setArmed(false)}>
          <p>{t("business.people.erase_confirm", { name })}</p>
          <p className="muted">{t("business.people.erase_note")}</p>
        </ConfirmBox>
      )}
    </li>
  );
}

function AddForm({ businessId, platforms, done }: { businessId: number; platforms: string[]; done: () => void }) {
  const d = useDo();
  const blank = { handle: "", platform: platforms[0] ?? "direct", source: "warm", url: "", quote: "", why: "" };
  const [f, setF] = useState(blank);
  const [sent, setSent] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    setF({ ...f, [k]: e.target.value });
    setSent(null);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body = { ...f, url: f.url.trim() || null, quote: f.quote.trim() || null, why: f.why.trim() || null };
    if (await d.run(() => post(`/businesses/${businessId}/people`, body))) {
      setSent(f.handle.trim());
      setF(blank);
      done();
    }
  };
  const optional = <span className="muted">{t("business.people.optional")}</span>;
  return (
    <form className="biz-form" onSubmit={submit} aria-label={t("business.people.add_label")}>
      <div className="biz-fields">
        <div className="field"><label htmlFor="p-handle">{t("business.people.handle")}</label>
          <input id="p-handle" required maxLength={100} value={f.handle} onChange={set("handle")} autoComplete="off" /></div>
        <div className="field"><label htmlFor="p-platform">{t("business.people.platform")}</label>
          <select id="p-platform" value={f.platform} onChange={set("platform")}>{platforms.map((k) => <option key={k} value={k}>{platformText(k)}</option>)}</select></div>
      </div>
      <div className="field"><label htmlFor="p-source">{t("business.people.source")}</label>
        <select id="p-source" value={f.source} onChange={set("source")}>{SOURCES.map((k) => <option key={k} value={k}>{sourceText(k)}</option>)}</select></div>
      <div className="field"><label htmlFor="p-url">{t("business.people.url")} {optional}</label>
        <input id="p-url" type="url" maxLength={500} value={f.url} onChange={set("url")} placeholder={t("business.people.url_placeholder")} /></div>
      <div className="field"><label htmlFor="p-quote">{t("business.people.quote")} <span className="muted">{t("business.people.optional_max", { n: MAX_QUOTE })}</span></label>
        <textarea id="p-quote" rows={2} maxLength={MAX_QUOTE} value={f.quote} onChange={set("quote")} /></div>
      <div className="field"><label htmlFor="p-why">{t("business.people.why")} <span className="muted">{t("business.people.optional_max", { n: MAX_WHY })}</span></label>
        <input id="p-why" maxLength={MAX_WHY} value={f.why} onChange={set("why")} /></div>
      <Fail text={d.error} />
      <div className="row wrap">
        <button className="btn-primary" type="submit" disabled={d.busy || !f.handle.trim()}>{t(d.busy ? "business.people.adding" : "business.people.add")}</button>
        {sent && <span className="act-msg ok" role="status">{t("business.people.added", { name: sent })}</span>}
      </div>
      <p className="biz-note-s">{t("business.people.privacy")}</p>
    </form>
  );
}

/** The list by stage, the owner's own additions, and the one thing the owner may erase. */
export function PeopleTab({ businessId, people, reload }: { businessId: number; people: People; reload: () => void }) {
  const [params, setParams] = useSearchParams();
  const [shown, setShown] = useState(PAGE);
  const stage = params.get("stage");
  const pick = (s: string | null) => {
    const next = new URLSearchParams(params);
    if (s) next.set("stage", s);
    else next.delete("stage");
    setParams(next, { replace: true });
    setShown(PAGE);
  };
  const items = stage ? people.items.filter((p) => p.stage === stage) : people.items;
  const sources = SOURCES.filter((k) => people.by_source[k]);
  return (
    <div className="cols cols-main">
      <Section title={t("term.people")} count={people.total}>
        {people.total > 0 && (
          <div className="toolbar">
            <div className="seg" role="group" aria-label={t("business.people.filter")}>
              <button type="button" className={!stage ? "on" : ""} aria-pressed={!stage} onClick={() => pick(null)}>
                {t("business.people.all")} <span className="tab-n">{people.total}</span>
              </button>
              {STAGES.filter((k) => k !== "dropped" || people.by_stage[k]).map((k) => (
                <button key={k} type="button" className={stage === k ? "on" : ""} aria-pressed={stage === k} onClick={() => pick(k)}>
                  {stageText(k)} <span className="tab-n">{people.by_stage[k] ?? 0}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {items.length ? (
          <Box pad={false}>
            <ul className="biz-rows">{items.slice(0, shown).map((p) => <PersonRow key={p.id} p={p} choices={people.heard_choices ?? null} reload={reload} />)}</ul>
            {items.length > shown && (
              <div className="box-foot"><button className="link" onClick={() => setShown(shown + PAGE)}>{t("business.people.more", { n: items.length - shown })}</button></div>
            )}
          </Box>
        ) : people.total ? (
          <Hint title={t("business.people.none_here")} action={<button className="btn-ghost" onClick={() => pick(null)}>{t("business.people.show_all")}</button>} />
        ) : (
          <Hint title={t("business.people.empty")}>
            {t("business.people.empty_body", { steps: LADDER.map(stageText).join(" → ") })}
          </Hint>
        )}
      </Section>
      <div>
        <Section title={t("business.people.add_title")}>
          <Box><AddForm businessId={businessId} platforms={people.places.platforms} done={reload} /></Box>
        </Section>
        <Section title={t("business.people.places")}>
          <Box>
            {!people.places.searched.length && !people.places.by_hand.length && (
              <p className="biz-note-s">{t("business.people.no_places")} <CustomersLink>{t("business-profile.go")}</CustomersLink></p>
            )}
            {!!people.places.searched.length && (
              <p className="biz-note-s">{t("business.people.searched", { places: people.places.searched.join(", ") })}</p>
            )}
            {!!people.places.by_hand.length && (
              <p className="biz-note-s">{t("business.people.by_hand", { places: people.places.by_hand.join(", ") })}</p>
            )}
          </Box>
        </Section>
        {!!sources.length && (
          <Section title={t("business.people.sources")}>
            <Box pad={false}>
              <ul className="ilist">
                {sources.map((k) => (
                  <li key={k}><span className="grow">{sourceText(k)}</span><span className="end biz-end-v">{t("business.people.count", { n: people.by_source[k] })}</span></li>
                ))}
              </ul>
            </Box>
          </Section>
        )}
      </div>
    </div>
  );
}
