import { Fragment, ReactNode, useId } from "react";
import { Link } from "react-router-dom";
import type { Row } from "../api";
import { lookup, t } from "../i18n";
import { agentName, label } from "../labels";
import { Icon } from "./icons";
import { ItemLink } from "./panel";
import { AfterLine, CardContext, CardContextField, contextFailed, HeldTask, KNOWN, PathRef, PathStep, say, StepKind, worded } from "./pathContext";
import { ago, evidenceHref, refHref, shortDate, when } from "./ui";
import "./path.css";

const STEP_ICON: Record<StepKind, string> = {
  goal: "flag", cycle: "routines", stage: "board", experiment: "idea", project: "projects", task: "tasks", round: "agents",
  decision: "file", chat: "chat", server: "settings", call: "bolt", origin: "file", this: "inbox",
};

export type Tone = "ok" | "run" | "warn" | "bad" | "off" | "idle";
const TONES: Record<string, Tone> = {
  DONE: "ok", done: "ok", APPROVED: "ok", AUTO_APPROVED: "ok", ANSWERED: "ok",
  RUNNING: "run", running: "run", REVIEW: "run", REWORK: "run", closing: "run",
  PENDING: "warn", WAITING_HUMAN: "warn", awaiting_owner: "warn", APPROVAL_REQUIRED: "warn", DEFERRED: "warn",
  FAILED: "bad", REJECTED: "bad", EXPIRED: "bad",
  CANCELLED: "off", REPLACED: "off", STOPPED: "off", stopped: "off", skipped: "off", DROPPED: "off", closed: "off",
};
export const TONE_ICON: Record<Tone, string> = { ok: "check", run: "play", warn: "clock", bad: "alert", off: "stop", idle: "pause" };

/** A record's status as an icon and a word, never colour alone. */
export function stateView(kind: StepKind, state: string): { tone: Tone; word: string } {
  const word = kind === "this" && state === "PENDING" ? t("path.state.pending")
    : (kind === "stage" || kind === "cycle" ? lookup(`cycle.status.${state}`) ?? lookup(`cycle.state.${state}`) : undefined) ?? label(state);
  return { tone: TONES[state] ?? "idle", word };
}

export function State({ kind, state }: { kind: StepKind; state: string | null }) {
  if (!state) return null;
  const v = stateView(kind, state);
  return <span className={`ws-state ws-${v.tone}`}><Icon name={TONE_ICON[v.tone]} size={12} />{v.word}</span>;
}

const CODE = /^(REQ|T|P|DEC)-(\d+)$/;

function StepItem({ s, projectId }: { s: PathStep; projectId: number | null }) {
  const unknown = s.known === "unknown";
  const text = unknown ? t("path.unknown") : worded(s.label_key, s.vars);
  const m = s.ref ? CODE.exec(s.ref) : null;
  const href = !unknown && s.kind !== "this" && m ? refHref(m[1], m[2], projectId) : null;
  const tag = s.known === "matched" ? t("path.known.matched") : s.known === "backfilled" ? t("path.known.backfilled") : null;
  return (
    <li className={`ws-step ws-k-${s.kind}${unknown ? " ws-unk" : ""}`} aria-current={s.kind === "this" ? "step" : undefined}>
      <Icon name={STEP_ICON[s.kind]} size={14} />
      {href ? <ItemLink to={href}>{text}</ItemLink> : <span>{text}</span>}
      {!unknown && <State kind={s.kind} state={s.state} />}
      {tag && <span className="ws-tag">{tag}</span>}
    </li>
  );
}

function Steps({ steps, projectId }: { steps: PathStep[]; projectId: number | null }) {
  return <>{steps.map((s, i) => (
    <Fragment key={i}>
      {i > 0 && <li className="ws-sep" aria-hidden="true"><Icon name="chevron" size={12} /></li>}
      <StepItem s={s} projectId={projectId} />
    </Fragment>
  ))}</>;
}

function RefChip({ r, projectId }: { r: PathRef; projectId: number | null }) {
  const code = r.kind === "evidence" ? `EV-${r.id}` : r.kind === "task" ? `T-${r.id}` : `REQ-${r.id}`;
  const href = r.kind === "evidence" ? evidenceHref(r.id, projectId) : r.kind === "task" ? refHref("T", r.id, projectId) : refHref("REQ", r.id);
  return href ? <ItemLink to={href} title={r.label ?? undefined}>{code}</ItemLink> : <span title={r.label ?? undefined}>{code}</span>;
}

const joined = (items: ReactNode[]) => items.flatMap((x, i) => (i ? [", ", x] : [x]));

function TaskLine({ task, projectId }: { task: HeldTask; projectId: number }) {
  return (
    <li className={task.live ? undefined : "ws-ended"}>
      <ItemLink to={refHref("T", task.id, projectId) ?? "#"}>{task.title}</ItemLink>{" "}
      <State kind="task" state={task.live ? null : task.state} />
      {task.live && <span className="muted small">{t(task.phase === "start" ? "path.phase.start" : "path.phase.finish")}</span>}
    </li>
  );
}

