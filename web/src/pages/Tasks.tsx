import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { ItemLink } from "../components/panel";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { post, qs, Row, useLive } from "../api";
import { ActivityItem } from "../components/activity";
import { EvidenceSummary } from "../components/evidence";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { Icon } from "../components/icons";
import {
  Board, BoardCard, Box, Chip, EmptyState, FirstRun, PageHead, Row as ListRow, Rows, Section, Status, Toolbar, Tone, useView, ViewSwitch,
} from "../components/kit";
import { ProjectStateChip } from "../components/project";
import { AgentStopButton } from "../components/stop";
import { useBusinessLive, useBusinessSelection, useFollowBusiness, useNoBusiness } from "../business";
import {
  ActionButton, ago, Agent, announce, Badge, ConfirmBox, dur, leadLine, leadOf, Loading, Markdown, settleFocus, Skeleton, tid, Time, usd, useShowTest, when,
} from "../components/ui";
import { AreaFold, deep, Fit, plain } from "../components/strengths";
import { crewHref } from "../crews";
import { locale, PlainKey, t, tx } from "../i18n";
import { agentName, errorText, label, purposeLabel, verdictLabel } from "../labels";
import "./work.css";

/** Work that stopped and waits on the owner ("Needs you"): a failure to retry or an approval. The sidebar's Tasks badge
 * counts these. */
export const BLOCKED = ["APPROVAL_REQUIRED", "FAILED"];
const ACTIVE = ["RUNNING", "REVIEW", "REWORK"];

/** Status filter (`?group=`). The keys are linked from other screens ("tasks?group=blocked"), so they stay. */
const FILTERS: [string, PlainKey, string[]][] = [
  ["blocked", "work-tasks.filter.blocked", BLOCKED],
  ["active", "work-tasks.filter.active", ACTIVE],
  ["open", "work-tasks.filter.open", ["PROPOSED", "READY"]],
  ["done", "work-tasks.filter.done", ["DONE", "APPROVED"]],
  ["closed", "work-tasks.filter.closed", ["CANCELLED", "REJECTED", "REPLACED"]],
];

/** Board columns, left to right in the order work moves. A status no column names waits in the first. */
const COLUMNS: { key: string; label: PlainKey; tone: Tone; statuses: string[]; empty: PlainKey }[] = [
  { key: "wait", label: "work-tasks.col.wait", tone: "warn", statuses: ["PROPOSED", "READY", "APPROVAL_REQUIRED"], empty: "work-tasks.empty.wait" },
  { key: "run", label: "work-tasks.col.run", tone: "run", statuses: ["RUNNING"], empty: "work-tasks.empty.run" },
  { key: "review", label: "work-tasks.col.review", tone: "info", statuses: ["REVIEW", "REWORK"], empty: "work-tasks.empty.review" },
  { key: "done", label: "work-tasks.col.done", tone: "ok", statuses: ["DONE", "APPROVED"], empty: "work-tasks.empty.done" },
  { key: "closed", label: "work-tasks.col.closed", tone: "bad", statuses: ["FAILED", "CANCELLED", "REJECTED", "REPLACED"], empty: "work-tasks.empty.closed" },
];
const columnOf = (st: string) => COLUMNS.find((c) => c.statuses.includes(st)) ?? COLUMNS[0];

/** The list's status groups: what needs the owner first, then the board's order without those. */
const LIST_GROUPS: { key: string; label: PlainKey; tone: Tone; statuses: string[] }[] = [
  { key: "needs", label: "work-tasks.filter.blocked", tone: "bad", statuses: BLOCKED },
  ...COLUMNS.map((c) => ({ ...c, statuses: c.statuses.filter((x) => !BLOCKED.includes(x)) })),
];
const listGroupOf = (st: string) => LIST_GROUPS.find((g) => g.statuses.includes(st)) ?? LIST_GROUPS[1];

const verdictTone = (task: Row) =>
  task.verdict === "pass" ? "t-ok" : task.verdict === "unchecked" && task.outcome !== "partial" ? "" : "t-warn";

function statusTone(st: string): Tone {
  if (["CANCELLED", "REPLACED", "PROPOSED", "READY"].includes(st)) return "muted";
  if (st === "APPROVAL_REQUIRED") return "warn";
  return columnOf(st).tone;
}

type Worker = ReturnType<typeof useWorker>;

/** A task's state as a chip. Work in a running state says it waits for the worker while none runs. */
function StatusChip({ st, worker }: { st: string; worker: Worker }) {
  const held = ACTIVE.includes(st) ? worker.running(st) : null;
  return <Chip tone={held ? "warn" : statusTone(st)}>{held ?? label(st)}</Chip>;
}

// the provider's usage-limit wording, as runtime/quota.py _MARKERS reads it
const QUOTA = /usage limit|hit your (?:[\w-]+ )?limit|limit reached|rate_limit_exceeded|quota/i;
const ERROR_CLASSES = /^(?:[A-Za-z_][\w./-]*:\s*)+/;
const HANGUL = /[\uAC00-\uD7A3]/;

type Failure = { handled: boolean; says: string } | null | undefined;
/** Who moves a failed task on: the company by itself, the owner after a reset or a budget, or the owner's call. */
type FailureWho = "company" | "quota" | "budget" | "owner" | "unknown";

/**
 * Why a task failed, in one owner sentence, and who moves it on. The card, the list row and the detail all read it, so
 * the three never disagree. The server's reading (`failure`, on the detail only) wins where it knows more: work the
 * company redoes by itself. A usage limit and the engine's budget errors have owner words; any other stored error
 * shows its first line when that line reads as a sentence (the rule of the server's failure_reason), and otherwise
 * says plainly that the cause is not known. The stored text goes under Details, never in place of the sentence.
 */
function failureText(task: Row, failure?: Failure): { says: string; who: FailureWho } {
  const raw = String(task.error ?? "");
  if (failure?.handled) return { says: failure.says, who: "company" };
  if (QUOTA.test(raw)) return { says: t("work-tasks.fail.quota"), who: "quota" };
  const known = errorText(raw);
  if (known !== raw) return { says: known, who: "budget" };
  const first = raw.trim().split("\n", 1)[0].replace(ERROR_CLASSES, "").trim();
  const readable = HANGUL.test(first) || (locale() === "en" && first.split(/\s+/).length >= 3);
  if (!readable) return { says: t("work-tasks.fail.unclassified"), who: "unknown" };
  return { says: first.length > 200 ? `${first.slice(0, 199)}…` : first, who: "owner" };
}

