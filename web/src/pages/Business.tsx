import { ReactNode, useEffect } from "react";
import { ItemLink } from "../components/panel";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useLive } from "../api";
import { Business, useBusinessSelection } from "../business";
import { Fix } from "../components/fix";
import { useWorker } from "../components/frame";
import { todayCounts } from "../today";
import { CurrentStage } from "../cycle/types";
import { useCycleBoard } from "../cycle/useCycleBoard";
import { BetCard, BetsTab, betNeedsOwner } from "../business/Bets";
import { ChannelRules } from "../business/ChannelRules";
import { BriefSummary, BriefTab } from "../business/BriefView";
import { CustomersCard } from "../business/Customers";
import { IdeasTab } from "../business/Ideas";
import { LookTab } from "../business/Look";
import { Ladder, NumbersTab, Reactions, unitFor } from "../business/Numbers";
import { CameFrom } from "../business/Tracking";
import { Loaded, Steps, TAB_KEYS, TabKey, useBizTabs } from "../business/parts";
import { FirstTenKit } from "../business/Kit";
import { PeopleTab } from "../business/People";
import { Bet, BriefView, Ideas, Lane, Numbers, People, Roadmap, Routine, budgetLink, cleanName, daysLeft, isLive } from "../business/types";
import { Icon } from "../components/icons";
import { Banner, Board, BoardColumn, Hint, PageHead, Section, Status, Tabs, Tone } from "../components/kit";
import { QuietError } from "../components/today";
import { landOn, Skeleton } from "../components/ui";
import { PlainKey, t, tx } from "../i18n";
import { CycleHistory } from "../cycle/History";
import { IdeaPanelSection } from "../kickoff/IdeaPanel";
import { KickoffView } from "../kickoff/types";
import "../business/business.css";

const TAB_LABEL: Record<TabKey, PlainKey> = {
  overview: "business.tab.overview", bets: "business.tab.bets", numbers: "business.tab.numbers", people: "business.tab.people",
  ideas: "business.tab.ideas", brief: "business.tab.brief", look: "business.tab.look",
};

const LANES: { key: Lane; label: PlainKey; tone: Tone; empty: PlainKey }[] = [
  { key: "now", label: "business.lane.now", tone: "run", empty: "business.lane.now_empty" },
  { key: "next", label: "business.lane.next", tone: "info", empty: "business.lane.next_empty" },
  { key: "later", label: "business.lane.later", tone: "muted", empty: "business.lane.later_empty" },
  { key: "done", label: "business.lane.done", tone: "ok", empty: "business.lane.done_empty" },
];

const STAGE_STATE: Record<"reached" | "current" | "ahead", PlainKey> = {
  reached: "business.stage_state.reached", current: "business.stage_state.current", ahead: "business.stage_state.ahead",
};

type Href = ReturnType<typeof useBizTabs>["href"];
type Live<T> = { data: T | null; error: string | null; reload: () => void };

