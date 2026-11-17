import { Link } from "react-router-dom";
import { useWorker } from "../components/frame";
import { Chip } from "../components/kit";
import { AgentStopButton } from "../components/stop";
import { ActionButton, ago, refHref, usd } from "../components/ui";
import { t } from "../i18n";
import { agentName, label } from "../labels";
import { Decide } from "./Cards";
import { elapsedShort, routineWord, stageWord } from "./format";
import { ActionCard, ActionItem, CurrentStage } from "./types";

function itemHref(c: ActionItem): string | null {
  if (c.project_id === null) return null;
  if (c.task_id !== null) return refHref("T", c.task_id, c.project_id);
  if (c.dev_job_id !== null) return refHref("J", c.dev_job_id, c.project_id);
  return refHref("P", c.project_id);
}

const ACTIVE = ["RUNNING", "REVIEW", "REWORK"];

/** A task's status word, coloured only when it is a state the owner may care about: running or failed. While the
 * worker is off a running task is not moving, so it says it waits instead. */
export function TaskState({ status }: { status: string }) {
  const worker = useWorker();
  if (status === "FAILED") return <Chip tone="bad">{label(status)}</Chip>;
  if (ACTIVE.includes(status)) {
    const held = worker.running(status);
    return held ? <Chip tone="warn">{held}</Chip> : <Chip tone="run">{label(status)}</Chip>;
  }
  return <span className="small muted nowrap">{label(status)}</span>;
}

/** One thing the agent is on: in which cycle and stage, what, for how long, at what cost. The clock is the client's;
 * while the worker is off it shows none, since nothing moves then and the page's headline says why. */
function Item({ item, now, held }: { item: ActionItem; now: number; held: boolean }) {
  const to = itemHref(item);
  const body = (
    <>
      <p className="cy-action-where small">
        {item.stage_title ? t("cycle.working.tag", { n: item.cycle_number ?? 0, stage: item.stage_title }) : t("cycle.working.outside")}
        {item.kind === "review" && <>{" "}<span className="tag">{t("cycle.working.review")}</span></>}
        {item.kind === "dev" && <>{" "}<span className="tag">{t("cycle.working.dev")}</span></>}
      </p>
      <p className="agent-work" title={item.title}>{item.title}</p>
      <p className="cy-action-f small muted num">
        <span>{!held && elapsedShort(item.started_at, now) && t("cycle.working.for", { time: elapsedShort(item.started_at, now) })}</span>
        {/* the call ledger's sum for this task or build, the money Costs counts; a queue job keeps none */}
        {item.kind !== "job" && <span>{t(item.kind === "dev" ? "cycle.working.spent_dev" : "cycle.working.spent_task", { usd: usd(item.spent_usd) })}</span>}
      </p>
    </>
  );
  return to ? <Link to={to} className="cy-item">{body}</Link> : <div className="cy-item">{body}</div>;
}

/** One agent at work, once, with every task it is on listed under its name, and the owner's stop for it. While the
 * worker is off the section's heading and the page's headline say so once, so the card adds no chip of its own. */
function Action({ card, now }: { card: ActionCard; now: number }) {
  const worker = useWorker();
  return (
    <div className={`agent-card ${card.stopping || worker.down ? "s-stopped" : "s-running"} cy-action`}>
      <div className="cy-action-h">
        <span className="agent-n">{agentName(card.agent)}</span>
        {card.items.length > 1 && <span className="tag">{t("cycle.working.tasks", { n: card.items.length })}</span>}
        {worker.down ? null
          : card.stopping ? <Chip tone="warn">{t("cycle.working.stopping")}</Chip> : <span className="live-dot" aria-hidden="true" />}
      </div>
      {card.items.map((item, i) => <Item key={`${item.task_id ?? item.dev_job_id ?? item.job_id ?? i}`} item={item} now={now} held={worker.down} />)}
      <div className="cy-action-a">
        {card.stopping && <span className="small muted">{t("cycle.working.stopping_note")}</span>}
        {(card.stopped || !card.stopping) && <AgentStopButton agent={card.agent} stopped={card.stopped} />}
      </div>
    </div>
  );
}

