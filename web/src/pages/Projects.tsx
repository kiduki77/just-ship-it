import { FormEvent, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { post, qs, Row, useLive } from "../api";
import { Business, useBusinessLive, useBusinessSelection, useNoBusiness } from "../business";
import { Icon } from "../components/icons";
import {
  Banner, Board, BoardCard, Box, EmptyState, FirstRun, Meter, PageHead, Row as ListRow, Rows, Status, TabPanel, Tabs, Toolbar, Tone, useView, ViewSwitch,
} from "../components/kit";
import { budgetStop, devStopped, finished, projectWord, ProjectStateChip, StageRef, StageTag, stageOf, StatusLine, useStages } from "../components/project";
import { useWorker } from "../components/frame";
import { useCycleBoard } from "../cycle/useCycleBoard";
import { ActionButton, ago, Loading, Skeleton, usd, useShowTest, when } from "../components/ui";
import { PlainKey, t, tx } from "../i18n";
import "./work.css";

type TabKey = "active" | "done" | "archived" | "all";
// A run that finished while its development stopped or still runs is not done for the owner: it stays open.
const TABS: [TabKey, PlainKey, (p: Row) => boolean][] = [
  ["active", "work.projects.tab.open", (p) => p.status !== "ARCHIVED" && !finished(p)],
  ["done", "work.projects.tab.done", finished],
  ["archived", "work.projects.tab.archived", (p) => p.status === "ARCHIVED"],
  ["all", "work.projects.tab.all", () => true],
];

const isPaused = (p: Row): boolean => p.status === "PAUSED" || (!!p.paused && ["CREATED", "RUNNING", "WAITING_HUMAN"].includes(p.status));
const ENDED = ["DONE", "FAILED", "CANCELLED", "ARCHIVED"];
/** A finished run whose build waits on the owner: a merge to decide, or a merged build to make usable. */
const buildWaits = (p: Row) => ["DEV_DELIVERED", "MERGED"].includes(stageOf(p));
/** The owner's turn, from one source: a decision waiting (the same `pending` the sidebar counts), a run that asks,
 * or a build that waits. A paused or archived project is not the owner's turn. */
const ownerTurn = (p: Row) => !isPaused(p) && p.status !== "ARCHIVED" && (p.pending > 0 || p.status === "WAITING_HUMAN" || buildWaits(p));
const isRunning = (p: Row) => !isPaused(p) && !ownerTurn(p) && (!ENDED.includes(p.status) || stageOf(p) === "DEV_ACTIVE");

/** Board columns: where each project stands with respect to the owner. Every project lands in exactly one. */
const COLUMNS: { key: string; label: PlainKey; tone: Tone; has: (p: Row) => boolean; empty: PlainKey }[] = [
  { key: "wait", label: "label.status.WAITING_HUMAN", tone: "warn", has: ownerTurn, empty: "work.projects.empty.wait" },
  { key: "run", label: "common.in_progress", tone: "run", has: isRunning, empty: "work.projects.empty.run" },
  { key: "paused", label: "label.status.PAUSED", tone: "muted", has: (p) => isPaused(p) && p.status !== "ARCHIVED", empty: "work.projects.empty.paused" },
  { key: "end", label: "work.projects.col.end", tone: "ok", has: (p) => !ownerTurn(p) && !isRunning(p) && !(isPaused(p) && p.status !== "ARCHIVED"), empty: "work.projects.empty.none" },
];

/** The list's order: the owner's turn, then stopped or failed, then moving, then the rest; newest first within. */
function rank(p: Row): number {
  if (ownerTurn(p)) return 0;
  if (p.status === "FAILED" || devStopped(p) || isPaused(p)) return 1;
  if (isRunning(p)) return 2;
  return 3;
}

export default function Projects() {
  const [params, setParams] = useSearchParams();
  const { state } = useLocation() as { state: { archived?: string } | null };
  const asked = params.get("tab") as TabKey | null;
  const [showTest] = useShowTest();
  const { current } = useBusinessSelection();
  const noBusiness = useNoBusiness();
  const worker = useWorker();
  const [view, setView] = useView("projects", "list");
  const [q, setQ] = useState(params.get("q") ?? "");
  const { data, error, failure, reload } = useBusinessLive<Row[]>(`/projects${qs({ include_archived: true })}`, ["projects", "tasks", "agent_requests"]);
  const stages = useStages(current?.id ?? null);
  const creating = params.get("new") === "1" && !!current;
  // work starts from a business's approved plan: with none, "New project" explains that instead of making a business
  const planned = !!current && current.brief_version != null;
  const set = (k: string, v: string | null) => {
    if (v == null || v === "") params.delete(k);
    else params.set(k, v);
    setParams(params, { replace: true });
  };
  const newButton = (primary: boolean) => !creating && current
    ? <button type="button" className={primary ? "btn-primary" : "btn-ghost"} onClick={() => set("new", "1")}><Icon name="plus" />{t("work.projects.new")}</button> : null;
  if (noBusiness) {
    return (
      <div className="wk-page">
        <PageHead title={t("shell.nav.projects")} status={{ text: t("work.projects.sub") }} />
        <FirstRun>{t("work.projects.firstrun")}</FirstRun>
      </div>
    );
  }
  const form = creating && current && (planned
    ? <NewProject business={current} onClose={() => set("new", null)} />
    : <NoPlan business={current} onClose={() => set("new", null)} />);
  if (!data) {
    return (
      <div className="wk-page">
        <PageHead title={t("shell.nav.projects")} actions={newButton(true)} />
        {form}
        {error ? <Loading error={error} failure={failure} retry={reload} /> : <Skeleton rows={5} />}
      </div>
    );
  }
  const visible = data.filter((p) => showTest || !p.is_test);
  // with no tab asked for, the page opens on the first one that has projects, never on an empty "open"
  const pick = TABS.find(([k]) => k === asked) ?? TABS.find(([, , fn]) => visible.some(fn)) ?? TABS[0];
  const needle = q.trim().toLowerCase();
  const rows = visible.filter(pick[2]).filter((p) => !needle || `p-${p.id} ${p.name} ${p.goal ?? ""}`.toLowerCase().includes(needle))
    .sort((a, b) => rank(a) - rank(b) || String(b.updated_at).localeCompare(String(a.updated_at)));
  const needsOwner = visible.some(ownerTurn);
  const stageOfP = (p: Row) => (p.stage_id ? stages?.get(p.stage_id) ?? null : null);
  return (
    <div className="wk-page">
      <PageHead title={t("shell.nav.projects")} status={visible.length ? projectsStatus(visible, worker.down) : null}
        actions={<>
          {needsOwner && <Link className="btn-primary" to="/inbox">{t("work.go_decide")}<Icon name="arrow" /></Link>}
          {newButton(!needsOwner)}
        </>} />
      {state?.archived && pick[0] === "archived" && (
        <Banner tone="info" icon="check">{t("work.projects.archived_note", { name: state.archived })}</Banner>
      )}
      {form}
      {visible.length === 0 ? (
        <Box>
          {planned ? (
            <EmptyState title={t("work.projects.none")} action={<Link className="btn-ghost" to="/">{t("work.projects.none_stage_link")}</Link>}>
              {t("work.projects.none_stage")}
            </EmptyState>
          ) : (
            <EmptyState title={t("work.projects.none")} action={<Link className="btn-ghost" to="/business">{t("work.projects.none_plan_link")}</Link>}>
              {t("work.projects.none_plan")}
            </EmptyState>
          )}
        </Box>
      ) : (
        <>
          <Toolbar label={t("work.projects.toolbar")}
            right={<>
              <span className="small muted num wk-count" aria-live="polite">{needle ? t("work-tasks.shown", { shown: rows.length, all: visible.filter(pick[2]).length }) : t("work.projects.count", { n: rows.length })}</span>
              <ViewSwitch value={view} onChange={setView} />
            </>}>
            <Tabs id="pj" label={t("work.projects.tabs")} value={pick[0]} onChange={(k) => set("tab", k)}
              items={TABS.map(([key, name, fn]) => ({ key, label: t(name), count: visible.filter(fn).length }))} />
            <label className="search-in">
              <Icon name="search" />
              <span className="sr-only">{t("work.projects.search")}</span>
              <input type="search" placeholder={t("work.projects.search_placeholder")} value={q} onChange={(e) => { setQ(e.target.value); set("q", e.target.value); }} />
            </label>
          </Toolbar>
          <TabPanel id="pj" tabKey={pick[0]}>
          {rows.length === 0 ? (
            <Box>
              <EmptyState kind="filtered" title={needle ? t("work.projects.no_match") : pick[0] === "active" ? t("work.projects.empty.run") : t("work.projects.empty_tab")}
                action={needle ? <button type="button" className="btn-ghost" onClick={() => { setQ(""); set("q", null); }}>{t("work.clear_filters")}</button> : null}>
                {needle ? t("work.projects.no_match_body") : t("work.projects.empty_tab_body")}
              </EmptyState>
            </Box>
          ) : view === "board" ? (
            <Board label={t("work.projects.board")} columns={COLUMNS.map((c) => ({
              key: c.key, label: t(c.label), tone: c.tone, empty: t(c.empty),
              items: rows.filter(c.has).map((p) => <ProjectBoardCard key={p.id} p={p} stage={stageOfP(p)} showStatus={c.key === "end"} />),
            }))} />
          ) : (
            <Box pad={false}>
              <Rows className="pj-list" label={t("work.projects.list")}>
                {rows.map((p) => <ProjectRow key={p.id} p={p} stage={stageOfP(p)} onChange={reload} />)}
              </Rows>
            </Box>
          )}
          </TabPanel>
        </>
      )}
    </div>
  );
}

/** Where the projects stand, as the owner needs it: trouble and waiting decisions first, then what is moving. */
function projectsStatus(projects: Row[], workerDown: boolean): Status {
  const open = projects.filter((p) => p.status !== "ARCHIVED");
  const live = open.filter((p) => !finished(p));
  if (!open.length) return { text: t("work.projects.status.none") };
  const stopped = live.filter(budgetStop);
  const failed = live.filter((p) => p.status === "FAILED" && !budgetStop(p));
  const devDown = open.filter(devStopped);
  const pending = open.reduce((s, p) => s + (p.pending ?? 0), 0);
  const blocking = open.reduce((s, p) => s + (p.blocking ?? 0), 0);
  const running = live.filter(isRunning).length;
  const waits = live.filter(buildWaits).length;
  const paused = live.filter(isPaused).length;
  const nearCap = live.filter((p) => p.status !== "FAILED" && p.budget_usd && ((p.spent_usd ?? 0) + (p.reserved_usd ?? 0)) / p.budget_usd >= 0.7).length;
  const held = workerDown ? live.filter((p) => p.needs_worker).length : 0;
  const moving = held ? t("work.projects.status.held", { n: held })
    : running ? t("work.projects.status.running", { n: running })
    : waits ? t("work.projects.status.build_waits", { n: waits })
    : paused ? t("work.projects.status.paused", { n: paused })
    : live.length ? null
    : t("work.projects.status.all_done", { n: open.length });
  const tone: Tone = failed.length || devDown.length ? "bad" : pending || nearCap || stopped.length ? "warn" : running ? "run" : "muted";
  return {
    tone,
    text: <>
      {failed.length ? tx("work.projects.status.failed", { n: failed.length,
        link: <Link to={`/projects/${failed[0].id}`}>{t("work.projects.status.failed_link", { name: failed[0].name })}</Link> }) : null}
      {devDown.length ? tx("work.projects.status.dev_down", { n: devDown.length,
        link: <Link to={`/projects/${devDown[0].id}/dev`}>{t("work.projects.status.dev_down_link", { name: devDown[0].name })}</Link> }) : null}
      {stopped.length ? tx("work.projects.status.budget", { n: stopped.length,
        link: <Link to={`/projects/${stopped[0].id}/settings#budget`}>{t("work.projects.status.budget_link", { name: stopped[0].name })}</Link> }) : null}
      {pending ? <>{tx("work.status.decisions", { n: <b>{pending}</b> })}{blocking ? t("work.status.blocking", { n: blocking }) : null} </> : null}
      {moving}
      {nearCap ? t("work.projects.status.near_cap", { n: nearCap }) : null}
      {!failed.length && !devDown.length && !stopped.length && !pending && !moving && !nearCap ? t("work.projects.status.quiet") : null}
    </>,
  };
}

/**
 * One project per row: its state, its name with the stage it serves, the status sentence and its one action, then the
 * numbers (tasks, budget) and when it last moved. The name is the link; the action stays a button of its own.
 */
function ProjectRow({ p, stage, onChange }: { p: Row; stage: StageRef | null; onChange: () => void }) {
  const pct = p.tasks_total ? Math.round((p.tasks_done / p.tasks_total) * 100) : 0;
  return (
    <ListRow className={`pj-row ${p.status === "ARCHIVED" ? "is-archived" : ""}`}
      lead={<ProjectStateChip p={p} />}
      title={<>
        <Link className="pj-name" to={`/projects/${p.id}`} title={stage?.doing ?? p.goal ?? p.name}>{p.name}</Link>
        {stage && <StageTag stage={stage} />}
        {p.is_test && <span className="tag">{t("work.test")}</span>}
      </>}
      meta={<StatusLine p={p} onChange={onChange} compact stage={stage} />}
      end={<>
        <span className="pj-num" title={t("work.card.progress")}>
          <span className="num">{p.tasks_total ? t("work.card.done_of", { done: p.tasks_done, total: p.tasks_total }) : t("work.card.no_tasks")}</span>
          <span className="wk-prog" role="progressbar" aria-label={t("work.card.progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span style={{ width: `${pct}%` }} /></span>
        </span>
        <span className="pj-num">
          <span className="num">{usd(p.spent_usd)} / {usd(p.budget_usd)}</span>
          <Meter value={p.spent_usd ?? 0} max={p.budget_usd ?? 0} label={t("work.card.budget_used")} />
        </span>
        <span className="pj-when" title={when(p.updated_at)}>{ago(p.updated_at)}</span>
        {p.status === "ARCHIVED" && (
          <ActionButton className="btn-ghost small" run={() => post(`/projects/${p.id}/unarchive`, {}).then(onChange)} done={t("work.settings.unarchived")}>
            {t("work.projects.unarchive")}
          </ActionButton>
        )}
      </>} />
  );
}

function ProjectBoardCard({ p, stage, showStatus }: { p: Row; stage: StageRef | null; showStatus: boolean }) {
  const worker = useWorker();
  const state = projectWord(p, worker.label);
  return (
    <BoardCard to={`/projects/${p.id}`} title={p.name} sub={stage ? t("work.stage_ref", { cycle: stage.cycle, n: stage.position + 1, title: stage.title }) : p.goal}
      live={p.tasks_active > 0}
      tags={<>
        {showStatus && <span className={`tag t-${state.tone}`}>{state.word}</span>}
        {p.is_test && <span className="tag">{t("work.test")}</span>}
        {p.pending > 0 && <span className={`tag ${p.blocking ? "t-bad" : "t-warn"}`}>{t("work.card.pending", { n: p.pending })}</span>}
      </>}
      foot={<>
        <span className="num">{t("work.card.done_of", { done: p.tasks_done, total: p.tasks_total })}</span>
        <span title={when(p.updated_at)}>{ago(p.updated_at)}</span>
        <span className="sp num">{usd(p.spent_usd)} / {usd(p.budget_usd)}</span>
      </>} />
  );
}

/** A business with no approved plan has no work to add to: say where work starts and give the two doors. */
function NoPlan({ business, onClose }: { business: Business; onClose: () => void }) {
  return (
    <Box className="wk-new">
      <h2 className="wk-new-h">{t("work.noplan.title", { name: business.name.replace(/^\[TEST\]\s*/, "") })}</h2>
      <p>{t("work.noplan.body")}</p>
      <div className="row wrap">
        <Link className="btn-primary" to="/business">{t("work.noplan.open")}</Link>
        <Link className="btn-ghost" to="/businesses/new"><Icon name="plus" />{t("work.new.other_idea_link")}</Link>
        <button type="button" className="btn-ghost" onClick={onClose}>{t("common.close")}</button>
      </div>
    </Box>
  );
}

/** A name from the goal's first line, under 60 characters: whole when it fits, else up to the last clause break (a
 * comma, a colon, a dash), else up to a word with "…", so a cut name never reads as a finished phrase. */
function nameFromGoal(goal: string): string {
  const first = goal.trim().split("\n", 1)[0].trim();
  if (first.length <= 60) return first;
  const cut = first.slice(0, 60);
  const clause = Math.max(...[". ", ", ", "; ", ": ", " - ", " — "].map((m) => cut.lastIndexOf(m)));
  if (clause > 20) return cut.slice(0, clause).trimEnd();
  const at = cut.lastIndexOf(" ");
  return `${(at > 20 ? cut.slice(0, at) : cut.slice(0, 59)).trimEnd()}…`;
}

/**
 * A project the owner starts by hand. It runs now (the owner's click is let through, as every owner start is) and joins
 * the running stage when one runs (services/queue.py: work of no stage joins the stage it runs in), on the default
 * budget unless given one, and takes one of the business's experiment slots. The form says all three before the click;
 * the slot count warns when it is already full, and does not block.
 */
function NewProject({ business, onClose }: { business: Business; onClose: () => void }) {
  const [goal, setGoal] = useState("");
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const { data: settings } = useLive<Row>("/settings", []);
  const { data: roadmap } = useLive<Row>(`/businesses/${business.id}/roadmap`, ["bets"]);
  const { data: board } = useCycleBoard(business.id);
  const running = board?.current?.stage.status === "running" ? board.current.stage : null;
  const stageName = running && board?.cycle
    ? t("work.stage_ref", { cycle: board.cycle.number, n: running.position + 1, title: running.title }) : null;
  const fallback = settings?.limits?.project_budget_usd as number | undefined;
  const slots = roadmap?.limits as { running: number; max_running: number } | undefined;
  const full = !!slots && slots.running >= slots.max_running;
  const badBudget = budget.trim() !== "" && !(Number(budget) > 0);
  // the budget it would run on: the one typed, else the company default once /settings has answered
  const amount = budget.trim() && !badBudget ? Number(budget) : fallback;
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!goal.trim() || busy || badBudget) return;
    setBusy(true);
    setErr(null);
    try {
      const p = await post<Row>("/projects", { goal, business_id: business.id, name: name.trim() || nameFromGoal(goal), budget_usd: budget ? Number(budget) : undefined });
      navigate(`/projects/${p.id}`);
    } catch (x: any) {
      setErr(t("work.new.failed", { error: x.message }));
      setBusy(false);
    }
  }
  return (
    <Box className="wk-new">
      <h2 className="wk-new-h">{t("work.projects.new")}</h2>
      <p className="small muted">
        {t("work.new.lead", { name: business.name.replace(/^\[TEST\]\s*/, "") })}{" "}
        {t("work.new.other_idea")} <Link to="/businesses/new">{t("work.new.other_idea_link")}</Link>
      </p>
      <form onSubmit={submit} className="form" noValidate>
        <div className="field">
          <label htmlFor="np-goal">{t("work.new.goal")}</label>
          <textarea id="np-goal" rows={4} autoFocus placeholder={t("work.new.goal_placeholder")}
            value={goal} onChange={(e) => setGoal(e.target.value)} />
          <span className="small muted">{t("work.new.goal_help")}</span>
        </div>
        <div className="row wrap">
          <div className="field grow">
            <label htmlFor="np-name">{t("work.new.name")} <span className="muted">{t("work.optional")}</span></label>
            <input id="np-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={goal.trim() ? nameFromGoal(goal) : undefined} aria-describedby="np-name-h" />
            <span id="np-name-h" className="small muted">{t("work.new.name_help")}</span>
          </div>
          <div className="field">
            <label htmlFor="np-budget">{t("work.new.budget")} <span className="muted">{t("work.optional")}</span></label>
            <input id="np-budget" type="number" min={1} inputMode="decimal" aria-invalid={badBudget} aria-describedby="np-budget-h"
              placeholder={fallback != null ? t("work.new.budget_default_n", { amount: usd(fallback) }) : t("work.new.budget_default")} value={budget} onChange={(e) => setBudget(e.target.value)} />
            <span id="np-budget-h" className={`small ${badBudget ? "bad-t" : "muted"}`}>{badBudget ? t("work.new.budget_bad") : t("work.budget.help")}</span>
          </div>
        </div>
        <p className="wk-note" role="note">
          {/* which stage it joins is said once the board has answered, never guessed while it loads */}
          {!board ? (amount != null ? t("work.new.starts", { amount: usd(amount) }) : t("work.new.starts_default"))
            : stageName
              ? (amount != null ? t("work.new.joins", { stage: stageName, amount: usd(amount) }) : t("work.new.joins_default", { stage: stageName }))
              : amount != null ? t("work.new.outside", { amount: usd(amount) }) : t("work.new.outside_default")}
          {slots ? <> {full ? t("work.new.slots_full", { running: slots.running, max: slots.max_running, after: slots.running + 1 })
            : t("work.new.slots", { running: slots.running, max: slots.max_running })}</> : null}
        </p>
        <div className="row wrap">
          <button type="submit" className="btn-primary" disabled={busy || !goal.trim() || badBudget}>{busy ? t("work.new.creating") : t("work.new.submit")}</button>
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>{t("common.close")}</button>
          {err && <span className="act-msg bad" role="alert">{err}</span>}
        </div>
      </form>
    </Box>
  );
}