/** Everything waiting on the owner on this screen, said once at the top with where to act. */
function OwnerWork({ roadmap, routines, ideas, brief, kickoffId, href, businessId }: {
  roadmap: Roadmap | null; routines: Routine[] | null; ideas: Ideas | null; brief: BriefView | null; kickoffId: number | null; href: Href;
  businessId: number;
}) {
  const { today } = useBusinessSelection();
  const worker = useWorker();
  const mine = today?.business_id === businessId ? today : null;
  const counts = todayCounts(mine);
  const cycleItem = mine?.items.find((it) => it.kind === "cycle") ?? null;
  const queuedIdeas = (ideas?.counts.waiting ?? 0) > 0;
  const bets = roadmap?.bets ?? [];
  const legacy = bets.filter((b) => b.legacy);
  const stuck = bets.filter((b) => b.attention.includes("retro_stuck"));
  const refused = bets.filter((b) => b.attention.includes("retro_budget") && b.budget_stop);
  const failed = bets.filter((b) => !b.legacy && b.attention.includes("run_failed"));
  const proposed = (routines ?? []).filter((r) => r.state === "proposed");
  const c = ideas?.counts;
  const go = (to: string, text: PlainKey = "business.owner.view") => <ItemLink className="btn-ghost small" to={to}>{t(text)}</ItemLink>;
  const bold = (n: number): ReactNode => <b>{n}</b>;
  const needs: Need[] = [];
  const need = (key: string, tone: Tone, text: ReactNode, action?: ReactNode) => needs.push({ key, tone, text, action });
  if (cycleItem) need("cycle", "warn", <><b>{cycleItem.title}</b> {cycleItem.meta.reason}</>, go(cycleItem.link, "business.owner.cycle_open"));
  if (counts.decisions) {
    need("decisions", "warn", counts.blocking ? tx("business.owner.decisions_blocking", { n: bold(counts.decisions), blocking: counts.blocking })
      : tx("business.owner.decisions", { n: bold(counts.decisions) }), go("/inbox", "business.owner.decide_all"));
  }
  for (const b of failed) need(`failed-${b.id}`, "bad", tx("business.owner.run_failed", { title: <b>{b.title}</b> }), go(href("bets", b.ref.toLowerCase())));
  for (const b of refused) need(`refused-${b.id}`, "warn", <><b>{b.title}</b>: {b.budget_stop!.says}</>, go(budgetLink(b.budget_stop!), "business-bets.raise_budget"));
  if (stuck.length) need("stuck", "bad", tx("business.owner.stuck", { n: bold(stuck.length) }), go(href("bets", stuck[0].ref.toLowerCase()), "business.owner.stuck_action"));
  if (brief?.draft_waiting && brief.brief && kickoffId) need("draft", "warn", t("business.owner.draft"), go(`/kickoff/${kickoffId}`, "business.owner.review"));
  if (legacy.length) need("legacy", "warn", tx("business.owner.legacy", { n: bold(legacy.length) }), go(href("bets", legacy[0].ref.toLowerCase()), "business.owner.legacy_action"));
  if (proposed.length) {
    need("routines", "warn", tx("business.owner.routines", { n: bold(proposed.length) }),
      go(proposed[0].request_id ? `/inbox?req=${proposed[0].request_id}` : href("bets"), "business.owner.decide"));
  }
  if (c?.discard_proposed) need("discard", "warn", tx("business.owner.discard", { n: bold(c.discard_proposed) }), go(href("ideas"), "business.owner.review"));
  if (c?.failed) need("ideas-failed", "warn", tx("business.owner.ideas_failed", { n: bold(c.failed) }), go(href("ideas"), "business.owner.review"));
  if (worker.down && queuedIdeas) need("worker", "warn", <Fix text={t("business.owner.worker_ideas")} />);
  if (!needs.length) return null;
  if (needs.length === 1) return <Banner tone={needs[0].tone} action={needs[0].action}>{needs[0].text}</Banner>;
  // several at once: one banner that counts them, one line each with its own way to act
  return (
    <Banner tone={needs.some((n) => n.tone === "bad") ? "bad" : "warn"}>
      <p className="banner-h">{t("business.owner.waiting", { n: needs.length })}</p>
      <ul className="banner-needs">
        {needs.map((n) => (
          <li key={n.key} className={`t-${n.tone}`}>
            <span className="banner-need-t">{n.text}</span>
            {n.action && <span className="banner-need-a">{n.action}</span>}
          </li>
        ))}
      </ul>
    </Banner>
  );
}

/** One thing on this screen that waits on the owner, with where to act on it. */
type Need = { key: string; tone: Tone; text: ReactNode; action?: ReactNode };

function worst(bets: Bet[]): Tone | undefined {
  const tones = bets.flatMap((b) => b.attention).map((a) => (a === "run_failed" || a === "retro_stuck" ? "bad" : a.startsWith("over_") || a === "owner_cleanup" || a === "retro_budget" ? "warn" : null));
  return tones.includes("bad") ? "bad" : tones.includes("warn") ? "warn" : undefined;
}

/** How soon the nearest live bet is due, as one sentence, or that none has a deadline. */
function deadlineText(soonest: number | undefined): string {
  if (soonest === undefined) return t("business.status.no_deadline");
  if (soonest === 0) return t("business.status.due_today");
  return soonest > 0 ? t("business.status.due_in", { n: soonest }) : t("business.status.overdue", { n: -soonest });
}

function goalText(goal: Numbers["completion"]["goals"][number], fromDefault: boolean): string {
  if (goal.current === null || goal.number === null) return t(fromDefault ? "business.status.goal_default_owner" : "business.status.goal_owner", { goal: goal.text });
  return t(fromDefault ? "business.status.goal_default" : "business.status.goal", { goal: goal.text, current: goal.current, target: goal.number, unit: unitFor(goal.unit) });
}