type GroupBy = "status" | "owner" | "project";
const GROUP_BY: [GroupBy, PlainKey][] = [["status", "work-tasks.by.status"], ["owner", "work-tasks.by.owner"], ["project", "work-tasks.by.project"]];
type Group = { key: string; title: ReactNode; rows: Row[] };

/** All work, company-wide or for one project (`projectId`). The selected task opens beside the board or list. */
export default function Tasks({ projectId }: { projectId?: number }) {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const [showTest] = useShowTest();
  const noBusiness = useNoBusiness();
  const { current } = useBusinessSelection();
  const worker = useWorker();
  const [view, setView] = useView("tasks");
  const scoped = projectId !== undefined;
  const group = params.get("group") ?? "";
  const owner = params.get("owner") ?? "";
  const project = scoped ? null : Number(params.get("project")) || null;
  const byParam = params.get("by") as GroupBy | null;
  const by: GroupBy = byParam && GROUP_BY.some(([k]) => k === byParam) && !(scoped && byParam === "project") ? byParam : "status";
  const [q, setQ] = useState(params.get("q") ?? "");
  const selected = Number(params.get("task")) || null;
  const side = useRef<HTMLElement>(null);
  const { data, error, failure, reload } = useBusinessLive<{ tasks: Row[]; owners: string[] }>(
    `/tasks${qs({ project_id: projectId, include_test: showTest })}`, ["tasks", "agent_requests"]);
  const set = (k: string, v: string | null) => {
    if (v == null || v === "") params.delete(k);
    else params.set(k, v);
    setParams(params, { replace: k !== "task" });
  };
  /** This page's address with some parameters changed, so a link keeps the filters the owner set. */
  const link = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) {
      if (v == null) next.delete(k);
      else next.set(k, v);
    }
    return `${pathname}?${next}`;
  };
  const href = (id: number) => link({ task: String(id) });
  const needle = q.trim().toLowerCase();
  // the scope the numbers describe: project and agent, not the status filter or the search box
  const inScope = useMemo(() => (data?.tasks ?? []).filter((t) => (!project || t.project_id === project)
    && (!owner || t.owner === owner)), [data, project, owner]);
  const statuses = FILTERS.find(([k]) => k === group)?.[2];
  const rows = useMemo(() => inScope.filter((t) => (!statuses || statuses.includes(t.status))
    && (!needle || `t-${t.id} ${t.title} p-${t.project_id} ${t.project ?? ""}`.toLowerCase().includes(needle))), [inScope, statuses, needle]);
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    // stacked layout, and inside a project under its header: bring the detail into view when a task is picked
    if (selected && (scoped || window.matchMedia("(max-width: 1200px)").matches)) side.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [selected, scoped]);
  const head = (status?: Status | null) => scoped ? null : <PageHead title={t("work-tasks.title")} status={status} />;
  if (noBusiness) {
    return (
      <div className="wk-page">
        <PageHead title={t("work-tasks.title")} status={{ text: t("work-tasks.sub_empty") }} />
        <FirstRun>{t("work-tasks.firstrun")}</FirstRun>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="wk-page">
        {head()}
        {error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={6} />}
      </div>
    );
  }
  const all = data.tasks;
  const filtered = !!(group || owner || project || needle);
  const clear = () => {
    for (const k of ["group", "owner", "project", "q"]) params.delete(k);
    setQ("");
    setParams(params, { replace: true });
  };
  const detail = selected ? (
    <aside className="wk-side" aria-label={t("work-tasks.detail")} ref={side}>
      <TaskDetail id={selected} onClose={() => set("task", null)} onChange={reload} />
    </aside>
  ) : null;
  const addButton = scoped ? (
    <button type="button" className={all.length ? "btn-ghost" : "btn-primary"} onClick={() => setAdding(!adding)} aria-expanded={adding}>
      <Icon name="plus" />{t("work-tasks.add")}
    </button>
  ) : null;
  const newTask = adding && scoped ? (
    <NewTask projectId={projectId} owners={data.owners} tasks={all} onDone={(id) => { setAdding(false); reload(); if (id) set("task", String(id)); }} />
  ) : null;

  // No work yet: one place says why and where it comes from; the toolbar and empty filters wait for the first task.
  if (!all.length) {
    const planned = current?.brief_version != null;
    return (
      <div className="wk-page">
        {head(null)}
        {newTask}
        <div className={`wk-split ${selected ? "has" : ""}`}>
          <div className="wk-main">
            <Box>
              {scoped ? (
                <EmptyState title={t("work-tasks.none_project")} action={!adding && addButton}>{t("work-tasks.none_project_body")}</EmptyState>
              ) : planned ? (
                <EmptyState title={t("work-tasks.none")} action={<Link className="btn-ghost" to="/">{t("work-tasks.none_stage_link")}</Link>}>
                  {t("work-tasks.none_stage")}
                </EmptyState>
              ) : (
                <EmptyState title={t("work-tasks.none")} action={<Link className="btn-ghost" to="/business">{t("work-tasks.none_plan_link")}</Link>}>
                  {t("work-tasks.none_plan")}
                </EmptyState>
              )}
            </Box>
          </div>
          {detail}
        </div>
      </div>
    );
  }

  const projects = [...new Map(all.map((t) => [t.project_id as number, t.project as string])).entries()].sort((a, b) => b[0] - a[0]);
  const owners = [...new Set(all.map((t) => t.owner as string))].sort((a, b) => agentName(a).localeCompare(agentName(b), locale()));
  // the list grouped by status already leads with what needs the owner; every other view gets it as a strip on top
  const strip = !(view === "list" && by === "status");

  const body = rows.length === 0 ? (
    <Box>
      <EmptyState kind="filtered" title={t("work-tasks.no_match")} action={<button type="button" className="btn-ghost" onClick={clear}>{t("work.clear_filters")}</button>}>
        {t("work-tasks.no_match_body")}
      </EmptyState>
    </Box>
  ) : view === "board" ? (
    <Board label={t("work-tasks.board")} columns={COLUMNS.map((c) => ({
      key: c.key, label: t(c.label), empty: t(c.empty),
      hint: c.key === "run" ? worker.label ?? undefined : undefined,
      // red only when something failed; cancellations alone are not trouble
      tone: c.key === "closed" && !rows.some((t) => t.status === "FAILED") ? "muted" : c.key === "run" && worker.label ? "warn" : c.tone,
      items: rows.filter((t) => columnOf(t.status) === c).map((t) => (
        <TaskCard key={t.id} task={t} to={href(t.id)} on={selected === t.id} showProject={!scoped} showStatus={c.statuses.length > 1} worker={worker} />
      )),
    }))} />
  ) : (
    <TaskList groups={groupRows(rows, by, worker)} by={by} selected={selected} href={href} showProject={!scoped} worker={worker} />
  );

  const status = tasksStatus(inScope, link({ group: "blocked", task: null }), worker);
  return (
    <div className="wk-page">
      {scoped ? <p className={`phead-status wk-scope t-${status.tone ?? "muted"}`}><span className="sdot" aria-hidden="true" />{status.text}</p> : head(status)}
      <Toolbar label={t("work-tasks.toolbar")}
        right={<>
          <span className="small muted num wk-count" aria-live="polite">{filtered ? t("work-tasks.shown", { shown: rows.length, all: all.length }) : t("work-tasks.count", { n: all.length })}</span>
          {addButton}
          <ViewSwitch value={view} onChange={setView} />
        </>}>
        <label className="search-in">
          <Icon name="search" />
          <span className="sr-only">{t("work-tasks.search")}</span>
          <input type="search" placeholder={t(scoped ? "work-tasks.search_placeholder_project" : "work-tasks.search_placeholder")} value={q}
            onChange={(e) => { setQ(e.target.value); set("q", e.target.value); }} />
        </label>
        <select aria-label={t("work-tasks.by_status")} value={group} onChange={(e) => set("group", e.target.value)}>
          <option value="">{t("work-tasks.all_statuses")}</option>
          {FILTERS.map(([k, name, s]) => <option key={k} value={k}>{t(name)} ({inScope.filter((x) => s.includes(x.status)).length})</option>)}
        </select>
        {!scoped && (
          <select aria-label={t("work-tasks.by_project")} value={project ?? ""} onChange={(e) => set("project", e.target.value)}>
            <option value="">{t("work-tasks.all_projects")}</option>
            {projects.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        )}
        <select aria-label={t("work-tasks.by_agent")} value={owner} onChange={(e) => set("owner", e.target.value)}>
          <option value="">{t("work-tasks.all_agents")}</option>
          {owners.map((o) => <option key={o} value={o}>{agentName(o)}</option>)}
        </select>
        {view === "list" && (
          <label className="wk-by">
            <span>{t("work-tasks.group")}</span>
            <select value={by} onChange={(e) => set("by", e.target.value === "status" ? null : e.target.value)}>
              {GROUP_BY.filter(([k]) => !(scoped && k === "project")).map(([k, name]) => <option key={k} value={k}>{t(name)}</option>)}
            </select>
          </label>
        )}
        {filtered && <button type="button" className="btn-ghost small" onClick={clear}>{t("work.clear_filters")}</button>}
      </Toolbar>
      {newTask}
      <div className={`wk-split ${selected ? "has" : ""}`}>
        <div className="wk-main">
          {strip && <NeedsYou rows={rows} href={href} selected={selected} showProject={!scoped} />}
          {body}
        </div>
        {detail}
      </div>
    </div>
  );
}

