import { FormEvent, useEffect, useState } from "react";
import { ItemLink } from "./panel";
import { Link } from "react-router-dom";
import { api, post, Row, useLive } from "../api";
import { lookup, PlainKey, t, tx } from "../i18n";
import { label } from "../labels";
import { Fix } from "./fix";
import { useWorker, WORKER_FIX } from "./frame";
import { Chip, Tone } from "./kit";
import { ActionButton, Badge, ConfirmBox, usd, when } from "./ui";

/*
 * What a project is doing, why it stopped and what the owner can do about it, in one line. The same line sits on the
 * home screen, the project list and the project header, so "why is it stuck" never takes a second screen.
 */

const PHASES = ["planning", "executing", "ceo_review", "deciding", "reporting", "done"];
const PHASE_OF: Record<string, string> = { meeting: "ceo_review" };

/**
 * A failed run the budget keeps from running again: it stored a budget refusal, or the server says the budget lacks the
 * room one more call holds (`budget_short`), whatever error the run stored. "Run again" would only stop again.
 */
export const budgetStop = (p: Row) => p.status === "FAILED" && (/^BudgetExceeded/.test(p.error ?? "") || !!p.budget_short);
/**
 * The project's one state, as the server derives it (engineering.status.project_stage): the run's status, except that
 * a finished run whose build stopped, runs, waits on the owner or is merged but not usable yet is not done. The header,
 * the Projects list and its counts, and the business page all read it.
 */
export const stageOf = (p: Row): string => p.stage ?? p.status;

/** The worker-off sentence for a live project: it "hasn't started" only while no task has run and nothing was spent. */
export const workerDownKey = (p: Row, worked = (p.tasks_done ?? 0) + (p.tasks_active ?? 0) > 0): PlainKey =>
  p.status === "CREATED" && !worked && !((p.spent_usd ?? 0) > 0) ? "work.line.created_down" : "work.line.running_down";
/** A run that finished while a development of the project failed: the project is not done for the owner. */
export const devStopped = (p: Row) => stageOf(p) === "DEV_STOPPED";
/** A run that finished while a delivered build waits on the owner to merge it, have it changed, or throw it away. */
export const devWaiting = (p: Row) => stageOf(p) === "DEV_DELIVERED";
/** Finished for the owner: the run is done and its builds are usable, nothing people open, or dropped. */
export const finished = (p: Row) => ["DONE", "DEV_DROPPED"].includes(stageOf(p));
/** The project's state as one short label, for chips and the header's stage. */
export const stageLabel = (p: Row): string =>
  lookup(`work.stage.${stageOf(p)}`) ?? label(stageOf(p));

/** The server's one state of a project (services/project_state.py). */
export type ProjectState = "archived" | "paused" | "pausing" | "stopped" | "waiting_you" | "not_started" | "running" | "done";

const STATE_WORD: Partial<Record<ProjectState, PlainKey>> = {
  archived: "label.status.ARCHIVED", paused: "label.status.PAUSED", pausing: "work.card.pausing",
  waiting_you: "label.status.WAITING_HUMAN", not_started: "work.state.not_started",
};
const STATE_TONE: Record<ProjectState, Tone> = {
  archived: "muted", paused: "muted", pausing: "warn", stopped: "bad", waiting_you: "warn", not_started: "muted",
  running: "run", done: "ok",
};

/**
 * The word and tone every screen shows for a project: the server's state, except that work the company would move
 * now says the worker's word while no worker runs or the owner stopped the company (`held`, useWorker().label), since
 * nothing moves then. A stopped, running or finished project names its stage ("Build stopped", "Building").
 */
export function projectWord(p: Row, held: string | null): { word: string; tone: Tone } {
  const state = (p.owner_state ?? "running") as ProjectState;
  if (held && p.needs_worker) return { word: held, tone: "warn" };
  const key = STATE_WORD[state];
  return { word: key ? t(key) : stageLabel(p), tone: state === "waiting_you" && p.blocking ? "bad" : STATE_TONE[state] };
}

export function ProjectStateChip({ p }: { p: Row }) {
  const worker = useWorker();
  const { word, tone } = projectWord(p, worker.label);
  return <Chip tone={tone}>{word}</Chip>;
}

/** A cycle stage as a project made for it names it: the cycle's number, the stage's place and title, its state, and
 * the stage card's "what the company does". */