const SHOWN_TASKS = 4;

function HoldsRow({ c, projectId }: { c: CardContext; projectId: number | null }) {
  const h = c.holds!;
  // A card that holds tasks or the next plan step keeps the whole project waiting only while nothing else can start.
  const parks = c.parks_project && <p className="ws-p">{t("path.holds.stalled")}</p>;
  if (h.scope === "none") return <p className="ws-p">{t("path.holds.none")}</p>;
  if (h.scope === "run") return <p className="ws-p">{t("path.holds.run")}</p>;
  if (h.scope === "planning") return <><p className="ws-p">{t("path.holds.planning")}</p>{parks}</>;
  if (h.nothing_live) {
    const ended = h.tasks.map((x) => x.ended_on).filter(Boolean).sort().pop();
    return (
      <>
        <p className="ws-p">{ended ? t("path.holds.moot", { when: shortDate(ended) }) : t("path.holds.none")}</p>
        {h.tasks.length > 0 && projectId != null && <ul className="ws-tasks">{h.tasks.map((x) => <TaskLine key={`${x.id}${x.phase}`} task={x} projectId={projectId} />)}</ul>}
      </>
    );
  }
  const shown = h.tasks.slice(0, SHOWN_TASKS);
  return (
    <>
      <p className="ws-p">{t("path.holds.tasks")}</p>
      {projectId != null && <ul className="ws-tasks">{shown.map((x) => <TaskLine key={`${x.id}${x.phase}`} task={x} projectId={projectId} />)}</ul>}
      {h.tasks.length > shown.length && <p className="muted small">{t("path.holds.more", { n: h.tasks.length - shown.length })}</p>}
      {h.behind > 0 && <p className="muted small">{t("path.holds.behind", { n: h.behind })}</p>}
      {parks}
    </>
  );
}

type Choices = { key: string; label: string }[];

/** The words for an option the card stored under its own key: the label the owner reads on the button. */
const optionName = (key: string | null, choices: Choices) => (key == null ? null : choices.find((x) => x.key === key)?.label ?? key);

/** One line of what answering does (declared by the agent) or what it did (followed). */
function AfterItem({ line, choices, decided, projectId }: { line: AfterLine; choices: Choices; decided: boolean; projectId: number | null }) {
  const key = `path.${decided ? "happened" : "after"}.${line.effect}`;
  const text = line.effect && KNOWN.has(key) ? say(key) : t("path.happened.other");
  const option = optionName(line.option_key, choices);
  return (
    <li>
      {option && <b>{option}: </b>}
      {text}
      {line.targets.length > 0 && <> {joined(line.targets.map((x) => <RefChip key={x.id} r={x} projectId={projectId} />))}</>}
      {line.at && <span className="muted small"> · {ago(line.at)}</span>}
    </li>
  );
}

function LapseRow({ c, choices }: { c: CardContext; choices: Choices }) {
  const u = c.unanswered!;
  const at = u.when ? when(u.when) : t("path.lapse.no_date");
  if (u.policy === "default") return <>{t("path.lapse.default", { when: at, option: optionName(u.default_option, choices) ?? "" })}</>;
  if (u.policy === "close") return <>{t(u.affects.length ? "path.lapse.close" : "path.lapse.close_plain", { when: at })}</>;
  if (u.policy === "keep_waiting") return <>{t("path.lapse.keep_waiting")}</>;
  return <>{t("path.lapse.unknown")}</>;
}

function Stale({ c }: { c: CardContext }) {
  const first = c.why.changed[0];
  const base = { ago: ago(c.why.at), agent: agentName(c.why.agent) };
  return (
    <p className="ws-stale" role="note">
      <Icon name="alert" size={12} />
      {first.kind === "task" ? t("path.stale.task", { ...base, title: first.label ?? `T-${first.id}` }) : t("path.stale.round", base)}
    </p>
  );
}

function Fact({ title, children }: { title: string; children: ReactNode }) {
  return <div><dt>{title}</dt><dd>{children}</dd></div>;
}

function Note({ c }: { c: CardContext }) {
  return <p className="ws-note">“{c.why.note}” <span>{agentName(c.why.agent)} · {ago(c.why.at)}</span></p>;
}

const NOTE_INLINE = 90;

/**
 * Where a decision sits in the work: why it is asked, the chain from the goal to this question, what it holds up, what
 * answering does and what happens if nobody answers, from what the server recorded. What it did not record reads "Not
 * recorded". "Show on the path" opens the project's Path tab on this decision's box, with this card still open beside it.
 */