function count(rows: Row[], statuses: string[]) {
  return rows.filter((t) => statuses.includes(t.status)).length;
}

/** Cancelled work is not work left to do, nor is work a later task redid, so neither is in the completion rate (as
 * on the project list). */
function doneRate(rows: Row[]) {
  const base = rows.length - count(rows, ["CANCELLED", "REJECTED", "REPLACED"]);
  const done = count(rows, ["DONE", "APPROVED"]);
  return { done, base, pct: base ? Math.round((done / base) * 100) : null };
}

/** The board's conclusion: what needs the owner first (a failure to retry, an approval), then how much is moving, and
 * that nothing moves while the worker is off. */
function tasksStatus(rows: Row[], needsTo: string, worker: Worker): Status {
  const failed = count(rows, ["FAILED"]);
  const needs = count(rows, BLOCKED);
  const active = count(rows, ACTIVE);
  const waiting = count(rows, ["PROPOSED", "READY"]);
  const { done, base } = doneRate(rows);
  if (!rows.length) return { text: t("work-tasks.status.none") };
  const moving = active ? t("work-tasks.status.moving", { active, waiting })
    : waiting ? t("work-tasks.status.waiting", { waiting }) : t("work-tasks.status.idle");
  const held = (active || waiting) > 0 && worker.down;
  return {
    tone: failed ? "bad" : needs || held ? "warn" : active ? "run" : "muted",
    text: <>
      {needs ? <><Link to={needsTo}>{t("work-tasks.status.needs", { n: needs })}</Link> </> : null}
      {t("work-tasks.status.done", { done, total: base })} {moving}
      {held ? <> {t("work-tasks.status.worker_down")} <Fix text={`\`${WORKER_FIX}\``} /></> : null}
    </>,
  };
}

/** What needs the owner, above the work: each failure with its cause, each approval, one click to open. */
function NeedsYou({ rows, href, selected, showProject }: { rows: Row[]; href: (id: number) => string; selected: number | null; showProject: boolean }) {
  const items = rows.filter((x) => BLOCKED.includes(x.status));
  if (!items.length) return null;
  return (
    <Section title={t("work-tasks.needs.title")} count={items.length} className="wk-needs">
      <Box pad={false}>
        <Rows label={t("work-tasks.needs.title")}>
          {items.map((task) => (
            <ListRow key={task.id} to={href(task.id)} selected={selected === task.id}
              lead={<Chip tone={statusTone(task.status)}>{label(task.status)}</Chip>}
              title={<><span className="wk-tid">{tid(task.id)}</span>{task.title}</>}
              meta={task.status === "FAILED" ? failureText(task).says : t("work-tasks.needs.approval")}
              end={<>{showProject && <span className="wk-proj" title={task.project}>{task.project}</span>}<span className="wk-open btn-ghost small" aria-hidden="true">{t("work-tasks.needs.open")}</span></>} />
          ))}
        </Rows>
      </Box>
    </Section>
  );
}

