import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { post } from "../api";
import { AttachButton, AttachList, useAttachments, useDrop } from "../components/attach";
import { Strengths, StrengthsPicker } from "../components/strengths";
import { ActionButton, announce } from "../components/ui";
import { locale, t } from "../i18n";
import { ProvBadge } from "./Brief";
import { loadDraft, saveDraft } from "./draft";
import { LanguageHint } from "./LanguageHint";
import { ANSWER_LIMIT, BusinessInfo, COUNTER_FROM, isClosing, KickoffView, Stage, Turn } from "./types";

const IDLE_MS = 20_000;
const SHORT_ANSWER = 15;

type Act = (path: string, body?: unknown) => Promise<unknown>;

/** The owner hesitated if the last answer was "모르겠어요" or a few words, so examples are offered at once. */
function hesitated(turns: Turn[], open: Turn): boolean {
  const prev = [...turns].reverse().find((t) => t.n < open.n && t.answered_at && !t.asks);
  if (!prev) return false;
  return prev.owner_choice === "unknown" || (!!prev.owner_text && prev.owner_text.trim().length < SHORT_ANSWER);
}

function Counter({ length }: { length: number }) {
  const over = length - ANSWER_LIMIT;
  const n = (v: number) => v.toLocaleString(locale());
  const announce = over > 0
    ? t("kickoff.dock.over_say", { limit: n(ANSWER_LIMIT) })
    : length >= COUNTER_FROM ? t("kickoff.dock.near_say", { from: n(COUNTER_FROM), limit: n(ANSWER_LIMIT) }) : "";
  return (
    <>
      <span id="kick-count" className={`counter small ${over > 0 ? "bad-t" : "muted"}`}>
        {length >= COUNTER_FROM && (over > 0
          ? t("kickoff.dock.over", { n: n(over), limit: n(ANSWER_LIMIT) })
          : t("kickoff.count", { length: n(length), limit: n(ANSWER_LIMIT) }))}
      </span>
      <span className="sr-only" role="status">{announce}</span>
    </>
  );
}