/** Where the business stands toward its first users, in one sentence: the stage, the live attempts, the north-star count. */
/** The board's sentence is a heading ("Cycle 1 · Define · 2 agents working"); the status reads on after it, so it ends here. */
const closed = (s: string) => (/[.!?]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`);

/** `planned` is null while the plan is still loading, so the "set in the kickoff" line never shows for a plan that exists. */
function businessStatus(roadmap: Roadmap | null, numbers: Numbers | null, planned: boolean | null, cycleSentence: string | null): Status {
  if (!roadmap) return { text: cycleSentence ?? t("business.status.loading") };
  const rungs = roadmap.rungs ?? [];
  const at = rungs.findIndex((r) => r.state === "current");
  const running = roadmap.bets.filter(isLive);
  const soonest = running.map((b) => daysLeft(b)).filter((d): d is number => d !== null).sort((x, y) => x - y)[0];
  const inNow = (roadmap.lanes.now ?? []).length;
  const c = numbers?.completion;
  const goal = c?.goals[c.headline];
  const parts = [
    cycleSentence ? closed(cycleSentence) : "",
    at >= 0 ? t("business.status.stage", { at: at + 1, total: rungs.length, name: rungs[at].name }) : "",
    running.length ? `${t("business.status.running", { n: running.length })} ${deadlineText(soonest)}`
      : inNow ? t("business.status.now_finished", { n: inNow }) : t("business.status.idle"),
    planned === false ? t("business.status.goal_in_kickoff") : planned && goal ? goalText(goal, c?.targets_from === "default") : "",
  ];
  return {
    tone: worst(running) ?? (running.length ? "run" : "muted"),
    text: parts.filter(Boolean).join(" "),
  };
}

function RoadmapBoard({ roadmap, href }: { roadmap: Roadmap; href: Href }) {
  const byId = new Map(roadmap.bets.map((b) => [b.id, b]));
  const lane = (k: Lane) => {
    const bets = (roadmap.lanes[k] ?? []).map((id) => byId.get(id)).filter((b): b is Bet => !!b);
    return k === "done" ? bets.sort((a, c) => (c.finished_at ?? "").localeCompare(a.finished_at ?? "")) : bets;
  };
  const columns: BoardColumn[] = LANES.map((l) => ({
    key: l.key, label: t(l.label), tone: l.tone, empty: t(l.empty),
    hint: l.key === "now" && roadmap.cycle ? t("business.cycle_left", { n: roadmap.cycle.days_left }) : undefined,
    items: lane(l.key).map((b) => <BetCard key={b.id} b={b} to={href("bets", b.ref.toLowerCase())} />),
  }));
  return (
    <Section title={t("business.roadmap")} count={roadmap.bets.length}
      actions={<Link className="btn-ghost" to={href("bets")}>{t("business.roadmap_all")}<Icon name="chevron" size={12} /></Link>}>
      {roadmap.bets.length ? <Board columns={columns} label={t("business.roadmap")} /> : (
        <Hint title={t("business.roadmap_empty")}>{t("business.roadmap_empty_body")}</Hint>
      )}
    </Section>
  );
}

const dDay = (n: number) => (n === 0 ? t("business.when.today") : n > 0 ? t("business.when.in", { n }) : t("business.when.ago", { n: -n }));

/** The running stage and the way to what it stands on: its project's evidence (Judge), one click from the overview. */
function StageEvidence({ current }: { current: CurrentStage | null }) {
  const project = current?.tasks.find((task) => task.project_id)?.project_id;
  if (!current) return null;
  return (
    <p className="biz-stage-ev">
      {t("business.stage_now", { title: current.stage.title })}
      {project && <> <Link to={`/projects/${project}/evidence`}>{t("business.stage_evidence")}</Link></>}
    </p>
  );
}

function Overview({ roadmap, numbers, brief, kickoffId, href, businessId, current }: {
  roadmap: Live<Roadmap>; numbers: Live<Numbers>; brief: Live<BriefView>; kickoffId: number | null; href: Href; businessId: number;
  current: CurrentStage | null;
}) {
  const r = roadmap.data;
  const checkpoints = (r?.checkpoints ?? []).filter((c) => !c.done_at).sort((a, b) => a.days_until - b.days_until).slice(0, 5);
  return (
    <>
      <Loaded ready={!!r} error={roadmap.error} retry={roadmap.reload}>
        {r && <RoadmapBoard roadmap={r} href={href} />}
      </Loaded>
      <div className="cols cols-main">
        <div>
          <StageEvidence current={current} />
          {!!r?.rungs.length && (
            <Section title={t("business.stages")} className="biz-rungs">
              <div id="rungs" className="box">
                <Steps label={t("business.stages")} steps={r.rungs.map((g) => ({
                  key: g.id, label: g.name, state: g.state, value: t(STAGE_STATE[g.state]),
                  note: g.exit_signal ? t("business.stage_exit", { signal: g.exit_signal }) : undefined,
                }))} />
              </div>
            </Section>
          )}
          <Section title={t("business.tab.numbers")} actions={<Link className="small" to={href("numbers")}>{t("business.trends")}</Link>}>
            <Loaded ready={!!numbers.data} error={numbers.error} retry={numbers.reload} rows={1}>
              {numbers.data && <Ladder numbers={numbers.data} />}
            </Loaded>
          </Section>
          <CycleHistory businessId={businessId} />
        </div>
        <div>
          <Section title={t("term.plan")} actions={brief.data?.brief ? <Link className="small" to={href("brief")}>{t("business.plan_all")}</Link> : undefined}>
            <Loaded ready={!!brief.data} error={brief.error} retry={brief.reload}>
              {brief.data && <BriefSummary data={brief.data} kickoffId={kickoffId} more={<Link to={href("brief")}>{t("business.plan_sections")}</Link>} />}
            </Loaded>
          </Section>
          <CustomersCard businessId={businessId} />
          {!!checkpoints.length && (
            <Section title={t("business.checkpoints")} count={checkpoints.length}>
              <div className="box flush">
                <ul className="ilist">
                  {checkpoints.map((c) => (
                    <li key={c.id}>
                      <div className="grow"><p className="t1">{c.title}</p><p className="t2">{t(c.kind === "rung_exit" ? "business.checkpoint.rung_exit" : "business.checkpoint.date")}</p></div>
                      <span className={`end biz-end-v ${c.days_until < 0 ? "biz-t-bad" : c.days_until <= 3 ? "biz-t-warn" : ""}`}>{dDay(c.days_until)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </Section>
          )}
        </div>
      </div>
    </>
  );
}

function BusinessScreen({ b }: { b: Business }) {
  const base = `/businesses/${b.id}`;
  const numbers = useLive<Numbers>(`${base}/numbers`, ["people", "bets", "businesses", "activity_events"]);
  const roadmap = useLive<Roadmap>(`${base}/roadmap`, ["bets", "cycles", "businesses", "projects", "jobs"]);
  const routines = useLive<Routine[]>(`${base}/routines`, ["bets", "agent_requests", "jobs", "activity_events"]);
  const people = useLive<People>(`${base}/people`, ["people", "owner_tasks"]);
  const ideas = useLive<Ideas>(`/ideas?business_id=${b.id}`, ["owner_ideas"]);
  const brief = useLive<BriefView>(`${base}/brief`, ["businesses", "kickoff_sessions", "kickoff_turns"]);
  const kickoff = useLive<KickoffView>(`${base}/kickoff`, ["kickoff_sessions"]);
  const board = useCycleBoard(b.id);
  const { tab, href, go } = useBizTabs();
  const { hash } = useLocation();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const betsReady = !!roadmap.data && !!routines.data;

  // Links written before the tabs pointed at a section of one long page (`#ideas`): open that tab instead.
  useEffect(() => {
    const old = decodeURIComponent(hash.slice(1));
    if (!params.has("tab") && old !== "overview" && TAB_KEYS.includes(old as TabKey)) navigate(href(old as TabKey), { replace: true });
  }, [hash, params, navigate, href]);

  const ready = tab === "bets" ? betsReady : tab === "ideas" ? !!ideas.data : tab === "people" ? !!people.data : !!roadmap.data;
  useEffect(() => {
    if (!hash || !ready) return;
    const id = requestAnimationFrame(() => landOn(decodeURIComponent(hash.slice(1))));
    return () => cancelAnimationFrame(id);
  }, [hash, ready, tab]);

  const kickoffId = kickoff.data?.session.id ?? null;
  const kickoffOpen = !!kickoff.data && !["DONE", "ABANDONED"].includes(kickoff.data.session.status);
  const owed: Partial<Record<TabKey, number>> = {
    bets: betsReady ? betNeedsOwner(roadmap.data!, routines.data!) : 0,
    ideas: ideas.data ? ideas.data.counts.discard_proposed + ideas.data.counts.failed : 0,
    brief: brief.data?.draft_waiting ? 1 : 0,
  };
  const reloadBets = () => { roadmap.reload(); routines.reload(); numbers.reload(); };

  return (
    <div className="biz owner-page">
      <PageHead
        title={cleanName(b.name)}
        status={tab === "overview" ? businessStatus(roadmap.data, numbers.data, brief.data ? !!brief.data.brief : null, board.data?.sentence ?? null) : null}
        sub={b.one_liner}
        actions={<>
          {b.project_ids.length > 0 && <Link className="btn-ghost" to="/projects"><Icon name="projects" />{t("business.projects", { n: b.project_ids.length })}</Link>}
          {brief.data?.brief && tab !== "brief" && <Link className="btn-ghost" to={href("brief")}><Icon name="list" />{t("business.view_plan")}</Link>}
          {kickoff.data && (
            <Link className={kickoffOpen ? "btn-primary" : "btn-ghost"} to={`/kickoff/${kickoffId}`}>
              {t(kickoffOpen ? "business.kickoff_continue" : "term.kickoff")}<Icon name="arrow" />
            </Link>
          )}
        </>} />

      {tab === "overview" && (
        <OwnerWork roadmap={roadmap.data} routines={routines.data} ideas={ideas.data} brief={brief.data} kickoffId={kickoffId} href={href} businessId={b.id} />
      )}

      <div className="biz-tabs">
        <Tabs label={t("business.tabs_label")} value={tab} onChange={(k) => go(k)}
          items={TAB_KEYS.map((k) => ({ key: k, label: t(TAB_LABEL[k]), count: owed[k] || undefined, tone: owed[k] ? "warn" as const : undefined }))} />
      </div>

      <div role="tabpanel" aria-label={t(TAB_LABEL[tab])}>
        {tab === "overview" && (
          <>
            <Overview roadmap={roadmap} numbers={numbers} brief={brief} kickoffId={kickoffId} href={href} businessId={b.id} current={board.data?.current ?? null} />
            {/* once work has started the idea review is history: folded to one line, still a click away */}
            {roadmap.data?.bets.length || b.project_ids.length ? (
              <details className="biz-review">
                <summary>{t("business.critics")} <span className="muted">{t("business.critics_folded")}</span></summary>
                <IdeaPanelSection businessId={b.id} />
              </details>
            ) : <IdeaPanelSection businessId={b.id} />}
          </>
        )}
        {tab === "bets" && (
          <Loaded ready={betsReady} error={roadmap.error ?? routines.error} retry={() => { roadmap.reload(); routines.reload(); }}>
            {betsReady && <BetsTab roadmap={roadmap.data!} routines={routines.data!} reload={reloadBets} />}
          </Loaded>
        )}
        {tab === "numbers" && (
          <Loaded ready={!!numbers.data} error={numbers.error} retry={numbers.reload} rows={1}>
            {numbers.data && <NumbersTab numbers={numbers.data} />}
            <CameFrom businessId={b.id} />
            <ChannelRules businessId={b.id} />
            <Reactions businessId={b.id} />
          </Loaded>
        )}
        {tab === "people" && (
          <Loaded ready={!!people.data} error={people.error} retry={people.reload}>
            {people.data && (
              <>
                <FirstTenKit businessId={b.id} reloadPeople={() => { people.reload(); numbers.reload(); }} />
                <PeopleTab businessId={b.id} people={people.data} reload={() => { people.reload(); numbers.reload(); }} />
              </>
            )}
          </Loaded>
        )}
        {tab === "ideas" && <IdeasTab businessId={b.id} live={ideas} href={href} />}
        {tab === "brief" && (
          <Loaded ready={!!brief.data} error={brief.error} retry={brief.reload}>
            {brief.data && <BriefTab data={brief.data} kickoffId={kickoffId} businessId={b.id}
              ask={(section) => href("ideas", "idea-text", { ask: section })} />}
          </Loaded>
        )}
        {tab === "look" && <LookTab businessId={b.id} />}
      </div>

    </div>
  );
}

/** One business, read in tabs: where it stands toward its first users, and what only the owner can do about it. */
export default function BusinessPage() {
  const { businesses, current, error, reload } = useBusinessSelection();
  if (!businesses) {
    // the same head and skeleton every page shows while it loads, never a blank with bare row rules
    return (
      <div className="biz owner-page">
        <PageHead title={t("term.business")} status={error ? null : { text: t("business.status.loading") }} />
        {error ? <QuietError text={t("business.load_failed_businesses")} retry={reload} /> : <Skeleton rows={4} />}
      </div>
    );
  }
  if (!current) {
    return (
      <div className="owner-page">
        <PageHead title={t("term.business")} sub={t("business.none")} />
        <Link className="btn-primary" to="/businesses/new"><Icon name="plus" />{t("business.start")}</Link>
      </div>
    );
  }
  return <BusinessScreen key={current.id} b={current} />;
}