/**
 * One status signal per card: the column says the state, so the card shows a state tag only where its column holds
 * several (failed beside cancelled, not started beside ready). Review results and evidence stay in the footer as text,
 * and only when they need a look.
 */
function TaskCard({ task, to, on, showProject, showStatus, worker }: {
  task: Row; to: string; on: boolean; showProject: boolean; showStatus: boolean; worker: Worker;
}) {
  const open = !["DONE", "APPROVED", "CANCELLED", "REJECTED", "REPLACED"].includes(task.status);
  return (
    <BoardCard to={to} selected={on} live={task.status === "RUNNING" && !worker.label} title={task.title}
      tags={showStatus && task.status !== "DONE" ? <span className={`tag t-${statusTone(task.status)}`}>{label(task.status)}</span> : null}
      sub={<>{agentName(task.owner)}{showProject && <span title={task.project}> · {task.project}</span>}
        {task.status === "FAILED" && task.error && <span className="wk-err" title={task.error}> · {failureText(task).says}</span>}</>}
      foot={<>
        <span className="wk-when" title={when(task.updated_at)}>{ago(task.updated_at)}</span>
        {task.waiting_on && <span className="warn-t">{t("work-tasks.card.waiting", { agent: agentName(task.waiting_on) })}</span>}
        {task.pending > 0 && <span className="warn-t">{t("work-tasks.card.pending", { n: task.pending })}</span>}
        {task.contested && <span className="warn-t">{t("work-tasks.card.contested")}</span>}
        {open && task.verdict && task.verdict !== "pass" && (task.verdict !== "unchecked" || task.outcome === "partial") && <span className="warn-t">{t("work-tasks.card.review", { verdict: verdictLabel(task) })}</span>}
        {open && task.depends_on?.length > 0 && <span title={t("work-tasks.card.after_title")}>{t("work-tasks.card.after", { ids: task.depends_on.map(tid).join(", ") })}</span>}
        {task.spent_usd > 0 && <span className="sp num">{usd(task.spent_usd)}</span>}
      </>} />
  );
}

function groupRows(rows: Row[], by: GroupBy, worker: Worker): Group[] {
  if (by === "status") {
    return LIST_GROUPS.map((g) => {
      const held = g.key === "run" && worker.label;
      return { key: g.key, title: <Chip tone={held ? "warn" : g.tone}>{t(g.label)}</Chip>, rows: rows.filter((task) => listGroupOf(task.status) === g) };
    }).filter((g) => g.rows.length);
  }
  const map = new Map<string, Row[]>();
  for (const task of rows) {
    const k = String(by === "owner" ? task.owner : task.project_id);
    map.set(k, [...(map.get(k) ?? []), task]);
  }
  return [...map.entries()]
    .map(([k, g]) => ({ key: k, rows: g, title: by === "owner" ? agentName(k) : g[0].project }))
    .sort((a, b) => by === "project" ? Number(b.key) - Number(a.key) : b.rows.length - a.rows.length);
}

/** Dense rows: the state first, then the number and title with one line of why (the failure, or who and where), then
 * what needs a look and when it last moved. */
function TaskList({ groups, by, selected, href, showProject, worker }: {
  groups: Group[]; by: GroupBy; selected: number | null; href: (id: number) => string; showProject: boolean; worker: Worker;
}) {
  return (
    <div className="wk-groups">
      {groups.map((g) => (
        <Section key={g.key} title={g.title} count={g.rows.length}>
          <Box pad={false}>
            <Rows className="wk-list">
              {g.rows.map((task) => (
                <ListRow key={task.id} to={href(task.id)} selected={selected === task.id}
                  lead={<StatusChip st={task.status} worker={worker} />}
                  title={<><span className="wk-tid">{tid(task.id)}</span><span title={task.title}>{task.title}</span></>}
                  meta={task.status === "FAILED" && task.error
                    ? <span className="wk-err">{failureText(task).says}</span>
                    : <>{by !== "owner" && agentName(task.owner)}{showProject && by !== "project" && <>{by !== "owner" && " · "}{task.project}</>}</>}
                  end={<>
                    {task.contested && <span className="tag t-bad">{t("work-tasks.row.contested")}</span>}
                    {task.waiting_on && <span className="tag t-warn">{t("work-tasks.card.waiting", { agent: agentName(task.waiting_on) })}</span>}
                    {task.pending > 0 && <span className="tag t-warn">{t("work-tasks.row.pending", { n: task.pending })}</span>}
                    {task.verdict && <span className={`tag ${verdictTone(task)}`}>{t("work-tasks.card.review", { verdict: verdictLabel(task) })}</span>}
                    <span className="wk-ago" title={when(task.updated_at)}>{ago(task.updated_at)}</span>
                  </>} />
              ))}
            </Rows>
          </Box>
        </Section>
      ))}
    </div>
  );
}

// The project phases past the final decision (services/control.py _CLOSED_PHASES): its tasks can no longer be retried.
const CLOSED_PHASES = ["deciding", "reporting", "done", "archived"];

/** What a retry answered (`POST /tasks/:id/retry`): the task row, the budget before the retry, and whether a worker is
 * there to run it now. Older servers send neither extra field. */
type RetryAnswer = Row & { budget_usd: number; budget_was_usd?: number; worker_running?: boolean; note_sent?: boolean };

/** The retry's result as the owner needs it: marked, what it changed (a raised budget), and why nothing moves yet. It
 * stays in the panel after the button goes, so the owner never reads a retry as a fix that already ran. */
