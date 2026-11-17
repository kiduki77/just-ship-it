import { FormEvent, Fragment, lazy, ReactNode, Suspense, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { post, Row, useLive } from "../api";
import { forItem, ItemScope, useBusinessLive, useFollowBusiness } from "../business";
import { ActivityList } from "../components/activity";
import { AreaFold, Fit } from "../components/strengths";
import { checkTone, EvidenceSummary, SERVER_CHECKS, serverRecord, shownKind, sourceName, unbacked } from "../components/evidence";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { Icon } from "../components/icons";
import { Box, Chip, Hint, NotFound, PageHead, RouteTabs, Row as ListRow, Rows, Section, Status, Tone, Toolbar } from "../components/kit";
import { ItemLink, PanelLink } from "../components/panel";
import {
  budgetStop, BudgetForm, devStop, devStopped, devWaiting, DirectiveForm, finished, hasReport, LifecycleStrip, RunControls, StageRef, StageTag,
  stageLabel, stageOf, stageWrapping, toolStatus, useStages, workerDownKey,
} from "../components/project";
import {
  ActionButton, Agent, EvRefs, Folded, KindTag, landOn, leadLine, leadOf, Loading, Markdown, RefLink, rid, Skeleton, tid, Time, toneOf, usd,
  Verification,
} from "../components/ui";
import { LANGS, lookupIn, PlainKey, t, tx } from "../i18n";
import { agentName, artifactKindLabel, decisionLabel, jobLabel, label } from "../labels";
import { ActivityLog } from "./Audit";
import Engineering from "./Engineering";
import Tasks from "./Tasks";
import "./misc.css";

// the path view carries React Flow and Dagre, so it loads when the Path tab opens and not with the console
const PathTab = lazy(() => import("../components/PathView").then((m) => ({ default: m.PathTab })));
import "./project-goal.css";

const TABLES = ["projects", "tasks", "decisions", "meetings", "agent_requests", "artifacts", "activity_events", "owner_tasks"];
const ACTIVE = ["RUNNING", "REVIEW", "REWORK"];

/** A status code as a chip, tone from the shared status table. `held` replaces a running word while no worker runs. */
function St({ v, held }: { v: string; held?: string | null }) {
  return <Chip tone={held ? "warn" : toneOf(v) as Tone}>{held ?? label(v)}</Chip>;
}

const toneForDecision = (d: string): Tone =>
  ({ adopt: "ok", conditional: "warn", defer: "warn", reject: "bad" } as Record<string, Tone>)[d] ?? "muted";

function DecisionChip({ d }: { d: string }) {
  return <Chip tone={toneForDecision(d)}>{decisionLabel(d)}</Chip>;
}

export default function ProjectPage() {
  const { id } = useParams();
  // a project's address is its number: anything else is no project, said as the not-found page says it
  const valid = /^\d+$/.test(id ?? "");
  const { data, error, failure, reload } = useLive<any>(valid ? `/projects/${id}` : null, TABLES);
  useFollowBusiness(data ? (data.project.business_id ?? null) : undefined, `project:${id}`, data?.project.name ?? "");
  // a lookup of this project's counts, asked of its own business: an archived business's projects list only when named
  const { data: list } = useLive<Row[]>(data ? forItem("/projects?include_archived=true", data.project.business_id ?? null) : null,
    ["projects", "agent_requests", "tasks"]);
  const evidence = useLive<Row[]>(valid ? `/projects/${id}/evidence` : null, ["tasks"]);
  const worker = useWorker();
  const stages = useStages(data?.project.business_id ?? null);
  if (!valid) return <NotFound />;
  if (!data) {
    return (
      <div className="owner-page kui mx mx-pj">
        <PageHead title={t("work.project")} />
        {error ? <><Loading error={error} failure={failure} retry={reload} /><p className="small"><Link to="/projects">{t("work.back_to_projects")}</Link></p></> : <Skeleton rows={5} />}
      </div>
    );
  }
  const p = data.project;
  // The list endpoint carries the counts the status line needs (pending, blocking, progress).
  const summary = { ...p, ...(list ?? []).find((x) => x.id === p.id) };
  const stage = p.stage_id ? stages?.get(p.stage_id) ?? null : null;
  const base = `/projects/${id}`;
  // One source for "waiting on you": the project's own pending requests, the same rows the sidebar and decisions count.
  const pending: Row[] = data.requests.filter((r: Row) => r.status === "PENDING");
  const blocking = pending.filter((r) => r.blocking);
  const live: Row[] = data.tasks.filter((t: Row) => t.status !== "CANCELLED" && t.status !== "REPLACED");
  const done = live.filter((t) => t.status === "DONE").length;
  const active = live.filter((t) => ACTIVE.includes(t.status)).length;
  const failed = live.filter((t) => t.status === "FAILED").length;
  const ev = evidence.data;
  const { status, next } = projectStatus(summary, pending, blocking, { done, total: live.length, active, failed }, base, worker.down, stage);
  const phase = p.status === "DONE" && !finished(summary) ? stageLabel(summary) : p.phase ? label(p.phase) : null;
  const tabs = [
    { to: base, label: t("work.tab.overview") },
    { to: `${base}/tasks`, label: t("work.tab.tasks"), // a red count is the failed tasks, never the total: "6" in red would read as six failures
    count: (failed || live.length) || undefined, tone: failed ? "bad" as const : undefined },
    { to: `${base}/decisions`, label: t("work.tab.decisions"), count: pending.length || undefined, tone: pending.length ? (blocking.length ? "bad" as const : "warn" as const) : undefined },
    { to: `${base}/path`, label: t("path.tab") },
    { to: `${base}/evidence`, label: t("work.tab.evidence"), count: ev?.length || undefined },
    { to: `${base}/docs`, label: t("work.tab.docs"), count: data.artifacts.length || undefined },
    { to: `${base}/dev`, label: t("work.tab.dev") },
    { to: `${base}/activity`, label: t("work.tab.activity") },
    { to: `${base}/settings`, label: t("work.tab.settings") },
  ];
  return (
    <div className="owner-page kui mx mx-pj">
      <PageHead
        title={p.name}
        status={status}
        sub={
          <span className="mx-pj-meta">
            {stage && <StageTag stage={stage} />}
            {phase && p.status !== "ARCHIVED" && <span>{t(p.status === "FAILED" ? "work.meta.phase_stopped" : "work.meta.phase", { phase })}</span>}
            {p.plan_round ? <span>{t("work.meta.round", { n: p.plan_round })}</span> : null}
            {p.created_at && <span>{tx("work.meta.created", { when: <Time v={p.created_at} /> })}</span>}
          </span>
        }
        actions={<>
          <Link className="btn-ghost" to={`/crews?project_id=${p.id}`}><Icon name="users" />{t("work.team")}</Link>
          <RunControls p={summary} onChange={reload} quiet={!!next} />
          {next}
        </>} />
      <RouteTabs label={t("work.tabs")} items={tabs} />
      <ItemScope value={p.business_id ?? null}>
        <Routes>
          <Route index element={<Overview data={data} summary={summary} pending={pending} stage={stage} onChange={reload} />} />
          <Route path="tasks" element={<Tasks projectId={p.id} />} />
          <Route path="decisions" element={<Decisions data={data} pending={pending} />} />
          <Route path="path" element={<Suspense fallback={<Skeleton rows={3} />}><PathTab projectId={p.id} businessId={p.business_id ?? null} /></Suspense>} />
          <Route path="evidence" element={<EvidenceLedger projectId={p.id} live={evidence} decisions={data.decisions} />} />
          <Route path="docs" element={<Docs data={data} />} />
          <Route path="dev" element={<Engineering projectId={p.id} />} />
          <Route path="activity" element={<ActivityLog projectId={p.id} />} />
          <Route path="settings" element={<ProjectSettings p={p} pending={pending} ask={pending.find((r) => r.action_kind === "budget.raise" && r.estimated_cost_usd > 0)} onChange={reload} />} />
          {/* old tab addresses */}
          <Route path="board" element={<Keep to="tasks" />} />
          <Route path="meetings" element={<Keep to="decisions" />} />
          <Route path="report" element={<Keep to="docs" />} />
          <Route path="artifacts" element={<Keep to="docs" />} />
          <Route path="engineering" element={<Keep to="dev" />} />
          <Route path="team" element={<Navigate to={`/crews?project_id=${p.id}`} replace />} />
          <Route path="approvals" element={<Navigate to={`/inbox?project=${p.id}`} replace />} />
        </Routes>
      </ItemScope>
    </div>
  );
}

/** Redirect to a sibling tab, keeping ?task= / ?ev= style deep links. */
function Keep({ to }: { to: string }) {
  const { search } = useLocation();
  const { id } = useParams();
  return <Navigate to={`/projects/${id}/${to}${search}`} replace />;
}

type GoalBlock = { head?: string; items: string[] };

/**
 * Goals arrive as one run-on text: paragraphs split by blank lines, "라벨: …" lead-ins, and " / " or ";" between the
 * points. Each paragraph becomes a short lead line and a list; a bare long sentence splits at its sentence ends.
 */
function goalBlocks(text: string): GoalBlock[] {
  return text.split(/\n\s*\n/).map((para) => para.trim()).filter(Boolean).map((para) => {
    let parts = para.split(/\s+\/\s+|\s*\n\s*|;\s+/).map((x) => x.trim()).filter(Boolean);
    // Text with no "/" or line breaks of its own is one run-on: break it at the sentence ends.
    if (!/\s\/\s|\n/.test(para)) parts = parts.flatMap((x) => (x.length > 60 ? x.split(/(?<=[.!?。])\s+/) : [x])).filter(Boolean);
    const lead = parts[0]?.match(/^([^:：.]{1,16})[:：]\s*(.+)$/s);
    if (lead && (parts.length > 1 || lead[2].length > 24)) return { head: lead[1], items: [lead[2], ...parts.slice(1)] };
    return { items: parts };
  });
}

/** A goal runs to a page of text: the first point reads in full, the rest is one click away. */
function Goal({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const blocks = goalBlocks(text);
  const points = blocks.reduce((n, b) => n + b.items.length, 0);
  const long = text.length > 180 && points > 2;
  const shown = open || !long ? blocks : [{ head: blocks[0].head, items: blocks[0].items.slice(0, 2) }];
  const hidden = long && !open && (blocks.length > 1 || blocks[0].items.length > 2);
  return (
    <div className="pj-goal">
      {shown.map((b, i) => (
        <div key={i} className="pj-goal-b">
          {b.head && <p className="pj-goal-h">{b.head}</p>}
          {b.items.length === 1 && !b.head
            ? <p>{b.items[0]}</p>
            : <ul>{b.items.map((x, j) => <li key={j}>{x}</li>)}</ul>}
        </div>
      ))}
      {long && (hidden || open) && (
        <button type="button" className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? t("work.goal.less") : t("work.goal.all")}
        </button>
      )}
    </div>
  );
}

/** A merged build is not a finished project until people can open it (engineering/usable.py on the server). */
function mergedStatus(u: Row, pending: Row[], waiting: ReactNode, decide: ReactNode, base: string, report_: boolean): { status: Status; next: ReactNode } {
  // Other decisions waiting are still said, after the build's own line.
  const others = pending.some((r) => r.id !== u.request_id) ? <> {waiting}</> : null;
  const report = others ? decide : <Link className="btn-ghost" to={`${base}/docs`}>{report_ ? t("work.read_report") : t("work.view_docs")}</Link>;
  if (u.state === "tool") return { status: { tone: "ok", text: <>{toolStatus(u)}{others}</> }, next: report };
  if (u.state === "decide") return { status: { tone: "warn", text: <>{t("work.status.merged_decide")}{others}</> }, next: decide };
  if (u.state === "todo") {
    return {
      status: { tone: "warn", text: <>{t("work.status.merged_todo")}{others}</> },
      next: <ItemLink className="btn-primary" to={`/my-tasks?ot=${u.task_id}`}>{t("work.go_todo")}<Icon name="arrow" /></ItemLink>,
    };
  }
  if (u.state === "later" || u.state === "closed") {
    return {
      status: { tone: "warn", text: <>{t(u.state === "later" ? "work.status.merged_later" : "work.status.merged_closed")}{others}</> },
      next: <ActionButton className="btn-primary small" run={() => post(`/dev-jobs/${u.job_id}/use-again`, {})}>{t("work.use_again")}</ActionButton>,
    };
  }
  // Only a web address becomes a link: the one the owner typed on the to-do is theirs, never a script.
  const address = /^https?:\/\//i.test(u.address ?? "")
    ? <a href={u.address} target="_blank" rel="noreferrer">{u.address}</a> : u.address;
  const text = !u.address ? t("work.status.merged_usable")
    : tx(u.how === "local" ? "work.status.merged_local" : "work.status.merged_live", { address });
  return { status: { tone: "ok", text: <>{text}{others}</> }, next: report };
}

/**
 * The project in one sentence and the next thing the owner can do: stopped or failed first, then that no worker is
 * running (nothing moves however the rows read), then decisions waiting on the owner with any failed tasks, then the
 * work itself. A budget near its cap is said only from 70% on. `stage` is the cycle stage the project was made for.
 */
function projectStatus(p: Row, pending: Row[], blocking: Row[], c: { done: number; total: number; active: number; failed: number }, base: string,
  workerDown: boolean, stage: StageRef | null): { status: Status; next: ReactNode } {
  const budget = p.budget_usd ?? 0;
  const used = budget ? ((p.spent_usd ?? 0) + (p.reserved_usd ?? 0)) / budget : 0;
  const cap = used >= 0.7
    ? tx("work.status.cap", { budget: usd(budget), pct: Math.round(used * 100), link: <Link to={`${base}/settings#budget`}>{t("work.budget.change")}</Link> })
    : null;
  // the most urgent decision opens over this tab; the decisions tab holds the rest
  const urgent = blocking[0] ?? pending[0];
  const decide = urgent
    ? <PanelLink className="btn-primary" to={`/inbox?req=${urgent.id}`} target={{ key: "req", id: urgent.id }}>{t("work.go_decide")}<Icon name="arrow" /></PanelLink>
    : <Link className="btn-primary" to={`${base}/decisions`}>{t("work.go_decide")}<Icon name="arrow" /></Link>;
  const waiting = pending.length
    ? <>{tx("work.status.decisions", { n: <b>{pending.length}</b> })}{blocking.length ? t("work.status.blocking", { n: blocking.length }) : null}</>
    : null;
  const failedTasks = c.failed ? <Link to={`${base}/tasks?group=blocked`}>{t("work.status.failed_tasks", { n: c.failed })}</Link> : null;
  const paused = (p.paused || p.context?.paused) && ["CREATED", "RUNNING", "WAITING_HUMAN"].includes(p.status);
  const reason = p.context?.paused?.reason;
  const down = workerDown && ["CREATED", "RUNNING", "WAITING_HUMAN"].includes(p.status)
    ? <>{t(workerDownKey(p, c.done + c.active > 0))} <Fix text={`\`${WORKER_FIX}\`.`} /></> : null;
  if (p.status === "ARCHIVED") return { status: { text: t("work.line.archived") }, next: null };
  if (p.status === "PAUSED") {
    const text = reason ? t("work.status.paused_because", { reason }) : t("work.status.paused");
    return { status: { tone: "warn", text: <>{text} {waiting}</> }, next: pending.length ? decide : null };
  }
  if (paused && stageWrapping(stage)) {
    return { status: { tone: "warn", text: <>{t("work.line.stage_wrapping")} {waiting}</> }, next: <Link className="btn-ghost" to="/">{t("work.line.stage_open")}</Link> };
  }
  if (paused) {
    return { status: { tone: "warn", text: <>{reason ? t("work.line.pausing_because", { reason }) : t("work.line.pausing")} {waiting}</> }, next: pending.length ? decide : null };
  }
  if (budgetStop(p)) {
    // the decision the budget stop is waiting on, so the stop and the way on read as one thing
    const ask = pending.find((r) => r.action_kind === "budget.raise" || r.action_kind === "budget.spend" || r.type === "RESOURCE");
    const raise = pending.find((r) => r.action_kind === "budget.raise" && r.estimated_cost_usd > 0);
    return {
      status: {
        tone: "warn",
        text: <>
          {t("work.status.budget_stop", { budget: usd(budget) })}
          {ask ? <> {tx("work.status.budget_ask", { link: <PanelLink target={{ key: "req", id: ask.id }} to={`/inbox?req=${ask.id}`}>{ask.title}</PanelLink> })}</> : null}
        </>,
      },
      next: raise
        ? <PanelLink className="btn-primary" target={{ key: "req", id: raise.id }} to={`/inbox?req=${raise.id}`}>{t("home.status.budget_ask_action", { amount: usd(raise.estimated_cost_usd) })}</PanelLink>
        : <Link className="btn-primary" to={`${base}/settings#budget`}>{t("work.budget.raise_resume")}</Link>,
    };
  }
  if (p.status === "FAILED") {
    const why = String(p.error ?? "").split("\n")[0].replace(/^Stopped:\s*/i, "").slice(0, 160);
    const stopped = why ? tx("work.status.failed_because", { why: <span className="err-line">{why}</span> }) : t("work.status.failed");
    return { status: { tone: "bad", text: <>{stopped} {t("work.status.rerun")}{waiting ? <> {waiting}</> : null}</> }, next: pending.length ? decide : null };
  }
  if (devStopped(p)) {
    const stop = devStop(p);
    return {
      status: {
        tone: stop?.budget ? "warn" : "bad",
        text: <>{t("work.status.analysis_done")} {stop?.text ?? t("work.status.dev_stopped")}{waiting ? <> {waiting}</> : null}</>,
      },
      next: <Link className="btn-primary" to={stop?.to ?? `${base}/dev`}>{stop?.label ?? t("work.dev.view")}<Icon name="arrow" /></Link>,
    };
  }
  if (stageOf(p) === "DEV_ACTIVE") {
    // a build under way needs the worker too: say it waits for it, as the project's chip does
    const building = workerDown ? <>{t("work.line.running_down")} <Fix text={`\`${WORKER_FIX}\`.`} /></> : t("work.line.dev_active");
    return { status: { tone: workerDown ? "warn" : "run", text: <>{building}{waiting ? <> {waiting}</> : null}</> },
      next: pending.length ? decide : <Link className="btn-ghost" to={`${base}/dev`}>{t("work.dev.view")}</Link> };
  }
  if (devWaiting(p)) {
    return { status: { tone: "warn", text: <>{t("work.line.dev_delivered")}{waiting ? <> {waiting}</> : null}</> },
      next: <Link className="btn-primary" to={`${base}/dev`}>{t("work.dev.view")}<Icon name="arrow" /></Link> };
  }
  if (p.dev_use) return mergedStatus(p.dev_use, pending, waiting, decide, base, hasReport(p));
  if (p.status === "DONE") {
    if (pending.length) return { status: { tone: "warn", text: tx("work.status.done_pending", { n: <b>{pending.length}</b> }) }, next: decide };
    return hasReport(p)
      ? { status: { tone: "ok", text: t("work.status.done") }, next: <Link className="btn-primary" to={`${base}/docs`}>{t("work.read_report")}</Link> }
      : { status: { tone: "ok", text: t("work.status.done_no_report") }, next: <Link className="btn-ghost" to={`${base}/docs`}>{t("work.view_docs")}</Link> };
  }
  // the worker comes first: decisions and tasks below still need it to move once answered
  if (down) {
    return { status: { tone: "warn", text: <>{down}{waiting ? <> {waiting}</> : null}{failedTasks ? <> {failedTasks}</> : null}{cap}</> }, next: pending.length ? decide : null };
  }
  if (pending.length) return { status: { tone: blocking.length ? "bad" : "warn", text: <>{waiting}{failedTasks ? <> {failedTasks}</> : null}{cap}</> }, next: decide };
  if (p.status === "CREATED") return { status: { tone: cap ? "warn" : "muted", text: <>{t("work.line.created")}{cap}</> }, next: null };
  const work = c.total
    ? c.active
      ? t("work.status.work_active", { done: c.done, total: c.total, active: c.active })
      : t("work.status.work_idle", { done: c.done, total: c.total })
    : t("work.status.planning");
  return {
    status: {
      tone: c.failed ? "bad" : cap ? "warn" : "run",
      text: <>{failedTasks ? <>{failedTasks} </> : null}{work}{cap}</>,
    },
    next: null,
  };
}

/** Decisions waiting on the owner in this project, each opened beside the current page. */
function WaitingDecisions({ pending, max }: { pending: Row[]; max?: number }) {
  const shown = max ? pending.slice(0, max) : pending;
  return (
    <Box pad={false} className="pj-waiting">
      <Rows label={t("work.banner.waiting")}>
        {shown.map((r) => (
          <ListRow key={r.id} to={`/inbox?req=${r.id}`} panel={{ key: "req", id: r.id }}
            lead={<Chip tone={r.blocking ? "bad" : "warn"}>{r.blocking ? t("work.dec.blocking") : t("work.dec.waiting")}</Chip>}
            title={r.title} end={<Time v={r.created_at} />} />
        ))}
      </Rows>
      {max && pending.length > max && <p className="box-foot small"><Link to="decisions">{t("work.banner.more", { n: pending.length - max })}</Link></p>}
    </Box>
  );
}

function Overview({ data, summary, pending, stage, onChange }: { data: any; summary: Row; pending: Row[]; stage: StageRef | null; onChange: () => void }) {
  const p = data.project;
  const worker = useWorker();
  const plan = p.plan ?? {};
  const active: Row[] = data.tasks.filter((t: Row) => ACTIVE.includes(t.status));
  const decided: Row[] = data.decided;
  const byId = Object.fromEntries(data.decisions.map((d: Row) => [d.id, d]));
  const { data: feed } = useBusinessLive<any>(`/activity?project_id=${p.id}&limit=10`, ["activity_events"]);
  // the palette's "Give a project an instruction" opens `#d-<id>`: the instruction box
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    const f = requestAnimationFrame(() => landOn(decodeURIComponent(hash.slice(1)), "center"));
    return () => cancelAnimationFrame(f);
  }, [hash]);
  const folds: [PlainKey, string[]][] = [
    ["work.plan.success", plan.success_criteria ?? []], ["work.plan.uncertain", plan.key_uncertainties ?? []],
    ["work.plan.out", plan.out_of_scope ?? []],
  ];
  return (
    <div className="cols cols-main">
      <div className="mx-col">
        {pending.length > 0 && (
          <Section title={t("work.banner.waiting")} count={pending.length} actions={<Link className="small" to="decisions">{t("work.overview.decided_all")}</Link>}>
            <WaitingDecisions pending={pending} max={3} />
          </Section>
        )}
        <Guidance p={p} onChange={onChange} />
        {stage ? (
          <Section title={t("work.overview.stage_goal")}>
            <Box>
              <p className="mx-flat">{stage.doing ?? stage.title}</p>
              <p className="small mx-flat"><Link to="/business?tab=brief">{t("work.overview.stage_plan")}</Link></p>
            </Box>
          </Section>
        ) : p.goal && (
          <Section title={t("work.overview.project_goal")}>
            <Box><Goal text={p.goal} /></Box>
          </Section>
        )}
        {active.length > 0 && (
          <Section title={t("work.overview.active")} count={active.length} actions={<Link className="small" to="tasks?group=active">{t("work.overview.board")}</Link>}>
            {worker.down && <p className="small warn-t mx-flat">{t("work.overview.active_down")}</p>}
            <Box pad={false}>
              <Rows>
                {active.map((t) => (
                  <ListRow key={t.id} to={`tasks?task=${t.id}`} title={t.title}
                    meta={<Agent id={t.owner} />} end={<St v={t.status} held={worker.running(t.status)} />} />
                ))}
              </Rows>
            </Box>
          </Section>
        )}
        <Section title={t("work.overview.decided")} count={decided.length} actions={<Link className="small" to="decisions">{t("work.overview.decided_all")}</Link>}>
          {decided.length === 0 ? (
            <Hint title={t("work.overview.decided_none")}>{t("work.overview.decided_none_body")}</Hint>
          ) : (
            <Box pad={false}>
              <ul className="ilist mx-stackrows">
                {decided.slice(0, 3).map((x) => (
                  <li key={x.ref}>
                    <div className="grow">
                      <div className="mx-line">
                        {x.kind === "request"
                          ? <PanelLink className="t1" target={{ key: "req", id: x.id }} to={`/inbox?req=${x.id}`} label={x.title}>{x.title}</PanelLink>
                          : <Link className="t1" to={`decisions#dec-${x.id}`} title={x.title}>{x.title}</Link>}
                        {x.kind === "request" ? <St v={x.outcome} /> : <DecisionChip d={x.outcome} />}
                        <span className="small muted nowrap">{agentName(x.by === "owner" ? "owner" : "project_manager")}{x.chosen_option ? ` · ${x.chosen_option}` : ""}</span>
                      </div>
                      {x.kind === "decision" && byId[x.id] && <DecisionEvidence d={byId[x.id]} projectId={p.id} />}
                    </div>
                  </li>
                ))}
              </ul>
            </Box>
          )}
        </Section>
        <Section title={t("work.overview.goal")}>
          <Box>
            {plan.goal_interpretation
              ? <Folded text={plan.goal_interpretation} projectId={p.id} limit={500} />
              : <p className="muted mx-flat">{t("work.overview.goal_none")}</p>}
            {folds.filter(([, xs]) => xs.length).map(([name, xs]) => (
              <details key={name} className="mx-fold">
                <summary>{t(name)} <span className="sec-n">{xs.length}</span></summary>
                <ul>{xs.map((x) => <li key={x}>{x}</li>)}</ul>
              </details>
            ))}
          </Box>
        </Section>
      </div>
      <div className="mx-col">
        <Section title={t("work.stages")}>
          <Box><LifecycleStrip p={summary} /></Box>
        </Section>
        {p.status !== "ARCHIVED" && (
          <Section title={t("work.overview.direct")}>
            <Box><DirectiveForm p={p} onChange={onChange} /></Box>
          </Section>
        )}
        <Section title={t("work.overview.activity")} actions={<Link className="small" to="activity">{t("work.overview.activity_all")}</Link>}>
          <Box className="feed-box">{feed ? <ActivityList events={feed.events} showProject={false} compact empty={t("common.activity.empty")} /> : <Skeleton rows={3} />}</Box>
        </Section>
      </div>
    </div>
  );
}