export function WhereSits({ r, onRetry }: { r: Row; onRetry: (() => void) | null }) {
  const c = r.context as CardContextField;
  const hid = useId();
  const heading = <h3 className="ws-h" id={hid}>{t("path.block.title")}</h3>;
  if (!c) {
    return (
      <section className="ws" aria-busy="true" aria-labelledby={hid}>
        {heading}
        <div className="ws-skel"><p className="skel-bar" /><p className="skel-bar short" /><p className="skel-bar" /></div>
      </section>
    );
  }
  if (contextFailed(c)) {
    return (
      <section className="ws" aria-labelledby={hid}>
        {heading}
        <p className="ws-p" role="alert">
          {t("path.error")}{onRetry && <> <button type="button" className="link small" onClick={onRetry}>{t("common.retry")}</button></>}
        </p>
      </section>
    );
  }
  const projectId: number | null = r.project_id ?? null;
  const choices: Choices = r.report?.choices ?? [];
  const noteLong = (c.why.note?.length ?? 0) > NOTE_INLINE;
  // What approving does in the server's words is the report's own row above; this row adds what the agent declared.
  const declared = c.after.filter((x) => x.source === "declared" && x.effect && KNOWN.has(`path.after.${x.effect}`));
  const unknownStep = c.chain.some((x) => x.known === "unknown");
  const hidden = c.holds && c.holds.scope === "tasks" && !c.holds.nothing_live ? c.holds.tasks.slice(SHOWN_TASKS) : [];
  const detailed = noteLong || c.also_asked.length > 0 || c.why.refs.some((x) => x.label) || hidden.length > 0;
  return (
    <section className="ws" aria-labelledby={hid}>
      {heading}
      <dl className="ws-rows">
        <Fact title={t("path.why.title")}>
          {c.why.reason && KNOWN.has(`path.reason.${c.why.reason}`)
            ? say(`path.reason.${c.why.reason}`)
            : <span className="ws-unk-t">{t("path.unknown")}</span>}
          {c.why.refs.length > 0 && (
            <span className="muted small"> {t("path.why.refs")}: {joined(c.why.refs.map((x) => <RefChip key={`${x.kind}${x.id}`} r={x} projectId={projectId} />))}</span>
          )}
          {c.why.stale && <Stale c={c} />}
          {c.why.note && !noteLong && <Note c={c} />}
        </Fact>
      </dl>
      <ol className="ws-chain" aria-label={t("path.chain.aria")}>
        <Steps steps={c.chain} projectId={projectId} />
      </ol>
      {unknownStep && (
        <p className="muted small ws-once">
          {c.recorded_since ? t("path.unknown.once", { date: shortDate(c.recorded_since) }) : t("path.unknown.once_undated")}
        </p>
      )}
      <dl className="ws-rows">
        {c.holds && <Fact title={t("path.holds.title")}><HoldsRow c={c} projectId={projectId} /></Fact>}
        {!c.decided && declared.length > 0 && (
          <Fact title={t("path.after.title")}>
            <ul className="ws-list">{declared.map((x, i) => <AfterItem key={i} line={x} choices={choices} decided={false} projectId={projectId} />)}</ul>
          </Fact>
        )}
        {c.unanswered && <Fact title={t("path.lapse.title")}><p className="ws-p"><LapseRow c={c} choices={choices} /></p></Fact>}
        {c.decided && (
          <Fact title={t("path.happened.title")}>
            {c.after.length > 0
              ? <ul className="ws-list">{c.after.map((x, i) => <AfterItem key={i} line={x} choices={choices} decided projectId={projectId} />)}</ul>
              : <span className="ws-unk-t">{t("path.unknown")}</span>}
          </Fact>
        )}
      </dl>
      {projectId != null && (
        <p className="ws-show">
          <Link to={`/projects/${projectId}/path?focus=REQ-${r.id}&req=${r.id}`} data-show-on-path="">
            <Icon name="path" size={14} />{t("path.show")}
          </Link>
        </p>
      )}
      {detailed && (
        <details className="ws-fold">
          <summary>{t("common.details")}</summary>
          {c.why.refs.some((x) => x.label) && (
            <ul className="ws-list">
              {c.why.refs.filter((x) => x.label).map((x) => <li key={`${x.kind}${x.id}`}><RefChip r={x} projectId={projectId} /> {x.label}</li>)}
            </ul>
          )}
          {noteLong && <Note c={c} />}
          {c.also_asked.length > 0 && (
            <>
              <p className="muted small ws-h2">{t("path.also")}</p>
              <ol className="ws-chain"><Steps steps={c.also_asked} projectId={projectId} /></ol>
            </>
          )}
          {hidden.length > 0 && projectId != null && (
            <ul className="ws-tasks">{hidden.map((x) => <TaskLine key={`${x.id}${x.phase}`} task={x} projectId={projectId} />)}</ul>
          )}
        </details>
      )}
    </section>
  );
}