function RetryNote({ answer, projectFailed }: { answer: RetryAnswer; projectFailed: boolean }) {
  const was = answer.budget_was_usd;
  const raised = was != null && answer.budget_usd > was;
  return (
    <p className="wk-note" role="status">
      {t("work-tasks.retry.done")} {answer.note_sent ? <>{t("work-tasks.retry.note_sent")} </> : null}
      {projectFailed ? t("work-tasks.detail.retry_failed") : null}
      {raised ? <> {t("work-tasks.retry.budget", { from: usd(was), to: usd(answer.budget_usd) })}</> : null}
      {answer.worker_running === false
        ? <> {tx("work-tasks.retry.no_worker", { cmd: <Fix text={`\`${WORKER_FIX}\``} /> })}</>
        : !projectFailed ? <> {t("work-tasks.detail.retry_next")}</> : null}
    </p>
  );
}

/** Why it failed, who moves it on, and the next step; the stored text under Details. */
function FailureBlock({ task, failure, can, project, onRetry }: {
  task: Row; failure: Failure; can: Row; project: Row; onRetry: ReactNode;
}) {
  const f = failureText(task, failure);
  const closed = ["DONE", "ARCHIVED"].includes(project.status) || CLOSED_PHASES.includes(project.phase);
  // a retry the server refuses while the project runs is a budget stop: its sentence says what is short
  const budget = !can.retry && !closed && !f.who.startsWith("company");
  const next = f.who === "company" ? t("work-tasks.fail.who.company")
    : !can.retry ? (closed ? t("work-tasks.detail.no_retry") : t("work-tasks.fail.who.budget"))
      : f.who === "quota" ? t("work-tasks.fail.who.quota") : f.who === "budget" ? t("work-tasks.fail.who.budget")
        : t("work-tasks.fail.who.retry");
  return (
    <section className={`wk-fail ${f.who === "company" ? "is-handled" : ""}`} aria-labelledby={`fail-${task.id}`}>
      <h3 id={`fail-${task.id}`}>{t("work-tasks.fail.head")}</h3>
      {/* the next line says when to retry, so a usage limit's own sentence stops at its cause here */}
      <p className="wk-fail-says">{budget && failure?.says ? failure.says : f.who === "quota" ? t("work-tasks.fail.quota_cause") : f.says}</p>
      <p className="wk-fail-next">{next}</p>
      {(onRetry || budget || f.who === "budget") && (
        <div className="row wrap tight">
          {onRetry}
          {(budget || f.who === "budget") && <Link className="btn-ghost small" to={`/projects/${project.id}/settings#budget`}>{t("work-tasks.fail.budget_link")}</Link>}
        </div>
      )}
      {task.error && (
        <details className="wk-raw">
          <summary>{f.who === "company" ? t("work-tasks.detail.reviewer_words") : t("work-tasks.fail.details")}</summary>
          <p className="pre-wrap mono">{task.error}</p>
        </details>
      )}
    </section>
  );
}