/** Owner directives and answers, and what the CEO changed because of them. */
function Guidance({ p, onChange }: { p: Row; onChange: () => void }) {
  const answers: Row[] = p.context?.owner_answers ?? [];
  const directives: Row[] = p.context?.directives ?? [];
  const realignments: Row[] = p.context?.realignments ?? [];
  if (!answers.length && !directives.length && !realignments.length) return null;
  return (
    <Section title={t("work.guide.title")} count={directives.length + answers.length}>
      <Box pad={false}>
        <ul className="ilist mx-stackrows">
          {[...directives].reverse().map((d) => (
            <li key={`d${d.n}`}>
              <div className="grow">
                <div className="mx-line"><b>{t("work.guide.directive", { n: d.n })}</b><span className="end"><Time v={d.at} /></span></div>
                <div className="pre-wrap">{d.text}</div>
                <DirectiveRules p={p} d={d} onChange={onChange} />
              </div>
            </li>
          ))}
          {[...realignments].reverse().slice(0, 3).map((r, i) => (
            <li key={`r${i}`}>
              <details className="grow mx-fold" open={i === 0}>
                <summary>
                  {/* a record names its decision or to-do, or (newer ones) the directive number; older records hold a ready label */}
                  <b>{tx("work.guide.realigned", { request: r.directive ? t("work.guide.directive", { n: r.directive })
                    : <RefLink code={String(r.request ?? r.owner_task ?? "")} projectId={p.id} /> })}</b>
                  {" "}<span className="small muted"><Time v={r.at} /></span>
                </summary>
                <dl className="kv compact small">
                  {([["work.guide.cancelled", r.cancelled.map(tid)], ["work.guide.revised", r.revised.map(tid)], ["work.guide.added", r.added.map(tid)],
                    ["work.guide.withdrawn", r.withdrawn.map(rid)]] as [PlainKey, string[]][]).filter(([, v]) => v.length).map(([k, v]) => (
                    <div key={k}><dt>{t(k)}</dt><dd><Markdown text={v.join(", ")} projectId={p.id} /></dd></div>
                  ))}
                </dl>
                <Markdown text={r.assessment} projectId={p.id} />
              </details>
            </li>
          ))}
          {answers.length > 0 && (
            <li>
              <details className="grow mx-fold">
                <summary>{t("work.guide.answers")} <span className="sec-n">{answers.length}</span></summary>
                <ul className="plain mx-answers">
                  {answers.map((a, i) => (
                    <li key={i}>
                      <div className="small">
                        <ItemLink to={`/inbox?req=${String(a.request).replace(/^REQ-/, "")}`} className="mono">{a.request}</ItemLink> {a.title} <St v={a.decision} />
                      </div>
                      {a.answer && <div className="pre-wrap">{a.answer}</div>}
                      {a.note && <div className="pre-wrap muted">{t("work.guide.note", { note: a.note })}</div>}
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          )}
        </ul>
      </Box>
    </Section>
  );
}

/** The rules the server checks every new task and request against for this directive, each removable by the owner,
 * and the ones it refused to apply, with why. */
function DirectiveRules({ p, d, onChange }: { p: Row; d: Row; onChange: () => void }) {
  const rules: Row[] = d.rules ?? [];
  const refused: Row[] = d.refused_rules ?? [];
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!rules.length && !refused.length) return null;
  async function remove(i: number, label: string) {
    setBusy(i);
    setError(null);
    try {
      await post(`/projects/${p.id}/directives/${d.n}/rules/${i}/remove`, { label });
      onChange();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="small sp-next">
      {rules.length > 0 && (
        <>
          <p className="muted">{t("work.rules.head")}</p>
          <ul className="plain">
            {rules.map((r, i) => (
              <li key={`${i}:${r.label}`} className="row wrap tight">
                <span><b>{r.label}</b> <span className="muted">({(r.keywords ?? []).join(" + ")})</span></span>
                <button type="button" className="link small" disabled={busy !== null} onClick={() => remove(i, r.label)}>
                  {busy === i ? t("work.rules.removing") : t("work.rules.remove")}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {refused.length > 0 && (
        <p className="muted">{t("work.rules.refused", { list: refused.map((r) => `${r.label} (${r.why})`).join(" · ") })}</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

/** A decision's support next to the decision: linked evidence, or the EV ids its rationale cites. */
function DecisionEvidence({ d, projectId }: { d: Row; projectId: number }) {
  const linked = d.supporting_evidence_ids ?? [];
  const cited = (d.cited_evidence_ids ?? []).filter((x: string) => !linked.includes(x) && !(d.dissenting_evidence_ids ?? []).includes(x));
  if (linked.length) return <EvidenceSummary ids={linked} projectId={projectId} title={t("work.ev.for")} />;
  return (
    <EvidenceSummary ids={cited} projectId={projectId} title={t("work.ev.cited")}
      note={cited.length ? t("work.ev.cited_note") : undefined} />
  );
}

function Decisions({ data, pending }: { data: any; pending: Row[] }) {
  const p = data.project;
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [hash, data]);
  const decisions: Row[] = [...data.decisions].sort((a: Row, b: Row) => Number(a.status === "SUPERSEDED") - Number(b.status === "SUPERSEDED") || b.id - a.id);
  const answered: Row[] = data.decided.filter((x: Row) => x.kind === "request");
  const reviews: Row[] = p.context?.ceo_reviews ?? [];
  const requests = Object.fromEntries(data.requests.map((r: Row) => [r.id, r]));
  return (
    <div className="cols cols-main">
      <div className="mx-col">
      <Section title={t("work.dec.waiting_head")} count={pending.length}>
        {pending.length ? <WaitingDecisions pending={pending} /> : <p className="small muted mx-flat">{t("work.dec.waiting_none")}</p>}
      </Section>
      {answered.length > 0 && (
        <Section title={t("work.dec.answered")} count={answered.length}>
          <Box pad={false}>
            <ul className="ilist">
              {answered.map((x) => (
                <li key={x.ref}>
                  <PanelLink className="grow t1" target={{ key: "req", id: x.id }} to={`/inbox?req=${x.id}`} label={x.title}>{x.title}</PanelLink>
                  {x.chosen_option && <span className="small muted">{x.chosen_option}</span>}
                  <span className="small muted nowrap">{agentName(x.by === "owner" ? "owner" : "project_manager")}</span>
                  <St v={x.outcome} />
                </li>
              ))}
            </ul>
          </Box>
        </Section>
      )}
      <Section title={t("work.dec.log")} count={decisions.length}>
        {decisions.length === 0 ? <Hint title={t("work.dec.log_none")}>{t("work.dec.log_none_body")}</Hint> : (
          <div className="mx-boxes">
            {decisions.map((d) => {
              const req = d.approval_request_id ? requests[d.approval_request_id] : null;
              return (
                <Box key={d.id} className={d.status === "SUPERSEDED" ? "mx-old" : ""}>
                  <header className="mx-box-h">
                    <h3 id={`dec-${d.id}`} className="scroll-target">{d.title}</h3>
                    <div className="mx-box-a"><DecisionChip d={d.decision} /><St v={d.status} /><span className="small muted"><Time v={d.created_at} /></span></div>
                  </header>
                  {req && (
                    <p className="small mx-flat">{t("work.dec.approval")} <RefLink code={rid(req.id)} /> <St v={req.status} /></p>
                  )}
                  <DecisionEvidence d={d} projectId={p.id} />
                  {(d.dissenting_evidence_ids ?? []).length > 0 && <EvidenceSummary ids={d.dissenting_evidence_ids} projectId={p.id} title={t("work.ev.against")} />}
                  <details className="mx-fold" open={d.status !== "SUPERSEDED" && d.rationale.length < 1500}>
                    <summary>{t("work.dec.reasoning")}</summary>
                    <Markdown text={d.rationale} projectId={p.id} />
                  </details>
                  {d.dissent_summary && (
                    <div className="small mx-said"><b>{t("work.dec.dissent")}</b><Markdown text={d.dissent_summary} projectId={p.id} /></div>
                  )}
                  {d.open_questions.length > 0 && (
                    <details className="mx-fold">
                      <summary>{t("work.dec.open")} <span className="sec-n">{d.open_questions.length}</span></summary>
                      <ul className="mx-md-list">{d.open_questions.map((x: string) => <li key={x}><Markdown text={x} projectId={p.id} /></li>)}</ul>
                    </details>
                  )}
                  {d.action_task_ids.length > 0 && <p className="small mx-flat">{t("work.dec.tasks")} {d.action_task_ids.map((i: number, n: number) => <Fragment key={i}>{n > 0 && " · "}<RefLink code={tid(i)} projectId={p.id} /></Fragment>)}</p>}
                </Box>
              );
            })}
          </div>
        )}
      </Section>
      </div>
      <div className="mx-col">
        <Section title={t("work.dec.meetings")} count={data.meetings.length}>
          {data.meetings.length === 0
            ? <Hint title={t("work.dec.meetings_none")}>{t("work.dec.meetings_none_body")}</Hint>
            : <Meetings data={data} />}
        </Section>
        {reviews.length > 0 && (
          <Section title={t("work.dec.reviews")} count={reviews.length}>
            <Box pad={false}>
              <ul className="ilist mx-stackrows">
                {[...reviews].reverse().map((r, i) => (
                  <li key={i}>
                    <details className="grow mx-fold" open={i === 0}>
                      <summary><b>{t("work.dec.round", { n: r.round })}</b> <Chip tone="info">{label(r.action)}</Chip></summary>
                      <Markdown text={r.assessment} projectId={p.id} />
                    </details>
                  </li>
                ))}
              </ul>
            </Box>
          </Section>
        )}
      </div>
    </div>
  );
}

const KIND_ORDER = ["verified_fact", "unconfirmed", "sourced_claim", "calculation", "assumption", "hypothesis"];
const VERIFY_ORDER = ["supported", "reproduced", "quote_found", "url_ok", "computed", "derived", "transcribed", "weak",
  "unverifiable", "unchecked", "contradicted", "url_unreachable"];
const PAGE = 50;

const evNumber = (x: string | number) => Number(String(x).replace(/^EV-/i, ""));

/** The roles a review folded into the note ("[research_lead] supported: …", services/evidence.apply_review). */
const checkersOf = (note: string | null | undefined): string[] =>
  [...new Set([...(note ?? "").matchAll(/^\[([\w-]+)\] \w+:/gm)].map((m) => m[1]))];

/** Statuses a reviewer sets (services/evidence.apply_review), and those the server sets by fetching or computing. */
const REVIEW_CHECKS = new Set(["supported", "weak", "contradicted"]);
const FETCH_CHECKS = new Set(["url_ok", "quote_found", "url_unreachable", "unverifiable"]);

/** What a claim rests on, in one cell: its source, the server's own check, or plainly nothing. */
function EvSource({ e }: { e: Row }) {
  if (e.source_url) {
    return /^https?:/.test(e.source_url)
      ? <a href={e.source_url} target="_blank" rel="noreferrer">{sourceName(e)}</a>
      : <span className="mono">{e.source_url}</span>;
  }
  if (serverRecord(e)) return <span>{t("work.ev.source_server")}</span>;
  return <span className={unbacked(e) ? "warn-t" : "muted"}>{t("work.ev.source_none")}</span>;
}

/** Who checked a claim: the reviewers its note names, else what its status says about who could have. */
function checkedBy(e: Row): string {
  const named = checkersOf(e.verification_note);
  if (named.length) return named.map((c) => agentName(c)).join(", ");
  if (REVIEW_CHECKS.has(e.verification_status)) return t("work.ev.d.checked_review");
  if (FETCH_CHECKS.has(e.verification_status) || serverRecord(e)) return t("work.ev.d.checked_server");
  if (SERVER_CHECKS.has(e.verification_status)) return t("work.ev.d.checked_unrecorded");
  return t("work.ev.d.checked_none");
}

/** One claim opened: what it says, what it rests on, who wrote and who checked it, and the decisions that cite it. */
function EvDetail({ e, projectId, citing }: { e: Row; projectId: number; citing: Row[] }) {
  return (
    <dl className="ev-detail">
      {e.quote ? <><dt>{t("work.ev.d.quote")}</dt><dd><div className="quote">“{e.quote}”</div></dd></>
        : e.source_url && <><dt>{t("work.ev.d.quote")}</dt><dd className="muted">{t("work.ev.d.quote_none")}</dd></>}
      <dt>{t("work.ev.d.rests_on")}</dt>
      <dd>
        <EvSource e={e} />
        {e.published_at && <span className="muted"> · {e.published_at}</span>}
        {unbacked(e) && <p className="small warn-t mx-flat">{t("work.ev.d.unbacked", { kind: label(e.kind) })}</p>}
      </dd>
      <dt>{t("work.ev.d.written")}</dt>
      <dd><Agent id={e.agent} />{e.task_id && <> · <RefLink code={tid(e.task_id)} projectId={projectId} /></>} · <Time v={e.created_at} /></dd>
      <dt>{t("work.ev.d.checked")}</dt>
      <dd>
        {checkedBy(e)}
        {e.verification_note && <div className="small muted pre-wrap">{e.verification_note}</div>}
      </dd>
      <dt>{t("work.ev.d.cited")}</dt>
      <dd>
        {citing.length ? (
          <ul className="ev-cites">{citing.map((d) => <li key={d.id}><RefLink code={`DEC-${d.id}`} projectId={projectId} /></li>)}</ul>
        ) : <span className="muted">{t("work.ev.d.cited_none")}</span>}
      </dd>
    </dl>
  );
}

function EvidenceLedger({ projectId, live, decisions }: {
  projectId: number; live: { data: Row[] | null; error: string | null; reload: () => void }; decisions: Row[];
}) {
  const { data, error, reload } = live;
  const [params, setParams] = useSearchParams();
  // `?ev=` names one row to show among all of them, opened; the filters live in the address too, so a link keeps them
  const only = Number(params.get("ev")) || null;
  const [opened, setOpened] = useState<Set<number>>(() => new Set(only ? [only] : []));
  const toggle = (id: number) => setOpened((o) => {
    const n = new Set(o);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });
  const citing = (id: number) => decisions.filter((d) => [...(d.supporting_evidence_ids ?? []), ...(d.dissenting_evidence_ids ?? []),
    ...(d.cited_evidence_ids ?? [])].some((x) => evNumber(x) === id));
  const kinds = new Set((params.get("kind") ?? "").split(",").filter(Boolean));
  const verifs = new Set((params.get("check") ?? "").split(",").filter(Boolean));
  const [q, setQ] = useState(params.get("q") ?? "");
  const [shown, setShown] = useState(PAGE);
  // `?from=`: the report, decision or stage summary the owner opened this evidence from; only an in-app address
  const from = params.get("from");
  const back = from && from.startsWith("/") && !from.startsWith("//") ? from : null;
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    if (!only || !data) return;
    const at = data.findIndex((e) => e.id === only);
    if (at >= shown) setShown(at + 1);
    queueMicrotask(() => document.getElementById(`ev-${only}`)?.scrollIntoView({ block: "center" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [only, data]);
  if (!data) return <Loading error={error} retry={reload} />;
  // the kind facet counts and filters what the Kind column shows, so an unconfirmed "verified fact" isn't counted as one
  const valueOf = { kind: shownKind, verification_status: (e: Row) => e.verification_status };
  const count = (key: keyof typeof valueOf, v: string) => data.filter((e) => valueOf[key](e) === v).length;
  const present = (order: string[], key: keyof typeof valueOf) => {
    const seen = new Set<string>(data.map(valueOf[key]));
    return [...order.filter((x) => seen.has(x)), ...[...seen].filter((x) => !order.includes(x))];
  };
  const needle = q.trim().toLowerCase();
  const rows = data.filter((e) => (e.id === only)
    || ((!kinds.size || kinds.has(shownKind(e))) && (!verifs.size || verifs.has(e.verification_status))
      && (!needle || `ev-${e.id} ${e.claim} ${e.quote ?? ""} ${e.source_title ?? ""}`.toLowerCase().includes(needle))));
  const flip = (set: Set<string>, v: string, key: "kind" | "check") => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v);
    else n.add(v);
    setParam(key, [...n].join(","));
    setShown(PAGE);
  };
  const filtered = !!(kinds.size || verifs.size || needle);
  const clear = () => {
    setQ("");
    setShown(PAGE);
    const next = new URLSearchParams(params);
    for (const k of ["kind", "check", "q", "ev"]) next.delete(k);
    setParams(next, { replace: true });
  };
  return (
    <Section title={t("work.ev.title")} count={data.length}>
      {back && <p className="small mx-ev-back"><Link to={back}>← {t("work.ev.back")}</Link></p>}
      {/* the search sits in the view toolbar above the list, where every other list screen keeps it */}
      <Toolbar label={t("work.ev.search")}>
        <label className="search-in">
          <Icon name="search" />
          <span className="sr-only">{t("work.ev.search")}</span>
          <input id="ev-q" type="search" placeholder={t("work.ev.search_placeholder")} value={q}
            onChange={(e) => { setQ(e.target.value); setShown(PAGE); setParam("q", e.target.value); }} />
        </label>
      </Toolbar>
      <Box>
        <p className="muted small mx-flat">
          {tx("work.ev.fact", { fact: <b>{label("verified_fact")}</b> })}
        </p>
        <div className="facets mx-facets">
          <div className="facet" role="group" aria-label={t("work.ev.by_kind")}>
            <span className="facet-l">{t("work.ev.kind")}</span>
            {present(KIND_ORDER, "kind").map((k) => (
              <button key={k} type="button" className={`fchip ${kinds.has(k) ? "on" : ""}`} aria-pressed={kinds.has(k)}
                onClick={() => flip(kinds, k, "kind")}>
                <KindTag kind={k} /> <span className="num">{count("kind", k)}</span>
              </button>
            ))}
          </div>
          <div className="facet" role="group" aria-label={t("work.ev.by_check")}>
            <span className="facet-l">{t("work.ev.check_facet")}</span>
            {present(VERIFY_ORDER, "verification_status").map((v) => (
              <button key={v} type="button" className={`fchip ${verifs.has(v) ? "on" : ""}`} aria-pressed={verifs.has(v)}
                onClick={() => flip(verifs, v, "check")}>
                {/* green only when some row shows it green: a check no row has a record of reads muted here too */}
                <Verification value={v} tone={data.some((e) => e.verification_status === v && checkTone(e) !== "muted") ? undefined : "muted"} />
                {" "}<span className="num">{count("verification_status", v)}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="row between small muted mx-count" aria-live="polite">
          <span>{filtered ? t("work.ev.matches", { n: rows.length }) : t("work.ev.total", { n: rows.length })}{rows.length > shown && t("work.ev.first", { n: shown })}</span>
          {filtered && <button type="button" className="link small" onClick={clear}>{t("work.clear_filters")}</button>}
        </div>
        {rows.length > 0 && (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th scope="col">{t("work.ev.claim")}</th><th scope="col">{t("work.ev.kind")}</th><th scope="col">{t("work.ev.check")}</th>
                  <th scope="col">{t("work.ev.by")}</th><th scope="col">{t("work.ev.source")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((e) => {
                  const open = opened.has(e.id);
                  return (
                    <Fragment key={e.id}>
                      <tr id={`ev-${e.id}`} className={`${only === e.id ? "hl" : ""} ${open ? "ev-open" : ""}`}>
                        <td className="claim">
                          <button type="button" className="ev-claim" aria-expanded={open} aria-controls={`ev-d-${e.id}`} onClick={() => toggle(e.id)}>
                            <Icon name="chevron" size={11} />{e.claim}
                          </button>
                        </td>
                        <td><KindTag kind={shownKind(e)} title={unbacked(e) ? t("work.ev.unbacked_hint", { kind: label(e.kind) }) : undefined} /></td>
                        <td><Verification value={e.verification_status} tone={checkTone(e)} /></td>
                        <td className="small nowrap"><Agent id={e.agent} /></td>
                        <td className="small"><EvSource e={e} /></td>
                      </tr>
                      {open && (
                        <tr className="ev-detail-row" id={`ev-d-${e.id}`}>
                          <td colSpan={5}><EvDetail e={e} projectId={projectId} citing={citing(e.id)} /></td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {rows.length === 0 && (
          <Hint title={data.length ? t("work.ev.no_match") : t("work.ev.none")}>
            {data.length ? t("work.ev.no_match_body") : t("work.ev.none_body")}
          </Hint>
        )}
        {rows.length > shown && (
          <div className="row center"><button type="button" className="btn-ghost" onClick={() => setShown(shown + PAGE)}>{t("common.more", { n: Math.min(PAGE, rows.length - shown) })}</button></div>
        )}
      </Box>
    </Section>
  );
}

function Meetings({ data }: { data: any }) {
  const pid = data.project.id;
  const decisions = Object.fromEntries(data.decisions.map((d: Row) => [d.id, d]));
  return (
    <div className="mx-boxes">
      {data.meetings.map((m: Row) => {
        const d = decisions[m.decision_id];
        const [lead, long] = leadOf(m.agenda);
        return (
          <Box key={m.id}>
            <header className="mx-box-h">
              <h3 id={`m-${m.id}`} className="scroll-target"><span className="mono muted">M-{m.id}</span> {lead}</h3>
              <div className="mx-box-a"><span className="small muted"><Time v={m.created_at} /></span></div>
            </header>
            {long && (
              <details className="mx-fold">
                <summary>{t("work.meeting.agenda")}</summary>
                <Markdown text={m.agenda} projectId={pid} />
              </details>
            )}
            <details className="mx-fold">
              <summary>{t("work.meeting.statements", { n: m.transcript.length })}</summary>
              <div className="transcript">
                {m.transcript.map((x: Row, i: number) => (
                  <div key={i} className={`bubble ${x.stance}`}>
                    <div className="row between small">
                      <b><Agent id={x.agent} /></b>
                      <span className="row tight">{x.round === 0 ? t("work.meeting.opening") : t("work.meeting.rebuttal", { n: x.round })} <St v={x.stance} /></span>
                    </div>
                    <Markdown text={x.statement} projectId={pid} />
                    {x.evidence_ids?.length > 0 && <div className="small muted">{t("term.evidence")} <EvRefs ids={x.evidence_ids} projectId={pid} /></div>}
                    {x.concerns?.length > 0 && <div className="small"><Markdown text={t("work.meeting.concerns", { list: x.concerns.join(" / ") })} projectId={pid} /></div>}
                  </div>
                ))}
              </div>
            </details>
            {d && (
              <div className="mx-outcome">
                <div className="mx-line">
                  <a href={`#dec-${d.id}`} className="strong grow">{t("work.meeting.outcome", { title: d.title })}</a>
                  <DecisionChip d={d.decision} /><St v={d.status} />
                </div>
                {d.dissent_summary && (
                  <div className="small mx-said"><b>{t("work.dec.dissent")}</b><Markdown text={d.dissent_summary} projectId={pid} /></div>
                )}
              </div>
            )}
          </Box>
        );
      })}
    </div>
  );
}


/** A markdown document cut at its `## ` headings: what comes before the first one, then each section. */
function sections(md: string): { head: string; body: string }[] {
  return md.split(/^(?=## )/m).map((part) => {
    const m = part.match(/^## (.+)\r?\n?/);
    return m ? { head: m[1].trim(), body: part.slice(m[0].length) } : { head: "", body: part };
  });
}

// A report keeps the language it was written in, so its summary heading opens in either language.
const OPEN_SECTIONS = new Set(LANGS.map((lang) => lookupIn(lang, "work.report.summary")));

/** The final report as a report: the title, the recommendation and the summary open, every other section folded
 * under its heading so a 50KB report opens on one screen. A section in an area the owner is strong in opens, and one in
 * an area they are new to shows its first sentence under its heading (`fit`, by heading: services/strengths). */
function ReportView({ id, text, projectId, fit }: { id: number; text: string; projectId?: number; fit?: Record<string, Fit> }) {
  const parts = sections(text);
  return (
    <div className="mx-report">
      {parts.map((s, i) => !s.head ? (
        <Markdown key={i} text={s.body} projectId={projectId} />
      ) : OPEN_SECTIONS.has(s.head) ? (
        <section key={i}>
          <h2 className="mx-report-h">{s.head}</h2>
          <Folded text={s.body} projectId={projectId} limit={1500} />
        </section>
      ) : (
        <AreaFold key={i} fit={fit?.[s.head]} className="mx-fold mx-report-sec" head={s.head} lead={leadLine(s.body)} memo={`report:${id}:${i}`}>
          <Markdown text={s.body} projectId={projectId} />
        </AreaFold>
      ))}
    </div>
  );
}

function ArtifactView({ id, projectId }: { id: number; projectId?: number }) {
  const { data, error, reload } = useLive<Row>(`/artifacts/${id}`, []);
  if (!data) return <Loading error={error} retry={reload} />;
  const pid = projectId ?? data.project_id;
  return data.kind === "report" ? <ReportView id={id} text={data.content} projectId={pid} fit={data.fit} />
    : <Markdown text={data.content} projectId={pid} />;
}

const DOC_ORDER = ["report", "plan", "prd", "minutes", "handoff", "task_output"];

function Docs({ data }: { data: any }) {
  const [params, setParams] = useSearchParams();
  const reportId = data.project.context?.report_artifact_id;
  const sel = Number(params.get("doc")) || reportId || null;
  const arts: Row[] = [...data.artifacts].sort((a, b) => DOC_ORDER.indexOf(a.kind) - DOC_ORDER.indexOf(b.kind) || b.id - a.id);
  if (arts.length === 0) return <Hint title={t("work.docs.none")}>{t("work.docs.none_body")}</Hint>;
  const current = arts.find((a) => a.id === sel);
  const kinds = [...DOC_ORDER, ...new Set(arts.map((a) => a.kind).filter((k) => !DOC_ORDER.includes(k)))];
  return (
    <div className="mx-split">
      <Box pad={false} className="mx-side">
        <nav aria-label={t("work.docs.list")}>
          {kinds.map((k) => {
            const g = arts.filter((a) => a.kind === k);
            if (!g.length) return null;
            return (
              <div key={k} className="mx-group">
                <p className="mx-group-h">{artifactKindLabel(k)} <span className="sec-n">{g.length}</span></p>
                <ul className="ilist">
                  {g.map((a) => (
                    <li key={a.id} className={sel === a.id ? "on" : ""}>
                      <button type="button" className="mx-pick" aria-current={sel === a.id}
                        onClick={() => { params.set("doc", String(a.id)); setParams(params, { replace: true }); }}>
                        <span className="t1">{a.title}</span>
                        <span className="t2"><Time v={a.created_at} /></span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </nav>
      </Box>
      {current ? (
        <Box>
          <header className="mx-box-h">
            <h3>{current.title}</h3>
            <div className="mx-box-a"><span className="small muted" title={`registry/${current.path}`}><Time v={current.created_at} /></span></div>
          </header>
          <ArtifactView id={current.id} projectId={data.project.id} />
        </Box>
      ) : <Hint title={t("work.docs.pick")}>{t("work.docs.pick_body")}</Hint>}
    </div>
  );
}

const JOB_STATE: Record<string, PlainKey> = {
  queued: "work.settings.job.queued", gated: "work.settings.job.gated", running: "work.settings.job.running", cancelled: "work.settings.job.cancelled",
};

/** A queued job of this project, as `GET /jobs` lists it: the payload names the project. */
const jobOf = (j: Row, projectId: number) => Number(j.payload?.project_id) === projectId;

/**
 * This project's runs that have not finished or were cancelled, from `GET /jobs`; `cancellable` is the server's word.
 * `waiting` is what archiving would cancel, as the server counts it, so the confirm never misses a run older than the list.
 */
function useProjectRuns(projectId: number, businessId: number | null, archived: boolean) {
  const { data, reload } = useLive<Row[]>(forItem("/jobs?limit=200", businessId), ["jobs"]);
  const { data: archive, reload: recount } = useLive<{ cancels_jobs: number[] }>(archived ? null : `/projects/${projectId}/archive`, ["jobs"]);
  const runs = (data ?? []).filter((j) => jobOf(j, projectId) && ["queued", "gated", "running", "cancelled"].includes(j.status));
  return { runs, waiting: archive?.cancels_jobs.length ?? 0, reload: () => { reload(); recount(); } };
}

/**
 * Runs of this project that have not finished. Each one that has not started can be cancelled here
 * (POST /jobs/:id/cancel), and archiving cancels them all in its one confirm; the row stays, as cancelled, so the record
 * keeps it. A run already under way is listed but not cancellable, and archiving waits for it.
 */
function WaitingRuns({ runs: mine, reload }: { runs: Row[]; reload: () => void }) {
  const open = mine.filter((j) => j.status !== "cancelled");
  if (!open.length && !mine.some((j) => j.status === "cancelled")) return null;
  return (
    <div className="pj-runs">
      <p className="small mx-flat">{open.length ? t("work.settings.runs_head", { n: open.length }) : t("work.settings.runs_none")}</p>
      <Rows label={t("work.settings.runs_label")}>
        {mine.slice(0, 8).map((j) => (
          <ListRow key={j.id} title={<>{jobLabel(j.kind)} <span className="mono muted">#{j.id}</span></>}
            meta={j.status === "cancelled" ? j.last_error : j.status === "running" ? t("work.settings.run_running") : t("work.settings.run_waits")}
            lead={<Chip tone={j.status === "cancelled" ? "muted" : j.status === "running" ? "run" : "warn"}>{t(JOB_STATE[j.status])}</Chip>}
            end={j.cancellable ? (
              <ActionButton className="btn-ghost small" confirm={t("work.settings.run_cancel_confirm")} keep={t("work.settings.run_cancel_keep")}
                what={t("work.settings.run_cancel_what", { id: j.id })} done={t("work.settings.run_cancelled", { id: j.id })}
                run={() => post(`/jobs/${j.id}/cancel`, {}).then(reload)}>
                {t("work.settings.run_cancel")}
              </ActionButton>
            ) : null} />
        ))}
      </Rows>
    </div>
  );
}

function ProjectSettings({ p, pending, ask, onChange }: { p: Row; pending: Row[]; ask?: Row; onChange: () => void }) {
  const [name, setName] = useState(p.name);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const navigate = useNavigate();
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "center" });
  }, [hash]);
  async function rename(e: FormEvent) {
    e.preventDefault();
    try {
      await post(`/projects/${p.id}/rename`, { name });
      setMsg({ ok: true, text: t("work.settings.renamed") });
      onChange();
    } catch (x: any) {
      setMsg({ ok: false, text: x.message });
    }
  }
  const blocking = pending.filter((r) => r.blocking).length;
  const archived = p.status === "ARCHIVED";
  const runs = useProjectRuns(p.id, p.business_id ?? null, archived);
  return (
    <div className="cols cols-2 mx-forms">
      <Section title={t("work.settings.budget")}>
        <Box><BudgetForm p={p} ask={ask} onChange={onChange} readOnly={archived} /></Box>
      </Section>
      <div className="mx-col">
        <Section title={t("work.settings.name")}>
          <Box>
            <form className="row wrap" onSubmit={rename}>
              <label className="sr-only" htmlFor="p-name">{t("work.settings.name_label")}</label>
              <input id="p-name" className="grow" value={name} onChange={(e) => setName(e.target.value)} />
              <button type="submit" className="btn-primary" disabled={!name.trim() || name === p.name}>{t("work.settings.save_name")}</button>
              {msg && <span className={`act-msg ${msg.ok ? "ok" : "bad"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</span>}
            </form>
          </Box>
        </Section>
        {archived ? (
          <Section title={t("work.settings.unarchive")}>
            <Box>
              <p className="small muted mx-flat">{t("work.settings.unarchive_body")}</p>
              <ActionButton className="btn-ghost" run={() => post(`/projects/${p.id}/unarchive`, {}).then(onChange)} done={t("work.settings.unarchived")}>{t("work.settings.unarchive")}</ActionButton>
            </Box>
          </Section>
        ) : (
          <Section title={t("work.settings.archive")} className="mx-danger">
            <Box>
              <p className="small muted mx-flat">{t("work.settings.archive_body")}</p>
              <WaitingRuns runs={runs.runs} reload={runs.reload} />
              <ActionButton className="danger" confirm={t("work.settings.archive_confirm")} keep={t("work.settings.archive_keep")}
                what={<>
                  {pending.length
                    ? (blocking ? t("work.settings.archive_what_blocking", { n: pending.length, m: blocking }) : t("work.settings.archive_what", { n: pending.length }))
                    : t("work.settings.archive_what_none")}
                  {runs.waiting > 0 && <> {t("work.settings.archive_jobs", { n: runs.waiting })}</>}
                </>}
                done={t("work.settings.archived", { name: p.name })}
                run={() => post(`/projects/${p.id}/archive`, { cancel_waiting: true })
                  .then(() => navigate("/projects?tab=archived", { state: { archived: p.name } }))}>
                {t("work.settings.archive_button")}
              </ActionButton>
            </Box>
          </Section>
        )}
      </div>
    </div>
  );
}