/** Each line under its section's title in the console's language, as `Conversation` titles a turn. */
function Promote({ turn, view }: { turn: Turn; view: KickoffView }) {
  const lines = turn.promote ?? [];
  const titles = new Map((view.brief?.sections ?? []).map((s) => [s.key, s.title]));
  if (!lines.length && !isClosing(turn)) return null;
  return (
    <section className="promote" aria-label={t("kickoff.dock.promote")}>
      <p className="small muted">{t("kickoff.dock.promote_lead")}</p>
      {lines.length ? (
        <ul className="promote-l">
          {lines.map((l) => (
            <li key={l.id}>
              <ProvBadge provenance="guess" />
              <span><span className="strong">{titles.get(l.section) ?? l.title}</span><span className="muted"> · </span>{l.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small">{t("kickoff.dock.promote_none")}</p>
      )}
    </section>
  );
}

function Answering({ view, turn, mode, act, opening }: {
  view: KickoffView; turn: Turn; mode: "question" | "check" | "closing"; act: Act; opening: string;
}) {
  const sid = view.session.id;
  const [text, setText] = useState(() => loadDraft(sid, turn.n));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chipsOn, setChipsOn] = useState(() => hesitated(view.turns, turn));
  const [fixing, setFixing] = useState(false);
  const files = useAttachments(view.session.business_id, (view.documents ?? []).filter((d) => d.after_turn === turn.n && !d.removed));
  const drop = useDrop(files);
  const box = useRef<HTMLTextAreaElement>(null);
  const idle = useRef<number | undefined>(undefined);

  const arm = (value: string) => {
    window.clearTimeout(idle.current);
    if (turn.chips.length && value.trim().length < SHORT_ANSWER) idle.current = window.setTimeout(() => setChipsOn(true), IDLE_MS);
  };
  useEffect(() => {
    const reading = document.activeElement?.closest(".kick-brief, .kbar");
    if (!reading) box.current?.focus({ preventScroll: true });
    arm(text);
    return () => window.clearTimeout(idle.current);
  }, []);

  async function run(body: unknown, { path = "/answer", keepDraft = false } = {}) {
    setBusy(true);
    setError(null);
    try {
      await act(path, body);
      if (!keepDraft) saveDraft(sid, turn.n, "");
      document.getElementById("kick-log")?.focus({ preventScroll: true });
    } catch (e: any) {
      setError(e.message ?? String(e));
      box.current?.focus({ preventScroll: true });
    } finally {
      setBusy(false);
    }
  }

  const trimmed = text.trim();
  const tooLong = text.length > ANSWER_LIMIT;
  const filesOnly = !trimmed && files.kept.length > 0;
  const sendable = (!!trimmed || filesOnly) && !tooLong && !busy && !files.reading;
  const send = (e?: FormEvent) => {
    e?.preventDefault();
    if (!sendable) return;
    run({ action: "answer", ...(trimmed ? { text: trimmed } : {}) });
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) send(e);
  };
  const edit = (value: string) => {
    setText(value);
    saveDraft(sid, turn.n, value);
    arm(value);
  };
  const pickChip = (chip: string) => {
    if (!trimmed) return run({ action: "answer", choice: chip });
    edit(`${text.trimEnd()}
${chip}`);
    box.current?.focus();
  };

  const check = mode !== "question";
  const hasText = trimmed.length > 0;
  const placeholder = t(mode === "closing" ? "kickoff.dock.ph.closing"
    : fixing ? "kickoff.dock.ph.fixing" : check ? "kickoff.dock.ph.check" : "kickoff.dock.ph.answer");
  // before the first answer, the words the owner opened the business with tell their language
  const written = hasText || view.session.turns_used > 1 ? text : opening;

  return (
    <form className={`dock ${drop.over ? "drop-over" : ""}`} onSubmit={send} aria-busy={busy} {...drop.on}>
      {check && <Promote turn={turn} view={view} />}
      {check && (
        <div className="row wrap dock-check">
          <button
            type="button"
            className={hasText ? "" : "primary"}
            disabled={busy || tooLong}
            aria-describedby={hasText ? "kick-yes-hint" : undefined}
            onClick={() => run({ action: "answer", choice: "yes", ...(hasText ? { text: trimmed } : {}) })}
          >
            {hasText ? t("kickoff.dock.yes_and_send") : t("kickoff.dock.yes")}
          </button>
          <button type="button" disabled={busy} onClick={() => { setFixing(true); box.current?.focus(); }}>
            {mode === "closing" ? t("kickoff.dock.fix_closing") : t("kickoff.dock.fix")}
          </button>
          {hasText && <span id="kick-yes-hint" className="small muted">{t("kickoff.dock.yes_hint")}</span>}
        </div>
      )}
      {chipsOn && turn.kind === "question" && turn.chips.length > 0 && !isClosing(turn) && (
        <div className="chips" role="group" aria-label={t("kickoff.dock.chips")}>
          <span className="small muted">{t("kickoff.dock.chips_lead")}</span>
          {turn.chips.map((c) => (
            <button type="button" key={c} className="chip-b" disabled={busy} onClick={() => pickChip(c)}>{c}</button>
          ))}
          <button type="button" className="chip-b other" disabled={busy} onClick={() => box.current?.focus()}>{t("kickoff.dock.other")}</button>
        </div>
      )}
      <AttachList att={files} />
      {filesOnly && <p className="small muted dock-note">{t("attach.files_only")}</p>}
      <label className="sr-only" htmlFor="kick-answer">{t(mode === "question" ? "kickoff.dock.answer_question" : "kickoff.dock.answer_check")}</label>
      <div className="dock-in">
        <textarea
          id="kick-answer"
          ref={box}
          rows={3}
          value={text}
          placeholder={placeholder}
          aria-invalid={tooLong}
          aria-describedby="kick-hint kick-count kick-lang"
          aria-busy={busy}
          readOnly={busy}
          onChange={(e) => edit(e.target.value)}
          onKeyDown={onKey}
        />
        <button type="submit" className={!check || hasText || filesOnly ? "primary" : ""} disabled={!sendable}>
          {busy ? t("kickoff.dock.sending") : t("kickoff.dock.send")}
        </button>
      </div>
      <LanguageHint id="kick-lang" text={written} />
      {error && <div className="error" role="alert">{error}</div>}
      <div className="dock-f">
        <AttachButton att={files} id="kick-files" />
        <span id="kick-hint" className="small muted">{t("kickoff.dock.keys")}</span>
        <Counter length={text.length} />
        <span className="dock-q">
          {mode !== "closing" && (
            <button type="button" className="quiet" disabled={busy} onClick={() => run({ action: "unknown" })}>{t("kickoff.dock.unknown")}</button>
          )}
          <button type="button" className="quiet" disabled={busy} onClick={() => run({ action: "later" }, { keepDraft: true })}>{t("kickoff.dock.later")}</button>
          {mode !== "closing" && view.session.turns_used > 1 && (
            <button type="button" className="quiet" disabled={busy} onClick={() => run({}, { path: "/finish" })}>{t("kickoff.dock.finish")}</button>
          )}
        </span>
      </div>
    </form>
  );
}

/** The server's opening question: what the owner is good at, saved as Settings > About you saves it. The CEO's first
 * question waits for it. Skip leaves it unanswered, and the next business asks again. */
function StrengthsAnswer({ act }: { act: Act }) {
  const [picked, setPicked] = useState<Strengths>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(body: unknown) {
    setBusy(true);
    setError(null);
    try {
      await act("/answer", body);
      document.getElementById("kick-log")?.focus({ preventScroll: true });
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }
  const send = (e: FormEvent) => {
    e.preventDefault();
    if (picked !== null && !busy) run({ action: "answer", strengths: picked });
  };
  return (
    <form className="dock dock-strengths" onSubmit={send} aria-busy={busy}>
      <StrengthsPicker compact value={picked} onChange={setPicked} disabled={busy} legend={t("kickoff.strengths.legend")} />
      {error && <div className="error" role="alert">{error}</div>}
      <div className="dock-f">
        <button type="submit" className="primary" disabled={picked === null || busy}
          aria-describedby={picked === null ? "kick-strengths-why" : undefined}>
          {busy ? t("kickoff.dock.sending") : t("kickoff.strengths.send")}
        </button>
        {picked === null && <span id="kick-strengths-why" className="small muted">{t("kickoff.strengths.pick_first")}</span>}
        <span className="dock-q">
          <button type="button" className="quiet" disabled={busy} onClick={() => run({ action: "unknown" })}>{t("kickoff.strengths.skip")}</button>
          <button type="button" className="quiet" disabled={busy} onClick={() => run({ action: "later" })}>{t("kickoff.dock.later")}</button>
        </span>
      </div>
    </form>
  );
}

const CUSTOMER_LIMIT = 2000;

/** The CEO's last question, answered or skipped. The server then stops sending it, so the form goes away. */
function CustomerAnswer({ act }: { act: Act }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trimmed = text.trim();
  async function run(body: { text: string | null }) {
    setBusy(true);
    setError(null);
    try {
      await act("/customer", body);
      announce(t(body.text === null ? "kickoff.customer.skipped" : "kickoff.customer.sent"));
      document.getElementById("kick-log")?.focus({ preventScroll: true });
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }
  const send = (e?: FormEvent) => {
    e?.preventDefault();
    if (trimmed && !busy) run({ text: trimmed });
  };
  return (
    <form className="dock dock-customer" onSubmit={send} aria-busy={busy}>
      <label className="sr-only" htmlFor="kick-customer">{t("kickoff.customer.answer")}</label>
      <div className="dock-in">
        <textarea id="kick-customer" rows={3} value={text} maxLength={CUSTOMER_LIMIT} placeholder={t("kickoff.customer.ph")}
          aria-describedby="kick-customer-hint" readOnly={busy} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229 && send(e)} />
        <button type="submit" className="primary" disabled={!trimmed || busy}>{busy ? t("kickoff.customer.sending") : t("kickoff.customer.send")}</button>
      </div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="dock-f">
        <span id="kick-customer-hint" className="small muted">{t("kickoff.customer.hint")}</span>
        <span className="dock-q">
          <button type="button" className="quiet" disabled={busy} onClick={() => run({ text: null })}>{t("kickoff.customer.skip")}</button>
        </span>
      </div>
    </form>
  );
}

function Locked({ paused, reading }: { paused: boolean; reading: boolean }) {
  return (
    <div className="dock locked">
      <textarea rows={2} disabled aria-label={t("kickoff.dock.locked")}
        placeholder={t(paused ? "kickoff.dock.locked_paused" : reading ? "kickoff.dock.locked_reading" : "kickoff.dock.locked_ceo")} />
    </div>
  );
}

function Done({ view, business, reload, act }: { view: KickoffView; business: BusinessInfo | null; reload: () => void; act: Act }) {
  const navigate = useNavigate();
  const { brief, approval, coverage, session } = view;
  const started = business?.cycle_id ?? null;
  const approved = !!brief?.approved_at;
  const blocked = !approved && !approval.can_approve;
  const defaults = approved ? [] : approval.empty_defaults;
  const empty = approved ? [] : coverage.empty_optional;
  async function start() {
    if (!brief) throw new Error(t("kickoff.done.no_plan"));
    if (!brief.approved_at) {
      await post(`/briefs/${brief.id}/approve`, {});
      reload();
    }
    // Start opens the first cycle; the CEO's proposal appears on the dashboard, where the owner starts stage 1.
    await post(`/businesses/${session.business_id}/start`, {});
    navigate("/");
  }
  async function carryOn() {
    await act("/continue");
    document.getElementById("kick-log")?.focus({ preventScroll: true });
  }
  return (
    <div className="dock done-box">
      <p>
        <span className="strong">{approved && !started ? t("kickoff.done.approved") : t("kickoff.done.over")}</span>
        <span className="muted">
          {approved && !started ? t("kickoff.done.not_started") : t("kickoff.done.last_look", { n: session.turns_used })}
        </span>
      </p>
      {blocked && (
        <section className="blockers" aria-label={t("kickoff.done.blockers")}>
          <p className="strong">{t("kickoff.done.blockers_title")}</p>
          <ul className="plain">
            {approval.blocked_by.map((title) => <li key={title}>{t("kickoff.done.blocked_by", { title })}</li>)}
          </ul>
          <p className="small muted">{t("kickoff.done.blockers_why")}</p>
          {session.can_continue ? (
            <ActionButton className="primary" run={carryOn}>{t("kickoff.done.carry_on")}</ActionButton>
          ) : (
            <p className="small">{t("kickoff.done.cannot_continue")}</p>
          )}
        </section>
      )}
      {defaults.length > 0 && (
        <details className="defaults">
          <summary><span className="strong">{t("kickoff.done.defaults")}</span><span className="muted">{t("kickoff.done.defaults_n", { n: defaults.length })}</span></summary>
          <ul className="plain">
            {defaults.map((d) => (
              <li key={d.key}><span className="strong">{d.title}</span><span className="muted"> · {d.default}</span></li>
            ))}
          </ul>
        </details>
      )}
      {empty.length > 0 && <p className="small muted">{t("kickoff.done.empty", { titles: empty.join(", ") })}</p>}
      <div className="row wrap">
        {started ? (
          <Link className="btn" to="/">{t("kickoff.done.open_dashboard")}</Link>
        ) : (
          <ActionButton className={blocked ? "" : "primary"} run={start} disabled={!brief || blocked}
            title={blocked ? t("kickoff.done.blocked_title") : undefined}>
            {approved ? t("kickoff.done.start") : t("kickoff.done.approve_start")}
          </ActionButton>
        )}
      </div>
      {!started && <p className="small muted done-next">{t("kickoff.done.after_approve")}</p>}
    </div>
  );
}

export function Dock({ view, stage, act, business, reload }: {
  view: KickoffView; stage: Stage; act: Act; business: BusinessInfo | null; reload: () => void;
}) {
  if (stage.kind === "owner") {
    if (stage.mode === "strengths") return <StrengthsAnswer act={act} />;
    const opening = [business?.name, business?.one_liner, view.session.material].filter(Boolean).join("\n");
    return <Answering key={stage.turn.n} view={view} turn={stage.turn} mode={stage.mode} act={act} opening={opening} />;
  }
  if (stage.kind === "done") {
    const done = <Done view={view} business={business} reload={reload} act={act} />;
    return view.customer_question ? <div className="dock-stack"><CustomerAnswer act={act} />{done}</div> : done;
  }
  if (stage.kind === "abandoned")
    return (
      <div className="dock done">
        <p><span className="strong">{t("kickoff.done.abandoned")}</span><span className="muted">{t("kickoff.done.abandoned_note")}</span></p>
        <span className="row">
          <ActionButton run={() => act("/reopen")}>{t("kickoff.done.reopen")}</ActionButton>
          <Link className="btn" to="/">{t("kickoff.done.home")}</Link>
        </span>
      </div>
    );
  return <Locked paused={stage.kind === "paused"} reading={view.product?.report_state === "reading"} />;
}