function TaskDetail({ id, onClose, onChange }: { id: number; onClose: () => void; onChange: () => void }) {
  const { data, error, failure: loadFailure, reload } = useLive<any>(`/tasks/${id}`, ["tasks", "agent_requests", "activity_events"]);
  useFollowBusiness(data ? (data.project.business_id ?? null) : undefined, `task:${id}`, data?.task.title ?? "");
  const worker = useWorker();
  const [editing, setEditing] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [retried, setRetried] = useState<RetryAnswer | null>(null);
  // the comment being written: a retry sends it along, so a note meant for the new attempt is never left behind
  const [draft, setDraft] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    setEditing(false);
    setCancelling(false);
    setRetried(null);
    setDraft("");
  }, [id]);
  useEffect(() => {
    // focus returns to the card or row that opened the task when the panel closes
    opener.current = document.activeElement as HTMLElement | null;
    return () => {
      const back = opener.current;
      const shown = shownId.current;
      // after React has finished removing the panel: the row that opened it, else the same task's card or row
      // elsewhere on the page (a retried task leaves "Needs you" for a column), else the page's main region
      window.setTimeout(() => {
        if (back && back !== document.body && document.contains(back)) return back.focus();
        const again = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href*="task="]'))
          .find((a) => new URL(a.href).searchParams.get("task") === String(shown));
        if (again) again.focus();
        else settleFocus(null);
      }, 0);
    };
  }, []);
  const shownId = useRef(id);
  shownId.current = id;
  const loaded = !!data;
  useEffect(() => {
    if (loaded) heading.current?.focus();
  }, [id, loaded]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.key !== "Escape" || e.defaultPrevented || el?.closest("input, textarea, select, .confirm-box, .act-armed")) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const close = <button type="button" className="icon-btn wk-close" onClick={onClose} aria-label={t("work-tasks.detail.close")}><Icon name="close" size={14} /></button>;
  if (!data) {
    return (
      <section className="wk-detail">
        <header className="wk-dh"><div className="wk-dh-main"><p className="wk-dh-meta mono">{tid(id)}</p></div>{close}</header>
        <div className="wk-dbody">{error ? <Loading error={error} failure={loadFailure} retry={reload} /> : <Skeleton rows={4} />}</div>
      </section>
    );
  }
  const task = data.task;
  const p = data.project;
  const r = task.result ?? {};
  const rv = task.review;
  const can = data.can;
  // The owner is new to this task's area: the summary, its conclusion, comes first and the goal folds under one line.
  const fit: Fit | undefined = data.fit;
  const brief = plain(fit) && !deep(fit);
  const summary = r.summary && (<><h3>{t("work-tasks.detail.summary")}</h3><Markdown text={r.summary} projectId={p.id} /></>);
  const criteria = (
    <ul className="crit">
      {task.acceptance_criteria.map((c: string) => {
        const unmet = rv?.criteria_unmet?.includes(c);
        const met = rv?.criteria_met?.includes(c);
        return <li key={c} className={unmet ? "unmet" : met ? "met" : ""}>{c}{unmet && <span className="small bad-t">{t("work-tasks.detail.unmet")}</span>}{met && <span className="small ok-t">{t("work-tasks.detail.met")}</span>}</li>;
      })}
    </ul>
  );
  const refresh = () => {
    reload();
    onChange();
  };
  const note = draft.trim();
  async function retry() {
    const answer = await post<RetryAnswer>(`/tasks/${task.id}/retry`, note ? { note } : {});
    if (note) setDraft("");
    setRetried({ ...answer, note_sent: !!note });
    announce(t("work-tasks.retry.done"));
    refresh();
  }
  const retryButton = can.retry
    ? <ActionButton className="btn-primary" run={retry}>{t(note ? "work-tasks.retry.with_note" : "common.retry")}</ActionButton> : null;
  const held = ACTIVE.includes(task.status) ? worker.running(task.status) : null;
  return (
    <section className="wk-detail" aria-labelledby="td-title">
      <header className="wk-dh">
        <div className="wk-dh-main">
          <p className="wk-dh-meta"><span className="mono">{tid(task.id)}</span> · <Link to={`/projects/${p.id}`}>{p.name}</Link></p>
          <h2 id="td-title" ref={heading} tabIndex={-1}>{task.title}</h2>
        </div>
        {close}
      </header>
      <div className="wk-dbody">
        {retried && <RetryNote answer={retried} projectFailed={p.status === "FAILED"} />}
        <div className="row wrap tight wk-dstate">
          <Chip tone={held ? "warn" : statusTone(task.status)}>{held ?? label(task.status)}</Chip>
          {rv?.verdict && <Badge value={rv.verdict} />}
          {task.contested && <Badge value={t("work-tasks.detail.contested")} tone="bad" raw />}
          <span className="small muted">{t("work-tasks.detail.project_state")} <ProjectStateChip p={p} /></span>
        </div>
        {held && <p className="small warn-t">{t("work-tasks.detail.worker_down")} <Fix text={`\`${WORKER_FIX}\``} /></p>}
        {data.waiting_on && (
          <p className="small warn-t">
            {t("work-tasks.card.waiting", { agent: agentName(data.waiting_on) })} <AgentStopButton agent={data.waiting_on} stopped />
          </p>
        )}
        {task.status === "FAILED" && task.error && <FailureBlock task={task} failure={data.failure} can={can} project={p} onRetry={retryButton} />}
        {task.status === "CANCELLED" && task.error && <p className="wk-quiet-note">{task.error}</p>}
        {(can.edit || can.cancel || (can.retry && !(task.status === "FAILED" && task.error))) && (
          <div className="row wrap tight tactions">
            {can.retry && !(task.status === "FAILED" && task.error) && retryButton}
            {can.edit && <button type="button" className="btn-ghost" onClick={() => setEditing(!editing)} aria-expanded={editing}>{t("work-tasks.detail.edit")}</button>}
            {can.cancel && !cancelling && <button type="button" className="btn-ghost wk-danger" onClick={() => setCancelling(true)}>{t("work-tasks.detail.cancel")}</button>}
          </div>
        )}
        {cancelling && <CancelTask task={task} onCancel={() => setCancelling(false)} onDone={() => { setCancelling(false); refresh(); }} />}
        {editing && <EditTask task={task} onDone={() => { setEditing(false); refresh(); }} />}
        <dl className="props">
          <div><dt>{t("work-tasks.detail.owner")}</dt><dd><Link to={`/agents/${task.owner}`}><Agent id={task.owner} /></Link></dd></div>
          <div><dt>{t("work-tasks.detail.reviewer")}</dt><dd>{(rv?.reviewer ?? data.reviewer) ? <Link to={`/agents/${rv?.reviewer ?? data.reviewer}`}><Agent id={rv?.reviewer ?? data.reviewer} /></Link> : t("work-tasks.detail.no_reviewer")}{(rv?.independence?.startsWith("not independent") || rv?.independence?.startsWith("degraded")) && <span className="bad-t small">{t("work-tasks.detail.not_independent")}</span>}</dd></div>
          <div><dt>{t("work-tasks.detail.crew")}</dt><dd><Link to={crewHref("task", p.id, task.id)}>{t("work-tasks.detail.crew_link")}</Link></dd></div>
          <div><dt>{t("work-tasks.detail.origin")}</dt><dd>{origin(task)}</dd></div>
          {task.iteration > 0 && <div><dt>{t("work-tasks.detail.attempts")}</dt><dd>{t("work-tasks.detail.attempts_of", { n: task.iteration, max: task.max_iterations + 1 })}</dd></div>}
          <div><dt>{t("work-tasks.detail.spent")}</dt><dd className="num">{usd(task.spent_usd)} / {usd(task.budget_usd)}</dd></div>
          {task.started_at && <div><dt>{t("work-tasks.detail.when")}</dt><dd><Time v={task.started_at} /> → {task.finished_at ? <Time v={task.finished_at} /> : t("work-tasks.detail.still_going")}</dd></div>}
          {data.depends_on.length > 0 && <div><dt>{t("work-tasks.detail.depends")}</dt><dd>{data.depends_on.map((d: Row) => <TaskLink key={d.id} d={d} pid={p.id} />)}</dd></div>}
          {data.dependents.length > 0 && <div><dt>{t("work-tasks.detail.dependents")}</dt><dd>{data.dependents.map((d: Row) => <TaskLink key={d.id} d={d} pid={p.id} />)}</dd></div>}
        </dl>
        {brief && summary}
        {brief ? (
          <AreaFold fit={fit} className="sect" head={t("work-tasks.detail.goal")} lead={leadOf(task.objective)[0]}>
            <p className="pre-wrap">{task.objective}</p>
            <h3>{t("work-tasks.detail.criteria")}</h3>
            {criteria}
          </AreaFold>
        ) : (
          <>
            <h3>{t("work-tasks.detail.goal")}</h3>
            <p className="pre-wrap">{task.objective}</p>
            <h3>{t("work-tasks.detail.criteria")}</h3>
            {criteria}
          </>
        )}
        {!brief && summary}
        {(r.evidence_ids?.length ?? 0) > 0 && <EvidenceSummary ids={r.evidence_ids} projectId={p.id} title={t("work-tasks.detail.evidence")} />}
        {rv && (rv.counter_arguments?.length > 0 || rv.criteria_unmet?.length > 0 || rv.notes?.length > 0) && (
          <AreaFold fit={fit} className="sect" head={rv.reviewer ? t("work-tasks.detail.review", { name: agentName(rv.reviewer), n: rv.iteration }) : t("work-tasks.detail.author_unmet")}
            lead={leadOf(rv.criteria_unmet?.[0] ?? rv.notes?.[0] ?? rv.counter_arguments?.[0] ?? "")[0]}>
            {rv.criteria_unmet?.length > 0 && <><b className="small">{t("work-tasks.detail.unmet_head")}</b><ul>{rv.criteria_unmet.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
            {rv.notes?.length > 0 && <><b className="small">{t("work-tasks.detail.notes_head")}</b><ul>{rv.notes.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
            {rv.counter_arguments?.length > 0 && <><b className="small">{t("work-tasks.detail.counter")}</b><ul>{rv.counter_arguments.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
          </AreaFold>
        )}
        {r.findings_markdown && (
          <AreaFold fit={fit} className="sect" head={t("work-tasks.detail.findings")} lead={leadLine(r.findings_markdown)}>
            <Markdown text={r.findings_markdown} projectId={p.id} />
          </AreaFold>
        )}
        {r.open_questions?.length > 0 && (
          <AreaFold fit={fit} className="sect" head={t("work-tasks.detail.open", { n: r.open_questions.length })} lead={leadOf(r.open_questions[0])[0]}>
            <ul>{r.open_questions.map((x: string) => <li key={x}>{x}</li>)}</ul>
          </AreaFold>
        )}
        {data.requests.length > 0 && (
          <>
            <h3>{t("work-tasks.detail.requests")}</h3>
            <ul className="plain small">
              {data.requests.map((x: Row) => (
                <li key={x.id}><ItemLink to={`/inbox?req=${x.id}`}>{x.title}</ItemLink> <Badge value={x.status} /></li>
              ))}
            </ul>
          </>
        )}
        {data.runs.length > 0 && (
          <details className="sect">
            <summary>{t("work-tasks.detail.runs", { n: data.runs.length, cost: usd(data.runs.reduce((a: number, x: Row) => a + x.cost_usd, 0)) })}</summary>
            <ul className="plain small">
              {data.runs.map((x: Row) => (
                <li key={x.id} className="row wrap tight">
                  <Link to={`/audit/runs/${x.id}`} className="mono">#{x.id}</Link>
                  <Agent id={x.agent} /> · {purposeLabel(x.purpose)} · {t("work-tasks.detail.attempt", { n: x.attempt })} ·
                  <Badge value={x.status === "ok" ? t("work-tasks.detail.run_ok") : t("work-tasks.detail.run_failed")} tone={x.status === "ok" ? "ok" : "bad"} raw /> {dur(x.duration_ms)} · {usd(x.cost_usd)}
                </li>
              ))}
            </ul>
          </details>
        )}
        <h3>{t("work-tasks.detail.thread")}</h3>
        <Thread data={data} onChange={reload} body={draft} setBody={setDraft} />
      </div>
    </section>
  );
}

/** Cancel a task that has not finished: said before it happens, with the safe button focused first and an optional
 * reason the record keeps. */
function CancelTask({ task, onCancel, onDone }: { task: Row; onCancel: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await post(`/tasks/${task.id}/cancel`, { reason: reason.trim() || null });
      announce(t("work-tasks.cancel.done"));
      onDone();
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }
  return (
    <ConfirmBox tone="danger" confirmLabel={t("work-tasks.cancel.confirm")} busy={busy} error={error} onConfirm={go} onCancel={onCancel}>
      <p>{t("work-tasks.cancel.what")}</p>
      <label className="wk-reason">
        <span className="small muted">{t("work-tasks.cancel.reason")}</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("work-tasks.cancel.reason_placeholder")} />
      </label>
    </ConfirmBox>
  );
}

const ORIGIN: Record<string, PlainKey> = {
  owner: "work-tasks.origin.owner", realign: "work-tasks.origin.realign", decision: "work-tasks.origin.decision",
};

/** Who put the task on the board: the owner, a directive, a decision, or a CEO planning round. */
function origin(task: Row): string {
  if (ORIGIN[task.origin]) return t(ORIGIN[task.origin]);
  if (task.origin !== "follow_up" && !task.plan_round) return t("work-tasks.origin.plan0");
  return t(task.origin === "follow_up" ? "work-tasks.origin.follow_up" : "work-tasks.origin.plan", { n: task.plan_round });
}

function TaskLink({ d, pid }: { d: Row; pid: number }) {
  return (
    <span className="tl-link">
      <Link to={`/projects/${pid}/tasks?task=${d.id}`}>{d.title}</Link> <Badge value={d.status} />
    </span>
  );
}

/** What a comment does now, by the task's state: the agent reads it on its next attempt, on a retry, or never (it
 * stays on the record), and where follow-up work goes instead. */
function commentHelp(data: any): ReactNode {
  const task = data.task;
  const p = data.project;
  if (["PROPOSED", "READY", "RUNNING", "REWORK", "REVIEW"].includes(task.status)) return t("work-tasks.thread.live");
  if (task.status === "FAILED") {
    if (data.can.retry) return t("work-tasks.thread.failed_retry");
    if (!["DONE", "ARCHIVED"].includes(p.status) && !CLOSED_PHASES.includes(p.phase)) {
      return <>{data.failure?.says} <Link to={`/projects/${p.id}/settings#budget`}>{t("work-tasks.fail.budget_link")}</Link></>;
    }
    return t("work-tasks.detail.no_retry");
  }
  return data.can.add
    ? tx("work-tasks.thread.done_link", { link: <Link to={`/projects/${p.id}/tasks`}>{t("work-tasks.thread.done_link_label")}</Link> })
    : t("work-tasks.thread.done_closed");
}

/** Activity and owner comments in one time order; a comment reaches the agent on its next attempt. */
function Thread({ data, onChange, body, setBody }: { data: any; onChange: () => void; body: string; setBody: (v: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const task = data.task;
  const events: Row[] = data.activity.filter((e: Row) => e.verb !== "comment");
  const comments: Row[] = data.comments.map((c: Row) => ({ id: `c${c.id}`, at: c.created_at, comment: c }));
  const merged: Row[] = [...events.map((e) => ({ id: `e${e.id}`, at: e.at, event: e })), ...comments]
    .sort((a, b) => String(a.at).localeCompare(String(b.at)));
  const reaches = ["PROPOSED", "READY", "RUNNING", "REWORK", "REVIEW"].includes(task.status) || (task.status === "FAILED" && data.can.retry);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await post(`/tasks/${task.id}/comments`, { body });
      setBody("");
      onChange();
    } catch (x: any) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="thread">
      {merged.length === 0 ? <p className="small muted">{t("work-tasks.thread.none")}</p> : (
        <ol className="feed">
          {merged.map((m) => m.comment ? (
            <li key={m.id} className="feed-i comment">
              <span className="dot info" aria-hidden="true" />
              <div className="feed-b">
                <div className="pre-wrap">{m.comment.body}</div>
                <div className="small muted">{tx("work-tasks.thread.by_owner", { when: <Time v={m.at} /> })}</div>
              </div>
            </li>
          ) : <ActivityItem key={m.id} e={m.event} showProject={false} />)}
        </ol>
      )}
      {data.can.comment && (
        <form className="form" onSubmit={submit}>
          <label className="sr-only" htmlFor={`c-${task.id}`}>{t("work-tasks.thread.label")}</label>
          <textarea id={`c-${task.id}`} rows={2} value={body} onChange={(e) => setBody(e.target.value)}
            placeholder={reaches ? t("work-tasks.thread.placeholder_live") : t("work-tasks.thread.placeholder_done")}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(e as unknown as FormEvent); }} />
          <div className="row wrap">
            <button type="submit" className="btn-ghost" disabled={busy || !body.trim()}>{busy ? t("work-tasks.thread.saving") : t("work-tasks.thread.send")}</button>
            <span className="small muted">{commentHelp(data)}</span>
            {err && <span className="act-msg bad" role="alert">{err}</span>}
          </div>
        </form>
      )}
    </div>
  );
}

function EditTask({ task, onDone }: { task: Row; onDone: () => void }) {
  const [title, setTitle] = useState(task.title);
  const [objective, setObjective] = useState(task.objective);
  const [crit, setCrit] = useState((task.acceptance_criteria ?? []).join("\n"));
  const [owner, setOwner] = useState(task.owner);
  const { data: owners } = useBusinessLive<{ owners: string[] }>("/tasks?limit=1", []);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await post(`/tasks/${task.id}/edit`, { title, objective, owner, acceptance_criteria: crit.split("\n") });
      onDone();
    } catch (x: any) {
      setErr(x.message);
      setBusy(false);
    }
  }
  return (
    <form className="form edit-box" onSubmit={submit}>
      <div className="field"><label htmlFor="et-title">{t("work-tasks.form.title")}</label><input id="et-title" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
      <div className="field">
        <label htmlFor="et-owner">{t("work-tasks.form.owner")}</label>
        <select id="et-owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
          {(owners?.owners ?? [task.owner]).map((o) => <option key={o} value={o}>{agentName(o)}</option>)}
        </select>
      </div>
      <div className="field"><label htmlFor="et-obj">{t("work-tasks.form.goal")}</label><textarea id="et-obj" rows={4} value={objective} onChange={(e) => setObjective(e.target.value)} /></div>
      <div className="field"><label htmlFor="et-crit">{t("work-tasks.form.criteria")} <span className="muted">{t("work-tasks.form.one_per_line")}</span></label><textarea id="et-crit" rows={3} value={crit} onChange={(e) => setCrit(e.target.value)} /></div>
      <div className="row wrap">
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? t("work.saving") : t("work-tasks.form.save")}</button>
        <button type="button" className="btn-ghost" onClick={onDone} disabled={busy}>{t("work-tasks.form.discard")}</button>
        {err && <span className="act-msg bad" role="alert">{err}</span>}
      </div>
    </form>
  );
}

