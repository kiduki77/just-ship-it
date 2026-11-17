import { FormEvent, KeyboardEvent, ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, post, useLive } from "../api";
import { Box, Section } from "../components/kit";
import { shortDate, Skeleton } from "../components/ui";
import { locale, t } from "../i18n";
import { ProvBadge } from "../kickoff/Brief";
import { Fail, useBizTabs, useDo } from "./parts";
import { Profile, ProfileChoice, ProfileEdit, ProfileLine, ProfileValue } from "./types";

const TABLES = ["customer_profile_values", "customer_profile_answers", "jobs", "business_briefs"];
const SENTENCE_LIMIT = 400;
const ANSWER_LIMIT = 2000;
const ANCHOR = "customers";

/** A link to the card, from any tab or screen of the business. */
export function CustomersLink({ children }: { children: ReactNode }) {
  const { href } = useBizTabs();
  return <Link to={href("overview", ANCHOR)}>{children}</Link>;
}

/** How the owner just changed a line, kept until the next edit so Undo stays one click away. */
type Done = { line: ProfileLine["line"]; label: string; kind: "right" | "not_right" | "change"; event: number };

const lower = (s: string) => s.toLocaleLowerCase(locale());

function doneText(d: Done): string {
  if (d.kind === "right") return t("business-profile.done.right", { line: d.label });
  if (d.kind === "not_right") return t("business-profile.done.not_right", { line: d.label });
  return t("business-profile.done.change", { line: d.label });
}

/** The value as the owner reads it: a vocabulary label, or their own words. */
const valueText = (v: ProfileValue) => v.label ?? v.sentence ?? "";

/** The picker's options in vocabulary order, then any held code the vocabulary has since retired, so saving never drops it. */
function optionsFor(line: ProfileLine, choices: ProfileChoice[]): ProfileChoice[] {
  const known = new Set(choices.map((c) => c.code));
  const retired = line.values.filter((v) => v.code && !known.has(v.code)).map((v) => ({ code: v.code!, label: v.label ?? v.code! }));
  return [...choices, ...retired];
}

