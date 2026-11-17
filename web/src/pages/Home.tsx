import { useCallback, useEffect, useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { agentStatus, failedByOwner } from "../agentState";
import { alertCounts, alertHref, alertText, alertTone } from "../alerts";
import { Row, TZ, qs } from "../api";
import { useBusinessLive, useBusinessSelection } from "../business";
import { ActivityList } from "../components/activity";
import { Fix } from "../components/fix";
import { useWorker, WORKER_FIX } from "../components/frame";
import { CycleRun, CycleSkeleton, CycleWaiting } from "../cycle/Board";
import { BOARD_TONE, NEEDS_OWNER } from "../cycle/format";
import { CycleBoard } from "../cycle/types";
import { useCycleBoard } from "../cycle/useCycleBoard";
import { TaskState } from "../cycle/Work";
import { Icon } from "../components/icons";
import { Chip, FirstRun, Meter, PageHead, Section, Status } from "../components/kit";
import { ItemLink, PanelLink } from "../components/panel";
import { budgetStop, devStop, devStopped } from "../components/project";
import { AgentStopButton } from "../components/stop";
import { ElsewhereLine, KickoffLine, QuietError, SkeletonRows, TodayList } from "../components/today";
import { ago, useShowTest, usd, when } from "../components/ui";
import { t, tx } from "../i18n";
import { agentName, agentTitle } from "../labels";
import { cycleTarget, minutesText, panelTarget, Today, todayCounts, TodayItem } from "../today";
import "./home.css";

const OVERVIEW_TABLES = ["tasks", "dev_jobs", "agent_requests", "projects", "activity_events", "jobs"];
/** The same list and the same tables the sidebar counts failures from, so the two numbers never disagree. */
const ALERT_TABLES = ["projects", "tasks", "dev_jobs", "jobs", "workers"];

function NoBusiness() {
  return (
    <div className="owner-page">
      <PageHead title={t("home.title")} sub={t("home.sub")} />
      <FirstRun>{t("home.firstrun")}</FirstRun>
    </div>
  );
}

type Next = Status & { action?: { to: string; label: string } };

/**
 * A stopped project or development leads, with its one action: work stands still until the owner moves it, which a
 * decision count does not say. A budget stop comes first, since only the owner can raise a budget.
 */
function stopFirst(projects: Row[], decisions: number): Next | null {
  const also = decisions ? tx("home.status.also_decisions", { n: <b>{decisions}</b> }) : null;
  const capped = projects.find(budgetStop);
  if (capped) {
    const ask = capped.budget_ask;
    return {
      tone: "warn",
      text: <>{ask
        ? tx("home.status.budget_stop_ask", { name: <b>{capped.name}</b>, budget: usd(capped.budget_usd), amount: usd(ask.amount_usd) })
        : tx("home.status.budget_stop", { name: <b>{capped.name}</b>, budget: usd(capped.budget_usd) })}{also}</>,
      action: ask ? { to: `/inbox?req=${ask.id}`, label: t("home.status.budget_ask_action", { amount: usd(ask.amount_usd) }) }
        : { to: `/projects/${capped.id}/settings#budget`, label: t("home.status.budget_action") },
    };
  }
  const stops = projects.filter(devStopped).map((p) => ({ p, stop: devStop(p) })).filter((x) => x.stop)
    .sort((a, b) => Number(b.stop!.budget) - Number(a.stop!.budget));
  if (!stops.length) return null;
  const { p, stop } = stops[0];
  return {
    tone: stop!.budget ? "warn" : "bad",
    text: <>{tx("home.status.dev_stop", { name: <b>{p.name}</b>, stop: stop!.text })}{also}</>,
    action: { to: stop!.to, label: stop!.label },
  };
}

interface Turn { decisions: number; blocking: number; mine: number }

/** "3 decisions and 1 to-do wait for you", from the owner's list: the counts the sidebar shows. */
function turnText(turn: Turn) {
  if (turn.decisions && turn.mine) return tx("home.status.both", { d: <b>{t("home.status.n_decisions", { n: turn.decisions })}</b>,
    m: <b>{t("home.status.n_todos", { n: turn.mine })}</b> });
  if (turn.decisions) return tx("home.status.decisions", { n: <b>{turn.decisions}</b> });
  if (turn.mine) return tx("home.status.mine", { n: <b>{turn.mine}</b> });
  return null;
}

/**
 * The dashboard's conclusion, the most serious first: the company stopped by the owner, the company unable to work (no
 * worker), a stopped project, the cycle waiting for the owner, then the owner's own turn and, with nothing to decide,
 * what broke. Every count comes from the owner's list (`today`) and the alert list, the two sources the sidebar reads.
 */
function homeStatus({ today, turn, running, projects, board, broke, workerDown }: {
  today: Today; turn: Turn; running: number; projects: Row[]; board: CycleBoard | null; broke: number; workerDown: boolean;
}): Next {
  if (today.company_stop) {
    const since = <b>{when(today.company_stop.at)}</b>;
    return { tone: "warn", text: running ? tx("home.status.stopped_finishing", { since, n: running }) : tx("home.status.stopped", { since }) };
  }
  const mine = turnText(turn);
  // with the worker off, the second sentence is still the owner's turn: a cycle proposal waiting for them says so itself
  const proposal = board && NEEDS_OWNER.includes(board.state) ? board.sentence : null;
  if (workerDown) return { tone: "bad", text: <>{t("home.status.worker_down")}{(proposal || mine) && <> {proposal || mine}</>}</> };
  const stopped = stopFirst(projects, turn.decisions);
  if (stopped) return stopped;
  const cycle: Next | null = board && board.state !== "none" ? { tone: BOARD_TONE[board.state], text: board.sentence } : null;
  if (cycle && NEEDS_OWNER.includes(board!.state)) return cycle;
  if (mine) {
    return { tone: "warn", text: <>{mine}{turn.blocking ? t("home.status.blocking", { n: turn.blocking }) : null}</> };
  }
  if (broke) return { tone: "bad", text: t("home.status.nothing_but_broke", { n: broke }) };
  if (cycle) return cycle;
  return running ? { tone: "run", text: t("home.status.running", { n: running }) } : { tone: "ok", text: t("home.status.quiet") };
}

const TODAY_SHOWN = 6;
const FEED_SHOWN = 3;

/** What needs the owner: today's share of the owner's list, decisions first, in the order the server ranked it. */
function NeedsYou({ items, today, error, retry }: { items: TodayItem[]; today: Today | null; error: string | null; retry: () => void }) {
  const later = today ? today.total - today.today_count : 0;
  return (
    <Section title={t("home.today.title")} count={today ? items.length : undefined}
      actions={today && today.today_count > 0 ? <span className="small muted">{t("home.today.time", { time: minutesText(today.today_min) })}
        {" · "}<kbd>j</kbd><kbd>k</kbd> {t("home.today.keys")}</span> : null}>
      <div className="home-list">
        {error && <QuietError text={t("home.today.load_failed")} retry={retry} />}
        {!today ? !error && <SkeletonRows /> : items.length ? <TodayList items={items.slice(0, TODAY_SHOWN)} /> : (
          <p className="quiet-line">{t("home.today.empty")}</p>
        )}
        {items.length > TODAY_SHOWN && (
          <p className="today-more"><Link to="/my-tasks">{t("home.today.more", { n: items.length - TODAY_SHOWN })}</Link></p>
        )}
        {later > 0 && today?.daily_min && (
          <p className="today-more">{t("home.today.later", { n: later, time: minutesText(today.daily_min) })}{" "}
            <Link to="/my-tasks">{t("home.today.later_link")}</Link></p>
        )}
      </div>
    </Section>
  );
}

const SHOWN_BROKE = 5;

/** What broke, as rows on the sheet: the worker first (the one fix that unblocks everything), then each failure with
 * the place to act on it. What the company is already redoing is one quiet line, since the owner has nothing to do. */
function Broke({ alerts, fixSaid }: { alerts: Row[]; fixSaid: boolean }) {
  const need = alerts.filter((a) => alertTone(a) !== "calm").sort((a, b) => Number(b.kind === "worker_down") - Number(a.kind === "worker_down"));
  const calm = alerts.length - need.length;
  if (!alerts.length) return null;
  const split = alertCounts(alerts);
  return (
    <Section title={t("home.alerts.section")} count={need.length || undefined}
      actions={<Link className="small" to="/inbox?tab=alerts">{t("home.alerts.all")}</Link>}>
      {need.length > 1 && <p className="small muted home-split">{[
        split.tasks ? t("home.alerts.split_tasks", { n: split.tasks }) : null,
        split.projects ? t("home.alerts.split_projects", { n: split.projects }) : null,
        split.other ? t("home.alerts.split_other", { n: split.other }) : null,
        split.company ? t("home.alerts.split_company", { n: split.company }) : null,
      ].filter(Boolean).join(" · ")}</p>}
      <ul className="home-broke" aria-label={t("home.alerts.label")}>
        {need.slice(0, SHOWN_BROKE).map((a, i) => (
          <li key={`${a.kind}-${a.task_id ?? a.dev_job_id ?? a.job_id ?? a.project_id ?? i}`} className={`t-${alertTone(a)}`}>
            <span className="sdot" aria-hidden="true" />
            <span className="home-broke-t">
              <ItemLink to={alertHref(a)}>{a.kind === "system_problem" ? <Fix text={alertText(a)} /> : alertText(a)}</ItemLink>
              {a.kind === "worker_down" && !fixSaid && <span className="home-broke-fix"><Fix text={`\`${WORKER_FIX}\``} /></span>}
            </span>
            {a.at && <span className="small muted nowrap" title={when(a.at)}>{ago(a.at)}</span>}
          </li>
        ))}
      </ul>
      {need.length > SHOWN_BROKE && (
        <p className="small muted">{tx("home.alerts.rest", { n: need.length - SHOWN_BROKE, link: <Link to="/inbox?tab=alerts">{t("home.alerts.rest_link")}</Link> })}</p>
      )}
      {calm > 0 && <p className="small muted">{t("home.alerts.calm_line", { n: calm })}</p>}
    </Section>
  );
}

/**
 * Agents with a failed task or stopped; while the cycle board shows who works on what, only those. Without a cycle it
 * is also who is working, or one sentence naming who worked last. A failed model call alone fails no task, so it is
 * not a failure here (`agentStatus`, as on the agents page).
 */
function AgentsNow({ agents, cycleShown }: { agents: Row[]; cycleShown: boolean }) {
  const worker = useWorker();
  const [showTest] = useShowTest();
  const { data: failedTasks } = useBusinessLive<{ tasks: Row[] }>(`/tasks${qs({ status: "FAILED", include_test: showTest, limit: 500 })}`, ["tasks"]);
  const byOwner = failedByOwner(failedTasks?.tasks);
  const failedOf = (a: Row) => byOwner.get(a.id) ?? [];
  const running = cycleShown ? [] : agents.filter((a) => a.state === "running");
  const failing = agents.filter((a) => agentStatus(a, failedOf(a), worker).kind === "failed"
    || (a.state === "stopped" && !(cycleShown && a.running?.length)));
  const last = agents.filter((a) => a.last_run_at).sort((a, b) => String(b.last_run_at).localeCompare(String(a.last_run_at)))[0];
  const shown = [...running, ...failing];
  if (cycleShown && !shown.length) return null;
  return (
    <Section title={t("home.agents.title")} count={running.length || undefined} actions={<Link className="small" to="/agents">{t("home.agents.all", { n: agents.length })}</Link>}>
      {shown.length ? (
        <div className="agent-grid">
          {shown.map((a) => {
            const work = a.running?.[0];
            if (a.state === "stopped") return <StoppedAgent key={a.id} a={a} />;
            const held = a.state === "running" ? worker.label : null;
            return (
              <Link key={a.id} to={`/agents/${a.id}`} className={`agent-card s-${a.state}`}>
                <div className="agent-card-h">
                  <span className="agent-n" title={agentTitle(a)}>{agentTitle(a)}</span>
                  <Chip tone={a.state === "running" ? (held ? "warn" : "run") : "bad"}>{held ?? t(a.state === "running" ? "home.agents.working" : "home.agents.failed")}</Chip>
                </div>
                <p className="agent-work">{work
                  ? work.title
                  : failedOf(a)[0] ? failedOf(a)[0].title
                    : a.last_run_at ? t("home.agents.last_run", { when: ago(a.last_run_at) }) : t("home.agents.never")}</p>
              </Link>
            );
          })}
        </div>
      ) : (
        <p className="quiet-line">
          {t("home.agents.none_working")}
          {last ? tx("home.agents.last", { name: <Link to={`/agents/${last.id}`}>{agentTitle(last)}</Link>, when: ago(last.last_run_at) }) : t("home.agents.none_yet")}
        </p>
      )}
    </Section>
  );
}

/** An agent the owner stopped: what it still finishes, if anything, and the way to resume it. */
function StoppedAgent({ a }: { a: Row }) {
  const work = a.running?.[0];
  return (
    <div className="agent-card s-stopped">
      <div className="agent-card-h">
        <Link className="agent-n" to={`/agents/${a.id}`} title={agentTitle(a)}>{agentTitle(a)}</Link>
        <Chip tone="warn">{t("home.agents.stopped")}</Chip>
      </div>
      <p className="agent-work">{work ? work.title : t("shell.stop.stopped_since", { since: when(a.stopped?.at) })}</p>
      <div><AgentStopButton agent={a.id} stopped /></div>
    </div>
  );
}

function RecentTasks({ tasks }: { tasks: Row[] }) {
  return (
    <Section title={t("home.tasks.title")} actions={<Link className="small" to="/tasks">{t("home.tasks.board")}</Link>}>
      <div className="home-list">
        {tasks.length ? (
          <ul className="ilist">
            {tasks.map((t) => (
              <li key={t.id}>
                <Link className="grow t1" to={`/projects/${t.project_id}/tasks?task=${t.id}`} title={t.title}>{t.title}</Link>
                <span className="small muted nowrap">{agentName(t.owner)}</span>
                <TaskState status={t.status} />
                <span className="end">{ago(t.updated_at)}</span>
              </li>
            ))}
          </ul>
        ) : <p className="quiet-line">{t("home.tasks.empty")}</p>}
      </div>
    </Section>
  );
}

function ProjectBudgets({ projects, spend }: { projects: Row[]; spend: Row | null }) {
  return (
    <Section title={t("home.projects.title")} count={projects.length || undefined} actions={<>
      {spend && <span className="small"><span className="muted">{t("home.projects.spent")}</span> <b className="mono">{usd(spend.total_usd)}</b> · </span>}
      <Link className="small" to="/projects">{t("home.projects.all")}</Link></>}>
      <div className="home-list">
        {projects.length ? (
          <ul className="ilist">
            {projects.map((p) => (
              <li key={p.id} className="proj-row">
                <div className="grow">
                  <Link className="t1" to={`/projects/${p.id}`}>{p.name}</Link>
                  <p className="t2">{t("home.projects.done", { done: p.tasks_done, total: p.tasks_total })}{p.pending ? t("home.projects.pending", { n: p.pending }) : ""}</p>
                </div>
                <div className="proj-budget">
                  <span className="mono small">{usd(p.spent_usd)} / {usd(p.budget_usd)}</span>
                  {p.budget_usd ? <Meter value={p.spent_usd} max={p.budget_usd} label={t("home.projects.budget_used", { name: p.name })} /> : null}
                </div>
              </li>
            ))}
          </ul>
        ) : <p className="quiet-line">{t("home.projects.empty")}</p>}
        {spend && spend.total_usd > 0 && (
          <p className="quiet-line small">
            {t("home.projects.spent_note", { projects: usd(spend.projects_usd), outside: usd(spend.outside_usd) })}
            {!spend.outside_known && ` ${t("home.projects.spent_before")}`}
          </p>
        )}
      </div>
    </Section>
  );
}

/** A link to the dashboard's cycle cards (`#cycle-waiting`) scrolls to them and puts focus on their first heading. */
function useCycleHash(ready: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    if (hash !== "#cycle-waiting" || !ready) return;
    const sec = document.getElementById("cycle-waiting");
    if (!sec) return;
    sec.scrollIntoView({ block: "start" });
    const head = sec.querySelector<HTMLElement>("h2, h3") ?? sec;
    head.setAttribute("tabindex", "-1");
    head.focus({ preventScroll: true });
  }, [hash, ready]);
}