function NewTask({ projectId, owners, tasks, onDone }: { projectId: number; owners: string[]; tasks: Row[]; onDone: (id?: number) => void }) {
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [crit, setCrit] = useState("");
  const [owner, setOwner] = useState(owners.includes("research") ? "research" : owners[0]);
  const [deps, setDeps] = useState<number[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const created = await post<Row>(`/projects/${projectId}/tasks`, { title, objective, owner, depends_on: deps,
        acceptance_criteria: crit.split("\n") });
      onDone(created.id);
    } catch (x: any) {
      setErr(x.message);
      setBusy(false);
    }
  }
  const done = tasks.filter((task) => task.status !== "CANCELLED" && task.status !== "REPLACED");
  return (
    <Box className="wk-new">
      <h2 className="wk-new-h">{t("work-tasks.add")}</h2>
      <form className="form" onSubmit={submit}>
        <div className="row wrap">
          <div className="field grow"><label htmlFor="nt-title">{t("work-tasks.form.title")}</label><input id="nt-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="field">
            <label htmlFor="nt-owner">{t("work-tasks.form.owner")}</label>
            <select id="nt-owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
              {owners.map((o) => <option key={o} value={o}>{agentName(o)}</option>)}
            </select>
          </div>
        </div>
        <div className="field"><label htmlFor="nt-obj">{t("work-tasks.form.goal")}</label><textarea id="nt-obj" rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} placeholder={t("work-tasks.form.goal_placeholder")} /></div>
        <div className="field"><label htmlFor="nt-crit">{t("work-tasks.form.criteria")} <span className="muted">{t("work-tasks.form.one_per_line_default")}</span></label><textarea id="nt-crit" rows={2} value={crit} onChange={(e) => setCrit(e.target.value)} /></div>
        {done.length > 0 && (
          <div className="field">
            <label htmlFor="nt-deps">{t("work-tasks.form.depends")} <span className="muted">{t("work-tasks.form.depends_help")}</span></label>
            <select id="nt-deps" multiple size={Math.min(5, done.length)} value={deps.map(String)}
              onChange={(e) => setDeps([...e.target.selectedOptions].map((o) => Number(o.value)))}>
              {done.map((task) => <option key={task.id} value={task.id}>{task.title} ({label(task.status)})</option>)}
            </select>
          </div>
        )}
        <p className="small muted">{t("work-tasks.form.help")}</p>
        <div className="row wrap">
          <button type="submit" className="btn-primary" disabled={busy || !title.trim() || !objective.trim()}>{busy ? t("work-tasks.form.adding") : t("work-tasks.add")}</button>
          <button type="button" className="btn-ghost" onClick={() => onDone()} disabled={busy}>{t("common.close")}</button>
          {err && <span className="act-msg bad" role="alert">{err}</span>}
        </div>
      </form>
    </Box>
  );
}