export type StageRef = { id: number; cycleId: number; cycle: number; position: number; title: string; status: string; doing: string | null };

/**
 * Every stage of a business's cycles by id, for the projects a stage made (`stage_id`). Read from the cycle history and
 * each cycle in full (GET /businesses/:id/cycles, /cycles/:id); the stage card's text comes from the latest proposal
 * that has the stage. Null until read; a cycle that fails to load is left out rather than guessed.
 */
export function useStages(businessId: number | null | undefined): Map<number, StageRef> | null {
  const { data: cycles } = useLive<Row[]>(businessId ? `/businesses/${businessId}/cycles` : null, ["cycles", "cycle_stages"]);
  const [stages, setStages] = useState<Map<number, StageRef> | null>(null);
  const key = (cycles ?? []).map((c) => `${c.id}:${c.state}:${c.stages_done}`).join(",");
  useEffect(() => {
    if (!cycles) return;
    let live = true;
    Promise.all(cycles.map((c) => api<Row>(`/cycles/${c.id}`).catch(() => null))).then((details) => {
      if (!live) return;
      const out = new Map<number, StageRef>();
      for (const d of details) {
        if (!d) continue;
        const proposals: Row[] = [...(d.proposals ?? [])].reverse();
        for (const st of d.stages ?? []) {
          const card = proposals.map((p) => (p.stages ?? []).find((x: Row) => x.key === st.key)).find(Boolean);
          out.set(st.id, { id: st.id, cycleId: d.cycle.id, cycle: d.cycle.number, position: st.position, title: st.title, status: st.status, doing: card?.doing ?? null });
        }
      }
      setStages(out);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return stages;
}

/** "Cycle 1 · stage 2: Define", linked to the dashboard where the stage lives. */
export function StageTag({ stage }: { stage: StageRef }) {
  return (
    <Link className="pj-stage" to="/" title={stage.doing ?? stage.title}>
      {t("work.stage_ref", { cycle: stage.cycle, n: stage.position + 1, title: stage.title })}
    </Link>
  );
}

/** A stage on its way out: the project it made stops with it, and that is the dashboard's to say. */
export const stageWrapping = (stage?: StageRef | null) => !!stage && ["closing", "done", "stopped", "skipped"].includes(stage.status);

/** How to use a merged build that is nothing people open (a library, a command-line tool). */
function toolLine(u: Row, prefix: "work.line" | "work.status"): string {
  return u.command
    ? t(`${prefix}.merged_tool_cmd`, { command: u.command, readme: u.readme })
    : t(`${prefix}.merged_tool`, { readme: u.readme });
}
export const toolStatus = (u: Row) => toolLine(u, "work.status");

/**
 * A stopped development in one sentence and the one action, naming the same way on as its card on 개발
 * (engineering.status.stop_kind): a budget to raise, a delivery to repeat, or a build to start again.
 */
export function devStop(p: Row): { text: React.ReactNode; to: string; label: string; budget: boolean } | null {
  const st = p.dev_stop;
  if (!st || !(p.dev_failed?.length)) return null;
  if (st.kind === "budget") {
    return {
      budget: true, to: `/projects/${p.id}/settings#budget`, label: t("work.dev.raise"),
      text: <>{t("work.dev.budget")}{st.short_usd ? <> {t("work.dev.budget_short", { amount: `$${Math.ceil(st.short_usd)}` })}</> : null} {t("work.dev.budget_then")}</>,
    };
  }
  if (st.kind === "deliver") {
    return {
      budget: false, to: `/projects/${p.id}/dev?job=${st.jobs[0]}`, label: t("work.dev.redeliver"),
      text: t("work.dev.deliver", { action: t("work.dev.redeliver") }),
    };
  }
  return {
    budget: false, to: `/projects/${p.id}/dev?job=${st.jobs[0]}`, label: t("work.dev.view"),
    text: t("work.dev.failed"),
  };
}

export function LifecycleStrip({ p }: { p: Row }) {
  // a run not started yet stands on its first step: the stepper always marks where the project is
  const cur = PHASE_OF[p.phase] ?? p.phase ?? "planning";
  // A run that is done while its build is not (running, stopped, waiting, merged but not usable) is on a step of its
  // own before 종료, named by the project's stage, as the header names it.
  const building = p.status === "DONE" && !finished(p);
  const steps = building ? [...PHASES.slice(0, -1), "dev", "done"] : PHASES;
  const idx = building ? steps.indexOf("dev") : steps.indexOf(cur === "archived" ? "done" : cur);
  const ended = p.status === "DONE" && finished(p);
  const bad = p.status === "FAILED" || devStopped(p);
  return (
    <ol className="life" aria-label={t("work.stages")}>
      {steps.map((ph, i) => (
        <li key={ph} className={i < idx || ended ? "past" : i === idx ? `cur ${bad ? "bad" : ""}` : ""}
          aria-current={i === idx ? "step" : undefined}>
          {ph === "dev" ? stageLabel(p) : label(ph === "ceo_review" && p.phase === "meeting" ? "meeting" : ph)}
        </li>
      ))}
    </ol>
  );
}

/** A project whose run the company can still move: started or running, not paused, stopped or done. */
const LIVE = ["CREATED", "RUNNING", "WAITING_HUMAN"];
const isPausing = (p: Row) => !!(p.paused || p.context?.paused) && LIVE.includes(p.status);
/** A finished run that has its final report (an artifact the project's context names). */
export const hasReport = (p: Row) => !!p.context?.report_artifact_id;

/**
 * One sentence: the state and its reason, and the one action that moves it. Decisions waiting on the owner come first
 * whatever the state, then a stop, then that no worker is running (nothing moves however the rows read), then the
 * work. `stage` is the cycle stage the project was made for, when it was.
 */
export function StatusLine({ p, onChange, compact, stage }: { p: Row; onChange?: () => void; compact?: boolean; stage?: StageRef | null }) {
  const worker = useWorker();
  const done = () => onChange?.();
  const pid = p.id;
  const used = p.budget_usd ? (p.spent_usd + (p.reserved_usd ?? 0)) / p.budget_usd : 0;
  // in a list row the page header holds the one primary action; the row's own way on draws quietly
  const lead = compact ? "btn-ghost small" : "btn-primary small";
  const decide = <Link className={lead} to={`/inbox?project=${pid}`}>{t("work.go_decide")}</Link>;
  let tone = "run";
  let text: React.ReactNode;
  let action: React.ReactNode = null;
  if (p.status === "ARCHIVED") {
    tone = "muted";
    text = t("work.line.archived");
  } else if (p.status === "PAUSED") {
    tone = "warn";
    text = p.context?.paused?.reason ? t("work.line.paused_because", { reason: p.context.paused.reason }) : t("work.line.paused");
    action = <ActionButton className={lead} run={() => post(`/projects/${pid}/resume`, {}).then(done)}>{t("work.resume")}</ActionButton>;
  } else if (isPausing(p) && stageWrapping(stage)) {
    // the stage is closing and its project stops with it: undoing the pause would not keep it going
    tone = "warn";
    text = t("work.line.stage_wrapping");
    action = <Link className="btn-ghost small" to="/">{t("work.line.stage_open")}</Link>;
  } else if (isPausing(p)) {
    tone = "warn";
    text = p.context?.paused?.reason ? t("work.line.pausing_because", { reason: p.context.paused.reason }) : t("work.line.pausing");
    action = <ActionButton className="btn-ghost small" run={() => post(`/projects/${pid}/resume`, {}).then(done)}>{t("work.unpause")}</ActionButton>;
  } else if (worker.down && p.needs_worker && !(compact && p.pending > 0)) {
    // the worker comes first, as the header and the chip say it: decisions answered now still wait for it to move
    tone = "warn";
    text = <>{t(workerDownKey(p))} <Fix text={`\`${WORKER_FIX}\`.`} />
      {p.pending > 0 ? <> {p.blocking ? t("work.line.pending_blocking", { n: p.pending, m: p.blocking }) : t("work.line.pending", { n: p.pending })}</> : null}</>;
    action = p.pending > 0 ? decide : null;
  } else if (p.pending > 0) {
    tone = p.blocking ? "bad" : "warn";
    text = <>{p.blocking ? t("work.line.pending_blocking", { n: p.pending, m: p.blocking }) : t("work.line.pending", { n: p.pending })}
      {budgetStop(p) ? <> {t("work.line.budget_stop", { spent: usd(p.spent_usd), budget: usd(p.budget_usd) })}</> : p.status === "FAILED" ? <> {t("work.status.failed")}</> : null}</>;
    action = decide;
  } else if (budgetStop(p)) {
    tone = "bad";
    text = <>{t("work.line.budget_stop", { spent: usd(p.spent_usd), budget: usd(p.budget_usd) })}{p.dev_hold_usd > 0 && <> {t("work.line.dev_hold", { amount: usd(p.dev_hold_usd) })}</>}</>;
    action = <Link className={lead} to={`/projects/${pid}/settings#budget`}>{t("work.budget.raise_resume")}</Link>;
  } else if (p.status === "FAILED") {
    tone = "bad";
    text = tx("work.line.failed", { error: <span className="err-line">{String(p.error ?? "").split("\n")[0]}</span> });
    action = <ActionButton className={lead} run={() => post(`/projects/${pid}/retry`, {}).then(done)}>{t("work.line.retry")}</ActionButton>;
  } else if (devStopped(p)) {
    const stop = devStop(p);
    tone = stop?.budget ? "warn" : "bad";
    text = stop?.text ?? t("work.line.dev_stopped");
    action = <Link className={lead} to={stop?.to ?? `/projects/${pid}/dev`}>{stop?.label ?? t("work.dev.view")}</Link>;
  } else if (stageOf(p) === "DEV_ACTIVE") {
    text = t("work.line.dev_active");
    action = <Link className="btn-ghost small" to={`/projects/${pid}/dev`}>{t("work.dev.view")}</Link>;
  } else if (devWaiting(p)) {
    tone = "warn";
    text = t("work.line.dev_delivered");
    action = <Link className={lead} to={`/projects/${pid}/dev`}>{t("work.dev.view")}</Link>;
  } else if (p.dev_use?.state === "tool") {
    tone = "ok";
    text = toolLine(p.dev_use, "work.line");
    action = <Link className="btn-ghost small" to={`/projects/${pid}/docs`}>{hasReport(p) ? t("work.read_report") : t("work.view_docs")}</Link>;
  } else if (p.dev_use) {
    // a merged build is not a finished project until people can open it, whatever the run's own status
    const u = p.dev_use;
    tone = u.state === "usable" ? "ok" : "warn";
    text = u.state === "usable"
      ? (!u.address ? t("work.line.merged_usable") : t(u.how === "local" ? "work.line.merged_local" : "work.line.merged_live", { address: u.address }))
      : t(u.state === "decide" ? "work.line.merged_decide" : u.state === "todo" ? "work.line.merged_todo"
        : u.state === "later" ? "work.line.merged_later" : "work.line.merged_closed");
    action = u.state === "decide" ? decide
      : u.state === "todo" ? <ItemLink className={lead} to={`/my-tasks?ot=${u.task_id}`}>{t("work.go_todo")}</ItemLink>
      : u.state === "usable" ? <Link className="btn-ghost small" to={`/projects/${pid}/docs`}>{t("work.view_docs")}</Link>
      : <ActionButton className={lead} run={() => post(`/dev-jobs/${u.job_id}/use-again`, {}).then(done)}>{t("work.use_again")}</ActionButton>;
  } else if (p.status === "DONE") {
    tone = "ok";
    text = hasReport(p) ? t("work.line.done") : t("work.line.done_no_report");
    action = <Link className="btn-ghost small" to={`/projects/${pid}/docs`}>{hasReport(p) ? t("work.read_report") : t("work.view_docs")}</Link>;
  } else if (p.status === "CREATED") {
    tone = "muted";
    text = t("work.line.created");
  } else {
    text = <>{label(p.phase ?? "planning")}{p.tasks_total ? t("work.line.tasks", { done: p.tasks_done, total: p.tasks_total }) : ""}{p.tasks_active ? t("work.line.active", { n: p.tasks_active }) : ""}</>;
  }
  return (
    <div className={`sline ${tone} ${compact ? "compact" : ""}`}>
      <span className={`dot ${tone}`} aria-hidden="true" />
      <span className="sline-t">{text}</span>
      {used >= 0.8 && !budgetStop(p) && p.status !== "DONE" && p.status !== "ARCHIVED" && (
        <Badge value={t("work.line.budget_pct", { pct: Math.round(used * 100) })} tone={used >= 1 ? "bad" : "warn"} raw />
      )}
      {action && <span className="sline-a">{action}</span>}
    </div>
  );
}

/** Pause / resume / retry, as one control that fits the current state. `quiet` when the header already has its one
 * primary action, so this one draws as a secondary button. */
export function RunControls({ p, onChange, quiet }: { p: Row; onChange: () => void; quiet?: boolean }) {
  const pid = p.id;
  const [reason, setReason] = useState("");
  const [asking, setAsking] = useState(false);
  const main = quiet ? "btn-ghost" : "btn-primary";
  if (p.status === "PAUSED") return <ActionButton className={main} run={() => post(`/projects/${pid}/resume`, {}).then(onChange)}>{t("work.resume")}</ActionButton>;
  // undoing a pause is never the page's main action: the pause the owner asked for is the default
  if (isPausing(p)) return <ActionButton className="btn-ghost" run={() => post(`/projects/${pid}/resume`, {}).then(onChange)}>{t("work.unpause")}</ActionButton>;
  if (p.status === "FAILED" && !budgetStop(p))
    return <ActionButton className={main} run={() => post(`/projects/${pid}/retry`, {}).then(onChange)}>{t("work.run.again")}</ActionButton>;
  if (!LIVE.includes(p.status)) return null;
  if (!asking) return <button type="button" className="btn-ghost" onClick={() => setAsking(true)}>{t("work.run.pause_ask")}</button>;
  return (
    <span className="row tight wrap">
      <label className="sr-only" htmlFor={`pause-${pid}`}>{t("work.run.pause_why")}</label>
      <input id={`pause-${pid}`} autoFocus placeholder={t("work.run.pause_placeholder")} value={reason} onChange={(e) => setReason(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), setAsking(false))} />
      <ActionButton className="btn-primary" run={() => post(`/projects/${pid}/pause`, { reason: reason || null }).then(() => { setAsking(false); onChange(); })}>
        {t("work.run.pause")}
      </ActionButton>
      <button type="button" className="btn-ghost" onClick={() => setAsking(false)}>{t("work.run.pause_keep")}</button>
    </span>
  );
}

/** A new budget this many times the current one asks once more before it is saved (a typo of an extra zero). */
const BIG_RAISE = 10;

/** `ask`: the open decision that adds to this budget. The form starts at the same total, so the two ways agree; with
 * none it starts at the budget as it is, and Save waits for a change, so a stray click raises nothing.
 * `readOnly`: an archived project keeps its budget as it was; the meter and history stay readable. */
export function BudgetForm({ p, ask, onChange, readOnly }: { p: Row; ask?: Row; onChange: () => void; readOnly?: boolean }) {
  const stop = budgetStop(p);
  const [value, setValue] = useState(String(ask ? Math.ceil(p.budget_usd + ask.estimated_cost_usd) : p.budget_usd));
  const [note, setNote] = useState("");
  const [resume, setResume] = useState(stop);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const n = Number(value);
  const same = value.trim() !== "" && n === p.budget_usd;
  const invalid = !same && (!value.trim() || !Number.isFinite(n) || n <= p.spent_usd + (p.reserved_usd ?? 0));
  const big = p.budget_usd > 0 && n >= p.budget_usd * BIG_RAISE;
  async function save() {
    setBusy(true);
    try {
      const saved = await post<Row>(`/projects/${p.id}/budget`, { budget_usd: n, note: note || null, resume });
      const restart: string | undefined = saved.context?.budget_changes?.at(-1)?.resume;
      setMsg({ ok: true, text: restart ? t("work.budget.saved_then", { restart }) : t("work.budget.saved") });
      setNote("");
      setChecking(false);
      onChange();
    } catch (err: any) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    if (same || invalid || readOnly) return;
    if (big) setChecking(true);
    else save();
  }
  const changes: Row[] = p.context?.budget_changes ?? [];
  return (
    <form className="form" onSubmit={submit} id="budget">
      {ask && !readOnly && (
        <p className="small" role="note">
          {tx("work.budget.ask", { amount: usd(ask.estimated_cost_usd), link: <ItemLink to={`/inbox?req=${ask.id}`}>{t("work.budget.ask_link")}</ItemLink> })}
        </p>
      )}
      <div className="meter-row">
        <div className="meter" role="img" aria-label={t("work.budget.meter", { spent: usd(p.spent_usd), budget: usd(p.budget_usd) })}>
          <span style={{ width: `${Math.min(100, (100 * p.spent_usd) / Math.max(p.budget_usd, 0.01))}%` }}
            className={p.spent_usd >= p.budget_usd ? "bad" : p.spent_usd >= p.budget_usd * 0.8 ? "warn" : ""} />
        </div>
        <span className="small">{t("work.budget.used", { spent: usd(p.spent_usd), budget: usd(p.budget_usd) })}{p.reserved_usd > 0 && t("work.budget.reserved", { amount: usd(p.reserved_usd) })}</span>
      </div>
      {readOnly ? <p className="small muted">{t("work.budget.archived")}</p> : (
        <>
          <div className="row wrap">
            <div className="field">
              <label htmlFor={`b-${p.id}`}>{t("work.budget.new")}</label>
              <input id={`b-${p.id}`} type="number" min={0} step="1" inputMode="decimal" value={value} onChange={(e) => { setValue(e.target.value); setChecking(false); }}
                aria-invalid={invalid} aria-describedby={`b-${p.id}-h`} />
            </div>
            <div className="field grow">
              <label htmlFor={`bn-${p.id}`}>{t("work.budget.note")} <span className="muted">{t("work.optional")}</span></label>
              <input id={`bn-${p.id}`} value={note} onChange={(e) => setNote(e.target.value)} placeholder={t("work.budget.note_placeholder")} />
            </div>
          </div>
          <p id={`b-${p.id}-h`} className={`small ${invalid ? "bad-t" : "muted"}`}>
            {invalid ? t("work.budget.too_low", { amount: usd(p.spent_usd + (p.reserved_usd ?? 0)) }) : t("work.budget.help")}
          </p>
          {stop && (
            <label className="row small">
              <input type="checkbox" checked={resume} onChange={(e) => setResume(e.target.checked)} />
              {t("work.budget.resume")}
            </label>
          )}
          {checking ? (
            <ConfirmBox confirmLabel={t("work.budget.big_confirm", { to: usd(n) })} cancelLabel={t("work.budget.big_keep")} busy={busy} error={msg && !msg.ok ? msg.text : null}
              onConfirm={save} onCancel={() => setChecking(false)}>
              {t("work.budget.big", { from: usd(p.budget_usd), to: usd(n), x: Math.floor(n / p.budget_usd) })}
            </ConfirmBox>
          ) : (
            <div className="row wrap">
              <button type="submit" className={same ? "btn-ghost" : "btn-primary"} disabled={busy || invalid || same}>{busy ? t("work.saving") : stop && resume ? t("work.budget.raise_resume") : t("work.budget.save")}</button>
              {msg && <span className={`act-msg ${msg.ok ? "ok" : "bad"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</span>}
            </div>
          )}
        </>
      )}
      {changes.length > 0 && (
        <details>
          <summary className="small">{t("work.budget.history", { n: changes.length })}</summary>
          <ul className="plain small">
            {[...changes].reverse().map((c, i) => (
              <li key={i}>{usd(c.from)} → {usd(c.to)} · {when(c.at)}{c.note && ` · ${c.note}`}{c.resume && ` · ${c.resume}`}</li>
            ))}
          </ul>
        </details>
      )}
    </form>
  );
}

/** A standalone instruction to the whole team, not tied to any request. */
export function DirectiveForm({ p, onChange }: { p: Row; onChange: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const live = ["RUNNING", "WAITING_HUMAN", "PAUSED"].includes(p.status);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try {
      const r = await post<Row>(`/projects/${p.id}/directives`, { text });
      setMsg({ ok: true, text: r.realigning ? t("work.directive.saved_realign", { n: r.n }) : t("work.directive.saved", { n: r.n }) });
      setText("");
      onChange();
    } catch (err: any) {
      setMsg({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form" onSubmit={submit}>
      <div className="field">
        <label htmlFor={`d-${p.id}`}>{t("work.directive.label")}</label>
        <textarea id={`d-${p.id}`} rows={3} value={text} onChange={(e) => setText(e.target.value)}
          placeholder={t("work.directive.placeholder")}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(e as unknown as FormEvent); }} />
      </div>
      <p className="small muted">
        {t("work.directive.reach")} {live ? t("work.directive.live") : t("work.directive.idle")}
      </p>
      <div className="row wrap">
        <button type="submit" className="btn-primary" disabled={busy || !text.trim()}>{busy ? t("work.directive.saving") : t("work.directive.send")}</button>
        <span className="small muted">Ctrl+Enter</span>
        {msg && <span className={`act-msg ${msg.ok ? "ok" : "bad"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</span>}
      </div>
    </form>
  );
}