/** One card per working agent; the agents with nothing to do are one line, not cards. */
/** The stage's own "planning" or "queued" sentence sits with its tasks (CurrentView), so it is not said here twice. */
export function WorkingNow({ cards, idle, stopped = 0, now }: { cards: ActionCard[]; idle: number; stopped?: number; now: number; starting?: CurrentStage["starting"] }) {
  const worker = useWorker();
  return (
    <section className="cy-working" aria-label={t("cycle.working.title")}>
      <h3 className="cy-h">{t(worker.down ? "cycle.working.title_held" : "cycle.working.title")}{cards.length > 0 && <span className="sec-n">{cards.length}</span>}</h3>
      {cards.length > 0 && (
        <div className="cy-actions-grid">
          {cards.map((c) => <Action key={c.agent} card={c} now={now} />)}
        </div>
      )}
      <p className="quiet-line">
        {cards.length ? t("cycle.working.others", { n: idle })
          : t("cycle.working.all_idle", { n: idle })}
        {stopped > 0 && <> · <Link to="/agents?state=stopped">{t("cycle.working.idle_stopped", { n: stopped })}</Link></>}
      </p>
    </section>
  );
}

const TASKS_OPEN = 3;

/** The running stage: what it is for, its tasks counted, and the owner's two levers on it. */
export function CurrentView({ cur, decide, n, cycleId }: { cur: CurrentStage; decide: Decide; n: number; cycleId: number }) {
  const { stage } = cur;
  const can = (a: CurrentStage["actions"][number]) => cur.actions.includes(a);
  const held = useWorker().label;
  const word = stageWord(stage.status, held);
  return (
    <div className="box cy-current">
      <header className="cy-current-h">
        <h3>{stage.title}</h3>
        {/* the strip above already says this stage waits for the worker */}
        {word.word !== held && <Chip tone={word.tone}>{word.word}</Chip>}
        <span className="small muted">{t("cycle.n", { n })}</span>
        <span className="cy-current-a">
          {can("resume_stage") && (
            <ActionButton className="btn-primary" run={() => decide(cycleId, { kind: "resume_stage", stage_id: stage.id })}>
              {t("cycle.current.resume")}
            </ActionButton>
          )}
        </span>
      </header>
      <p className="cy-doing">{cur.doing}</p>
      <p className="small muted">
        <span className="strong">{t("cycle.current.you")}</span> {cur.owner_does}
      </p>
      {cur.recurring.length > 0 && (
        <p className="small muted"><span className="strong">{t("cycle.current.recurring")}</span> {cur.recurring.map(routineWord).join(" · ")}</p>
      )}
      {cur.close_requested && <p className="small warn-t">{t("cycle.current.closing")}</p>}
      {/* the stage's tasks fold under their count: the dashboard says where the stage stands, the project page lists the work */}
      <details className="cy-tasks" open={cur.tasks.length <= TASKS_OPEN || undefined}>
        <summary className="cy-h">
          {t("cycle.current.tasks")}
          {cur.tasks_total > 0 && <span className="sec-n num">{t("cycle.tasks_done", { done: cur.tasks_done, total: cur.tasks_total })}</span>}
        </summary>
        {cur.tasks.length ? (
          <ul className="ilist">
            {cur.tasks.map((task) => (
              <li key={task.id}>
                <span className="mono small muted">{task.ref}</span>
                <Link className="grow t1" to={`/projects/${task.project_id}/tasks?task=${task.id}`} title={task.title}>{task.title}</Link>
                <span className="small muted nowrap">{agentName(task.agent)}</span>
                <span className="cy-task-state"><TaskState status={task.status} /></span>
              </li>
            ))}
          </ul>
        ) : <p className="quiet-line">{cur.starting ? t(`cycle.starting.${cur.starting}`) : t("cycle.current.no_tasks")}</p>}
      </details>
      {(can("stop_stage") || can("close_stage_now")) && (
        <div className="cy-actions">
          {can("close_stage_now") && (
            <div className="cy-act">
              <ActionButton className="btn-ghost" confirm={t("cycle.current.close_yes")} run={() => decide(cycleId, { kind: "close_stage_now", stage_id: stage.id })}>
                {t("cycle.current.close")}
              </ActionButton>
              <p className="small muted">{t("cycle.current.close_confirm")}</p>
            </div>
          )}
          {can("stop_stage") && (
            <div className="cy-act">
              <ActionButton className="danger" confirm={t("cycle.current.stop_yes")} run={() => decide(cycleId, { kind: "stop_stage", stage_id: stage.id })}>
                {t("cycle.current.stop")}
              </ActionButton>
              <p className="small muted">{t("cycle.current.stop_confirm")}</p>
            </div>
          )}
        </div>
      )}
      {cur.started_at && <p className="small muted num cy-current-since">{t("cycle.current.started", { when: ago(cur.started_at) })}</p>}
    </div>
  );
}