function LineEditor({ line, choices, instead, busy, onSave, onCancel }: {
  line: ProfileLine; choices: ProfileChoice[]; instead: boolean; busy: boolean;
  onSave: (codes: string[], sentence: string) => void; onCancel: () => void;
}) {
  const id = useId();
  const first = useRef<HTMLInputElement>(null);
  const [codes, setCodes] = useState<string[]>(() => line.values.flatMap((v) => (v.code ? [v.code] : [])));
  const [sentence, setSentence] = useState(() => line.values.find((v) => !v.code)?.sentence ?? "");
  useEffect(() => first.current?.focus({ preventScroll: true }), []);
  const options = optionsFor(line, choices);
  const empty = codes.length === 0 && !sentence.trim();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!empty && !busy) onSave(codes, sentence.trim());
  };
  const toggle = (code: string) => setCodes(codes.includes(code) ? codes.filter((c) => c !== code) : [...codes, code]);
  return (
    <form className="biz-pf-edit" onSubmit={submit} aria-busy={busy} onKeyDown={(e: KeyboardEvent) => e.key === "Escape" && onCancel()}>
      {instead && <p className="biz-pf-instead">{t("business-profile.instead")}</p>}
      <fieldset>
        <legend>{line.label}</legend>
        {options.map((c, i) => (
          <label key={c.code} className="biz-pf-pick">
            <input ref={i === 0 ? first : undefined} type="checkbox" checked={codes.includes(c.code)} onChange={() => toggle(c.code)} />
            <span>{c.label}</span>
          </label>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor={`${id}-s`}>{t("business-profile.sentence")}</label>
        <input id={`${id}-s`} type="text" value={sentence} maxLength={SENTENCE_LIMIT} aria-describedby={`${id}-h`} onChange={(e) => setSentence(e.target.value)} />
        <p id={`${id}-h`} className="field-note">{t("business-profile.sentence_hint")}</p>
      </div>
      <div className="biz-pf-acts">
        <button type="submit" className="primary small" disabled={empty || busy} aria-describedby={empty ? `${id}-e` : undefined}>
          {busy ? t("business-profile.saving") : t("business-profile.save")}
        </button>
        <button type="button" className="small" disabled={busy} onClick={onCancel}>{t("business-profile.cancel")}</button>
        {empty && <span id={`${id}-e`} className="small muted">{t("business-profile.nothing_picked")}</span>}
      </div>
    </form>
  );
}

function Row({ line, choices, editing, busy, onRight, onNotRight, onOpen, onSave, onCancel }: {
  line: ProfileLine; choices: ProfileChoice[]; editing: "change" | "instead" | null; busy: boolean;
  onRight: () => void; onNotRight: () => void; onOpen: () => void; onSave: (codes: string[], sentence: string) => void; onCancel: () => void;
}) {
  const labelId = `pf-${line.line}-l`;
  const name = lower(line.label);
  const canRight = line.known && (line.confirmable || line.stale);
  const onlyWords = line.known && line.uses.length === 0;
  const mixed = line.uses.length > 0 && line.values.some((v) => !v.code);
  return (
    <li id={`pf-${line.line}`} className="biz-pf-row" tabIndex={-1} aria-labelledby={labelId}>
      <div className="biz-pf-h">
        <span id={labelId} className="biz-pf-label">{line.label}</span>
        <ProvBadge provenance={line.weakest} />
      </div>
      {line.known ? (
        <>
          <ul className="biz-pf-vals">{line.values.map((v, i) => <li key={`${v.code ?? "s"}-${i}`}>{valueText(v)}</li>)}</ul>
          {mixed && <p className="biz-pf-uses">{t("business-profile.uses", { list: line.uses.join("; ") })}</p>}
          {onlyWords && <p className="biz-pf-uses">{t("business-profile.uses_none")}</p>}
          {line.values.length > 1 && (
            <details className="biz-pf-details">
              <summary>{t("business-profile.details")}<span className="sr-only"> {name}</span></summary>
              <ul>
                {line.values.map((v, i) => (
                  <li key={`${v.code ?? "s"}-${i}`}><ProvBadge provenance={v.provenance} /><span>{valueText(v)}</span></li>
                ))}
              </ul>
            </details>
          )}
          {line.stale && line.stale_since && <p className="biz-pf-stale">{t("business-profile.stale", { date: shortDate(line.stale_since) })}</p>}
        </>
      ) : (
        <p className="biz-pf-uses">{t("business-profile.unknown")}</p>
      )}
      {editing ? (
        <LineEditor line={line} choices={choices} instead={editing === "instead"} busy={busy} onSave={onSave} onCancel={onCancel} />
      ) : (
        <div className="biz-pf-acts">
          {canRight && (
            <button type="button" className="link" disabled={busy} aria-label={t("business-profile.right_for", { line: name })} onClick={onRight}>
              {t("business-profile.right")}
            </button>
          )}
          {line.known && (
            <button type="button" className="link" disabled={busy} aria-label={t("business-profile.not_right_for", { line: name })} onClick={onNotRight}>
              {t("business-profile.not_right")}
            </button>
          )}
          <button id={`pf-edit-${line.line}`} type="button" className="link" disabled={busy}
            aria-label={t(line.known ? "business-profile.change_for" : "business-profile.tell_for", { line: name })} onClick={onOpen}>
            {t(line.known ? "business-profile.change" : "business-profile.tell")}
          </button>
        </div>
      )}
    </li>
  );
}

/** The CEO's question and a box for the answer, for a description that has not been written yet. */
function Ask({ question, busy, onSend }: { question: string; busy: boolean; onSend: (text: string) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const trimmed = text.trim();
  const send = (e?: FormEvent) => {
    e?.preventDefault();
    if (trimmed && !busy) onSend(trimmed);
  };
  return (
    <form className="biz-pf-ask" onSubmit={send} aria-busy={busy}>
      <p className="biz-pf-q">{question}</p>
      <label className="sr-only" htmlFor={id}>{t("business-profile.answer")}</label>
      <textarea id={id} rows={3} value={text} maxLength={ANSWER_LIMIT} placeholder={t("business-profile.answer_ph")} readOnly={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229 && send(e)} />
      <div className="biz-pf-acts">
        <button type="submit" className="primary small" disabled={!trimmed || busy}>{busy ? t("business-profile.sending") : t("business-profile.send")}</button>
      </div>
    </form>
  );
}

/**
 * The customer description: who buys, how they are reached and what the owner already has, one row each with who
 * stands behind it. The owner says a line is right, not right or changed. A server word that opens a line already
 * names its line, so a row prints the line's values and not the server's composed sentence.
 */
export function CustomersCard({ businessId }: { businessId: number }) {
  const base = `/businesses/${businessId}/profile`;
  const live = useLive<Profile>(base, TABLES);
  const [local, setLocal] = useState<Profile | null>(null);
  const [open, setOpen] = useState<{ line: ProfileLine["line"]; mode: "change" | "instead" } | null>(null);
  const [done, setDone] = useState<Done | null>(null);
  const [sent, setSent] = useState(false);
  const { busy, error, run } = useDo();
  // an edit's answer shows at once; the next fetch of the server's own view replaces it
  useEffect(() => setLocal(null), [live.data]);
  const profile = local ?? live.data;

  const refresh = (e: unknown) => {
    if (e instanceof ApiError && e.status === 409) live.reload();
    throw e;
  };
  const focusRow = (line: string) => requestAnimationFrame(() => document.getElementById(`pf-${line}`)?.focus({ preventScroll: true }));
  const focusEdit = (line: string) => requestAnimationFrame(() => document.getElementById(`pf-edit-${line}`)?.focus({ preventScroll: true }));

  /** One edit of a line. Resolves with the server's answer, or null when it was refused (the card shows why). */
  async function edit(line: ProfileLine, body: Record<string, unknown>): Promise<ProfileEdit | null> {
    const out: { v: ProfileEdit | null } = { v: null };
    await run(async () => {
      try {
        out.v = await post<ProfileEdit>(`${base}/${line.line}`, { if_live: line.live_ids, ...body });
      } catch (e) {
        refresh(e);
      }
    });
    if (out.v) setLocal(out.v.profile);
    return out.v;
  }

  async function mark(line: ProfileLine, kind: "right" | "not_right") {
    const out = await edit(line, { action: kind });
    if (!out) return;
    setDone(out.event_id === null ? null : { line: line.line, label: line.label, kind, event: out.event_id });
    if (kind === "not_right") setOpen({ line: line.line, mode: "instead" });
  }

  async function save(line: ProfileLine, codes: string[], sentence: string) {
    // Saving what replaces a value just marked not right is one change to the owner: Undo goes back to before both.
    const first = open?.mode === "instead" && done?.kind === "not_right" && done.line === line.line ? done.event : null;
    const out = await edit(line, { action: "change", codes, ...(sentence ? { sentence } : {}) });
    if (!out) return;
    const event = first ?? out.event_id;
    setDone(event === null ? null : { line: line.line, label: line.label, kind: "change", event });
    setOpen(null);
    focusRow(line.line);
  }

  async function answer(text: string) {
    if (!profile) return;
    const out = await edit(profile.lines[0], { action: "answer", text });
    if (out) setSent(true);
  }

  async function undo() {
    if (!done) return;
    await run(async () => {
      try {
        setLocal(await post<Profile>(`${base}/undo`, { event_id: done.event }));
      } catch (e) {
        refresh(e);
      }
      setDone(null);
      setOpen(null);
    });
  }

  const title = t("business-profile.title");
  if (!profile) {
    return (
      <Section id={ANCHOR} title={title} className="biz-customers">
        {live.error ? (
          <p className="quiet-error" role="alert">{t("business-profile.load_failed")} <button type="button" className="link" onClick={live.reload}>{t("common.retry")}</button></p>
        ) : <Skeleton rows={3} />}
      </Section>
    );
  }
  const reading = profile.reading || (sent && profile.state === "not_written");
  return (
    <Section id={ANCHOR} title={title} className="biz-customers">
      <Box pad={false}>
        <div className="biz-pf-notes">
          <div role="status">
            {done && (
              <p className="biz-pf-done">
                {doneText(done)}{" "}
                <button type="button" className="link" disabled={busy} aria-label={t("business-profile.undo_for", { line: lower(done.label) })} onClick={undo}>
                  {t("business-profile.undo")}
                </button>
              </p>
            )}
          </div>
          {profile.state === "ready" && profile.backfilled && <p>{t("business-profile.backfilled")}</p>}
          {profile.state === "ready" && reading && <p role="status">{t("business-profile.reading")}</p>}
          {live.error && (
            <p role="alert">{t("business-profile.refresh_failed")} <button type="button" className="link" onClick={live.reload}>{t("common.retry")}</button></p>
          )}
        </div>
        {profile.state === "not_written" ? (
          <div className="biz-pf-body">
            <p className="biz-pf-lead">{t("business-profile.not_written")}</p>
            {reading ? <p role="status">{t("business-profile.reading")}</p> : <Ask question={profile.question} busy={busy} onSend={answer} />}
          </div>
        ) : (
          <ul className="biz-pf-rows">
            {profile.lines.map((line) => (
              <Row key={line.line} line={line} choices={profile.choices[line.line] ?? []} busy={busy}
                editing={open?.line === line.line ? open.mode : null}
                onRight={() => mark(line, "right")} onNotRight={() => mark(line, "not_right")}
                onOpen={() => setOpen({ line: line.line, mode: "change" })}
                onSave={(codes, sentence) => save(line, codes, sentence)}
                onCancel={() => { setOpen(null); focusEdit(line.line); }} />
            ))}
          </ul>
        )}
        <Fail text={error} />
      </Box>
    </Section>
  );
}