/**
 * The control room, in the order the owner asks: what needs me (the status sentence, the cycle cards waiting for a word,
 * the owner's list), what broke, and what the company is doing (the cycle at work, the activity feed).
 */
export default function Home() {
  const { businesses, error, reload, current, today, todayError, reloadToday } = useBusinessSelection();
  const [showTest] = useShowTest();
  const worker = useWorker();
  const { data: ov, error: ovError, reload: reloadOv } = useBusinessLive<Row>(`/overview${qs({ include_test: showTest, tz: TZ })}`, OVERVIEW_TABLES);
  const { data: alertRows, error: alertError, reload: reloadAlerts } = useBusinessLive<Row[]>(`/alerts${qs({ include_test: showTest })}`, ALERT_TABLES);
  const afterDecide = useCallback(() => reloadToday(), [reloadToday]);
  const { data: board, error: boardError, reload: reloadBoard, decide } = useCycleBoard(current?.id ?? null, afterDecide);
  useCycleHash(!!board);

  // only today's share: the server spread the rest over the coming days to fit the owner's time
  const items = useMemo(() => (today ? today.items.filter((it) => it.day === 0)
    .sort((a, b) => Number(b.kind === "decision") - Number(a.kind === "decision")) : []), [today]);

  if (!businesses) return <div className="owner-page">{error ? <QuietError text={t("home.load_failed_businesses")} retry={reload} /> : <SkeletonRows />}</div>;
  if (!current) return <NoBusiness />;

  const turn = todayCounts(today);
  const agents: Row[] = ov?.agents ?? [];
  const n = today?.number;
  const alerts = alertRows ?? [];
  const broke = alertCounts(alerts).need;
  const projects: Row[] = (ov?.projects ?? []).filter((p: Row) => p.status !== "ARCHIVED");
  const failedAny = !!(ovError || todayError || boardError || alertError);
  const retryAll = () => { reloadOv(); reloadToday(); reloadBoard(); reloadAlerts(); };
  const next = today && ov ? homeStatus({ today, turn, running: agents.filter((a) => a.state === "running").length, projects, board,
    broke, workerDown: worker.down }) : null;
  const status: Status = next ?? (failedAny ? { tone: "bad", text: t("home.load_failed_status") } : { text: t("home.loading_status") });
  const cycleShown = !!board && board.state !== "none";
  // until the board answers, its place is held; a first-run section would flash and then vanish
  const boardPending = !board && !boardError;
  const listed = new Set(items.slice(0, TODAY_SHOWN).flatMap((it) => (panelTarget(it)?.key === "req" ? [panelTarget(it)!.id] : [])));
  // a decision still open is on the list above; its "came in" line in the feed would say it twice
  const openReqs = new Set(items.flatMap((it) => (panelTarget(it)?.key === "req" ? [panelTarget(it)!.id] : [])));
  const feed: Row[] = (ov?.activity ?? []).filter((e: Row) => !(e.entity === "request" && e.verb === "created" && openReqs.has(Number(e.entity_id))));

  // the head's one primary action: a stop that only the owner can lift, else the most urgent item on the list
  const first = items[0];
  const firstTarget = first ? panelTarget(first) : null;
  const firstLink = first ? cycleTarget(first) ?? first.link : null;
  const primary = next?.action
    ? <ItemLink className="btn-primary" to={next.action.to}>{next.action.label}<Icon name="arrow" /></ItemLink>
    : first && firstTarget ? <PanelLink className="btn-primary" to={first.link} target={firstTarget}>{t("home.status.open_first")}<Icon name="arrow" /></PanelLink>
      : first && firstLink ? <Link className="btn-primary" to={firstLink}>{t("home.status.open_first")}<Icon name="arrow" /></Link> : null;

  return (
    <div className="owner-page dashboard">
      <PageHead
        title={current.name.replace(/^\[TEST\]\s*/, "")}
        status={status}
        sub={n
          ? <>{t(n.goal_from_brief ? "home.goal.from_plan" : "home.goal.default")}: {n.goal.text}
            {n.goal.current != null && n.goal.number != null ? t("home.goal.now", { current: n.goal.current, target: n.goal.number, unit: n.goal.unit })
              : t(n.goal.measure != null && n.repeat == null ? "home.goal.connect" : "home.goal.owner_checks")}</>
          : null}
        actions={<>
          {turn.decisions > 0 && <Link className="btn-ghost" to="/inbox"><Icon name="inbox" />{t("home.status.decide")}</Link>}
          <Link className="btn-ghost" to="/business"><Icon name="business" />{t("home.view_business")}</Link>
          {primary}
        </>} />

      {failedAny && next && <QuietError text={t("home.load_failed_part")} retry={retryAll} />}
      {failedAny && !next && <QuietError text={t("home.load_failed_status")} retry={retryAll} />}
      <ElsewhereLine today={today} what="both" to="/" />
      <KickoffLine business={current} />
      {today && today.yesterday.length > 0 && (
        <p className="home-line home-since"><span className="home-since-k">{t("home.since.title")}</span> {today.yesterday.join(" · ")}</p>
      )}

      {boardPending ? <CycleSkeleton /> : board && cycleShown && <CycleWaiting board={board} decide={decide} listed={listed} />}

      <div className="home-grid">
        <div className="home-main">
          <NeedsYou items={items} today={today} error={todayError} retry={reloadToday} />
          {/* the headline already gives the worker's command; the row names the stop without saying it again */}
          <Broke alerts={alerts} fixSaid={!!next && worker.down && !today?.company_stop} />
        </div>
        <Section title={t("home.activity.title")} className="home-feed" actions={<Link className="small" to="/audit">{t("home.activity.all")}</Link>}>
          {ov ? <ActivityList events={feed} compact owner limit={FEED_SHOWN} empty={t("home.activity.empty")} /> : ovError ? null : <SkeletonRows />}
        </Section>
      </div>

      {board && cycleShown && (board.strip.length > 0 || board.current || board.working.length > 0) && (
        <Section title={t("home.company.title")} className="home-company">
          <CycleRun board={board} decide={decide} />
        </Section>
      )}

      {ov && !boardPending ? <AgentsNow agents={agents} cycleShown={cycleShown} /> : null}

      {!cycleShown && !boardPending && (
        <div className="cols cols-2">
          <RecentTasks tasks={ov?.recent_tasks ?? []} />
          <ProjectBudgets projects={projects} spend={ov?.spend ?? null} />
        </div>
      )}
    </div>
  );
}
