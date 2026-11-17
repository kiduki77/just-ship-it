import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { post, Row, useLive } from "../api";
import { alertCounts, alertHref, alertTone, alertText, budgetAction, budgetHref, budgetShort } from "../alerts";
import { useBusinessLive, useBusinessSelection } from "../business";
import { ruleCite, strongConfirm } from "../components/decision";
import { Fix } from "../components/fix";
import { useWorker } from "../components/frame";
import { Icon } from "../components/icons";
import {
  Board, BoardCard, BoardColumn, Box, Chip, EmptyState, FirstRun, Hint, PageHead, TabPanel, Tabs, Tone, Toolbar, useView, ViewSwitch,
} from "../components/kit";
import { branchOf, OutlineTree } from "../components/Outline";
import { ItemLink, PanelLink, usePanel } from "../components/panel";
import { ElsewhereLine, QuietError, RowItem, RowList, SkeletonRows } from "../components/today";
import { ActionButton, ago, Loading, RefLink, usd, when } from "../components/ui";
import { PlainKey, t, tx } from "../i18n";
import { agentName, label } from "../labels";
import { dueText } from "../today";
import { freesText, TurnCard, YourTurn } from "../workPath";
import { DevJobActions } from "./Engineering";
import "./decide.css";

const DAY = 86_400_000;
const WEEK = 7 * DAY;
const RECENT_MAX = 12;
const SHOWN = 50;

// The owner decided these; everything else that is closed was handled by the system (merged, withdrawn, policy).
const ownerDecided = (r: Row) => ["APPROVED", "REJECTED", "DEFERRED", "ANSWERED"].includes(r.status)
  && r.response?.responded_by !== "policy";

type Who = "owner" | "project_manager" | "system";
const whoOf = (r: Row): Who => (r.by_project_manager ? "project_manager" : ownerDecided(r) ? "owner" : "system");
type WhoWords = { name: PlainKey; tone: Tone; decided: PlainKey; held: PlainKey; note: PlainKey; none: PlainKey };
const WHO: Record<Who, WhoWords> = {
  owner: { name: "decide.who.owner", tone: "info", decided: "decide.who.owner_decided", held: "decide.who.owner_held",
    note: "decide.who.owner_note", none: "decide.who.owner_none" },
  project_manager: { name: "decide.who.ceo", tone: "run", decided: "decide.who.ceo_decided", held: "decide.who.ceo_held",
    note: "decide.who.ceo_note", none: "decide.who.ceo_none" },
  system: { name: "decide.who.system", tone: "muted", decided: "decide.who.system_decided", held: "decide.who.system_held",
    note: "decide.who.system_note", none: "decide.who.system_none" },
};
const RESULT_TONE: Record<string, Tone> = {
  APPROVED: "ok", AUTO_APPROVED: "ok", ANSWERED: "ok", REJECTED: "bad", DENIED_BY_POLICY: "bad", DEFERRED: "warn",
};
const RISK_TONE: Record<string, Tone> = { medium: "warn", high: "bad", critical: "bad" };

const ms = (v?: string | null) => (v ? new Date(v).getTime() : NaN);
const decidedAt = (r: Row): string => r.resolved_at ?? r.created_at;
/** A product plan decides the direction of a build, so it sits with the major decisions though no work waits on it. */
const major = (r: Row) => !!r.blocking || r.action_kind === "dev.plan_approve";
/** Work is stopped on it, it is a plan, or it lapses within a day: decide these first. */
const urgent = (r: Row, now: number) => major(r) || ms(r.expires_at) - now < DAY;
const byUrgency = (a: Row, b: Row) => Number(major(b)) - Number(major(a))
  || (ms(a.expires_at) || Infinity) - (ms(b.expires_at) || Infinity) || a.id - b.id;
const newestFirst = (a: Row, b: Row) => ms(decidedAt(b)) - ms(decidedAt(a));

type TabKey = "open" | "done" | "alerts";
const VIEWS = ["board", "list", "project"] as const;
/** The tables that move what a decision frees or how far a project is. */
const TURN_TABLES = ["agent_requests", "tasks", "projects", "owner_tasks", "cycles", "cycle_stages", "bets", "request_links"];

export default function Approvals() {
  const [params, setParams] = useSearchParams();
  const tab: TabKey = params.get("tab") === "alerts" ? "alerts" : params.get("tab") === "done" ? "done" : "open";
  const by = (["owner", "project_manager", "system"] as const).find((w) => w === params.get("by")) ?? null;
  // `?project=`: one project's decisions, as the project page and older links ask for them
  const onlyProject = Number(params.get("project")) || null;
  const { businesses, current, today, error: listError, reload: reloadList } = useBusinessSelection();
  const { data: waiting, error, reload } = useBusinessLive<Row[]>(current && "/inbox", ["agent_requests"]);
  const { data: every, error: everyError, reload: reloadEvery } = useBusinessLive<Row[]>(current && "/inbox?include_resolved=true", ["agent_requests"]);
  const { data: projects } = useBusinessLive<Row[]>("/projects", ["projects"]);
  const { data: alerts, error: alertsError, reload: reloadAlerts } = useBusinessLive<Row[]>("/alerts", ["projects", "tasks", "dev_jobs", "jobs", "workers"]);
  const [view, setView] = useView("decisions", "board", VIEWS);
  const { data: turn, error: turnError, reload: reloadTurn } = useLive<YourTurn>(current && `/businesses/${current.id}/your-turn`, TURN_TABLES);
  const byProject = tab === "open" && view === "project";
  // the search is in the address, like the Tasks page: it survives a reload and a shared link
  const [q, setQ] = useState(params.get("q") ?? "");
  const panel = usePanel().current;
  const boardRef = useBoardKeys(tab === "open" && view === "board");

  if (!businesses) {
    return (
      <div className="owner-page dq-page">
        <PageHead title={t("decide.inbox.title")} />
        {listError ? <Loading error={listError} failure="server" retry={reloadList} /> : <SkeletonRows />}
      </div>
    );
  }
  if (!current) {
    return (
      <div className="owner-page dq-page">
        <PageHead title={t("decide.inbox.title")} />
        <FirstRun>{t("decide.inbox.firstrun")}</FirstRun>
      </div>
    );
  }

  const now = Date.now();
  const names = Object.fromEntries((projects ?? []).map((p) => [p.id, p.name]));
  const where = (r: Row) => (r.project_id ? names[r.project_id] ?? "" : t("common.whole_company"));
  const needle = byProject ? "" : q.trim().toLowerCase();
  const matches = (r: Row) => (!onlyProject || r.project_id === onlyProject) && (!needle || [`REQ-${r.id}`, r.project_id ? `P-${r.project_id}` : "", r.title,
    agentName(r.from_agent), where(r), label(r.type)].join(" ").toLowerCase().includes(needle));

  // the server keeps the selected business's requests and the company-wide ones, the same set the sidebar counts
  const open = waiting ?? [];
  const closed = (every ?? []).filter((r) => r.status !== "PENDING");
  const thisWeek = closed.filter((r) => now - ms(decidedAt(r)) < WEEK);
  const mineWeek = thisWeek.filter((r) => whoOf(r) === "owner");
  const projectManagerWeek = thisWeek.filter((r) => whoOf(r) === "project_manager");
  const blocking = open.filter((r) => r.blocking).length;
  const alertList = alerts ?? [];
  const alertsNow = alertCounts(alertList);
  const on = panel?.key === "req" ? panel.id : null;

  // one order with the To do page: what a decision frees, from the server (`/your-turn`), then the older urgency order
  const turnOf = new Map((turn?.cards ?? []).map((c) => [c.id, c]));
  const byRank = (a: Row, b: Row) => (turnOf.get(a.id)?.rank ?? 1e9) - (turnOf.get(b.id)?.rank ?? 1e9) || byUrgency(a, b);
  const openRows = open.filter(matches);
  const nowCol = openRows.filter((r) => urgent(r, now)).sort(byRank);
  const waitCol = openRows.filter((r) => !urgent(r, now)).sort(byRank);
  const deferred = closed.filter((r) => r.status === "DEFERRED").filter(matches).sort(newestFirst);
  const recentAll = thisWeek.filter((r) => r.status !== "DEFERRED").filter(matches).sort(newestFirst);
  const first = [...open].filter((r) => !onlyProject || r.project_id === onlyProject).sort(byRank)[0];

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const search = (v: string) => {
    setQ(v);
    setParam("q", v || null);
  };
  const setTab = (t: TabKey) => {
    const next = new URLSearchParams(params);
    if (t === "open") next.delete("tab");
    else next.set("tab", t);
    if (t !== "done") next.delete("by");
    setParams(next, { replace: true });
  };

  // a failed first load says so instead of "Loading" and zero counts that look like an empty inbox
  const failedFirst = !waiting && !!error;
  const retryAll = () => { reload(); reloadEvery(); };
  const sub = failedFirst
    ? t("decide.inbox.load_failed")
    : !waiting
    ? t("decide.inbox.loading")
    : open.length
      ? t("decide.inbox.waiting", { n: open.length }) + (blocking ? t("decide.inbox.blocking", { n: blocking }) : "")
        + t("decide.inbox.oldest", { ago: ago(open.reduce((a, b) => (ms(b.created_at) < ms(a.created_at) ? b : a)).created_at) })
      : t("decide.inbox.empty");

  const openCard = (r: Row) => <OpenCard key={r.id} r={r} where={where(r)} on={on === r.id} now={now} turn={turnOf.get(r.id)} />;
  const doneCard = (r: Row) => <DoneCard key={r.id} r={r} where={where(r)} on={on === r.id} />;
  // while searching, an empty column says the search found nothing there, not that nothing exists
  const none = (key: PlainKey) => (needle ? t("decide.col.no_match") : t(key));
  const columns: BoardColumn[] = [
    { key: "now", label: t("decide.col.now"), tone: "warn", items: nowCol.map(openCard), hint: t("decide.col.now_hint"), grow: 1.6,
      empty: none("decide.col.now_empty") },
    { key: "wait", label: t("decide.col.wait"), tone: "warn", items: waitCol.map(openCard), grow: 1.6, empty: none("decide.col.wait_empty") },
    { key: "deferred", label: t("decide.col.deferred"), tone: "muted", items: deferred.map(doneCard), empty: none("decide.col.deferred_empty") },
    { key: "recent", label: t("decide.col.recent"), tone: "ok", items: recentAll.slice(0, RECENT_MAX).map(doneCard), grow: .6,
      hint: recentAll.length > RECENT_MAX ? t("decide.col.recent_some", { shown: RECENT_MAX, total: recentAll.length }) : t("decide.col.recent_hint"),
      empty: none("decide.col.recent_empty") },
  ];
  const nothingFound = !!needle && !openRows.length && !deferred.length && !recentAll.length;
  const clearSearch = <button type="button" className="btn-ghost small" onClick={() => search("")}>{t("decide.inbox.clear_search")}</button>;

  const tabs = [
    { key: "open" as const, label: t("decide.inbox.tab_open"), count: waiting ? open.length : undefined, tone: blocking ? ("warn" as const) : undefined },
    { key: "done" as const, label: t("decide.inbox.tab_done"), count: every ? closed.length : undefined },
    { key: "alerts" as const, label: t("decide.inbox.tab_alerts"), count: alerts ? alertsNow.need : undefined, tone: alertsNow.bad ? ("bad" as const) : undefined },
  ];

  return (
    <div className="owner-page dq-page">
      <PageHead title={t("decide.inbox.title")} status={{ tone: failedFirst ? "bad" : open.length ? "warn" : "muted", text: sub }}
        sub={<>
          {every && tx("decide.inbox.week", {
            mine: <Link to="/inbox?tab=done&by=owner">{t("decide.inbox.count", { n: mineWeek.length })}</Link>,
            theirs: <Link to="/inbox?tab=done&by=project_manager">{t("decide.inbox.count", { n: projectManagerWeek.length })}</Link>,
          })}
          {/* failures are not decisions: one quiet line with the way to them, under the decision count */}
          {tab !== "alerts" && alertsNow.need > 0 && (
            <span className="dq-alert-note">
              <span className={alertsNow.bad ? "bad-t" : "warn-t"}>{t("decide.inbox.alerts_banner", { n: alertsNow.need })}</span>{" "}
              <button type="button" className="link" onClick={() => setTab("alerts")}>{t("decide.inbox.see_alerts")}</button>
            </span>
          )}
        </>}
        actions={first && (
          <PanelLink className="btn-primary" to={`/inbox?req=${first.id}`} target={{ key: "req", id: first.id }}>
            {t("decide.inbox.open_first")}<Icon name="arrow" />
          </PanelLink>
        )} />

      <ElsewhereLine today={today} what="decisions" to="/inbox" />

      <Toolbar label={t("decide.inbox.tabs")} right={tab === "open" ? <ViewSwitch value={view} onChange={setView} views={VIEWS} /> : undefined}>
        <Tabs id="dq" label={t("decide.inbox.tabs")} items={tabs} value={tab} onChange={setTab} />
        {tab !== "alerts" && !byProject && <SearchBox value={q} onChange={search} placeholder={t("decide.inbox.search_hint")} />}
        {tab !== "alerts" && !byProject && onlyProject && (
          <span className="dq-only small" role="status">
            {t("decide.inbox.only_project", { project: names[onlyProject] ?? `P-${onlyProject}` })}{" "}
            <button type="button" className="link" onClick={() => setParam("project", null)}>{t("decide.inbox.show_all")}</button>
          </span>
        )}
      </Toolbar>

      <TabPanel id="dq" tabKey={tab}>
      {tab === "alerts" ? (
        // until the list answers, "Nothing to fix" would be a claim nobody checked
        !alerts ? (alertsError ? <QuietError text={t("decide.alert.load_failed")} retry={reloadAlerts} /> : <SkeletonRows />)
          : <Alerts alerts={alertList} onChange={() => { reload(); reloadEvery(); reloadAlerts(); }} />
      ) : tab === "done" ? (
        <>
          {everyError && <QuietError text={t("decide.inbox.history_failed")} retry={reloadEvery} />}
          {!every ? !everyError && <SkeletonRows /> : (
            <History rows={closed.filter(matches)} by={by} setBy={(w) => setParam("by", w)} on={on} where={where} searching={!!needle} />
          )}
        </>
      ) : (
        <>
          {error && <QuietError text={waiting ? t("decide.inbox.refresh_failed") : t("decide.inbox.load_failed")} retry={failedFirst ? retryAll : reload} />}
          {!waiting ? !error && <SkeletonRows /> : nothingFound ? (
            <EmptyState kind="filtered" title={t("decide.inbox.no_match")} action={clearSearch}>{t("decide.inbox.no_match_open")}</EmptyState>
          ) : view === "project" ? (
            <ByProject turn={turn} error={!!turnError} retry={reloadTurn} only={onlyProject} />
          ) : view === "board" ? (
            <div ref={boardRef}>
              <Board label={t("decide.inbox.board")} columns={columns} />
              <p className="dq-keys small muted"><kbd>j</kbd><kbd>k</kbd> {t("decide.keys.move_card")} · <kbd>Enter</kbd> {t("decide.keys.open")}</p>
            </div>
          ) : (
            <OpenList groups={[[t("decide.col.now"), nowCol], [t("decide.col.wait"), waitCol], [t("decide.col.deferred"), deferred]]} on={on} where={where} now={now}
              searching={!!needle} turnOf={turnOf} />
          )}
        </>
      )}
      </TabPanel>
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="search-in dq-search">
      <Icon name="search" />
      <input type="search" value={value} placeholder={placeholder} aria-label={t("decide.inbox.search")} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** j and k walk the board's cards in reading order (column by column), so Enter opens the focused one. */
export function useBoardKeys(active: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.key !== "j" && e.key !== "k") || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.target as HTMLElement).closest("input, textarea, select, [contenteditable], .side-panel")) return;
      const cards = Array.from(ref.current?.querySelectorAll<HTMLElement>("a.bcard") ?? []);
      if (!cards.length) return;
      const at = cards.indexOf(document.activeElement as HTMLElement);
      e.preventDefault();
      cards[e.key === "j" ? Math.min(cards.length - 1, at + 1) : Math.max(0, at - 1)].focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
  return ref;
}

/** The current panel's card keeps an outline, so the owner sees which one is open. */
export function Cur({ on, children }: { on: boolean; children: ReactNode }) {
  return <div className={on ? "dq-cur" : undefined} aria-current={on ? "true" : undefined}>{children}</div>;
}

function TypeTags({ r }: { r: Row }) {
  const risk = RISK_TONE[r.risk_level];
  return (
    <>
      <span className="tag">{label(r.type)}</span>
      {r.status === "PENDING" && r.blocking && <span className="tag t-warn"><Icon name="flag" />{t("decide.tag.blocking")}</span>}
      {r.status === "PENDING" && strongConfirm(r) && <span className="tag"
        title={t(strongConfirm(r) === "unknown_spend" ? "decide.extra_check.why_spend" : "decide.extra_check.why")}>{t("label.status.strong_approve")}</span>}
      {r.status === "PENDING" && risk && <span className={`tag t-${risk}`}>{label(r.risk_level)}</span>}
    </>
  );
}

function OpenCard({ r, where, on, now, turn }: { r: Row; where: string; on: boolean; now: number; turn?: TurnCard }) {
  const due = dueText(r.expires_at, undefined, true);
  const late = ms(r.expires_at) < now;
  // when it came in rides with who sent it; the foot keeps the deadline and the cost, which fit one line in a 1280 column
  return (
    <Cur on={on}>
      <BoardCard title={r.title} to={`/inbox?req=${r.id}`} panel={{ key: "req", id: r.id }}
        tags={<TypeTags r={r} />}
        sub={<>{[agentName(r.from_agent), where].filter(Boolean).join(" · ")} · <span title={when(r.created_at)}>{ago(r.created_at)}</span></>}
        foot={(due || r.estimated_cost_usd || turn) ? <>
          {turn && <span className="dq-fact dq-frees"><Icon name="lock" size={12} />{freesText(turn)}</span>}
          {due && <span className={`dq-fact ${late ? "dq-late" : ""}`}><Icon name="clock" size={12} />{due}</span>}
          {r.estimated_cost_usd ? <span className="dq-fact num">{t("decide.card.cost", { cost: usd(r.estimated_cost_usd) })}</span> : null}
        </> : undefined} />
    </Cur>
  );
}

function DoneCard({ r, where, on }: { r: Row; where: string; on: boolean }) {
  const who = WHO[whoOf(r)];
  return (
    <Cur on={on}>
      <BoardCard title={r.title} to={`/inbox?req=${r.id}`} panel={{ key: "req", id: r.id }}
        tags={<>
          <TypeTags r={r} />
          <span className={`tag ${RESULT_TONE[r.status] ? `t-${RESULT_TONE[r.status]}` : ""}`}>{label(r.status)}</span>
        </>}
        sub={[agentName(r.from_agent), where].filter(Boolean).join(" · ")}
        foot={<>
          {r.report?.closed
            ? <span className="dq-closed">{r.report.closed}</span>
            : <span className="dq-fact">{t(r.status === "DEFERRED" ? who.held : who.decided)}</span>}
          <span className="sp" title={when(decidedAt(r))}>{ago(decidedAt(r))}</span>
        </>} />
    </Cur>
  );
}

/** The same three shelves as rows, with j/k to walk them. */
function OpenList({ groups, on, where, now, searching, turnOf }: {
  groups: [string, Row[]][]; on: number | null; where: (r: Row) => string; now: number; searching: boolean; turnOf: Map<number, TurnCard>;
}) {
  const total = groups.reduce((n, [, rows]) => n + rows.length, 0);
  if (!total) {
    return searching
      ? <Hint title={t("decide.inbox.no_match")}>{t("decide.inbox.no_match_open")}</Hint>
      : <Hint title={t("decide.inbox.none_title")}>{t("decide.inbox.none_list")}</Hint>;
  }
  const meta = (r: Row) => [agentName(r.from_agent), where(r), turnOf.has(r.id) ? freesText(turnOf.get(r.id)!) : null, dueText(r.expires_at, undefined, true),
    r.estimated_cost_usd ? t("decide.card.cost", { cost: usd(r.estimated_cost_usd) }) : null,
    t("decide.card.came_in", { ago: ago(r.created_at) })].filter(Boolean).join(" · ");
  return (
    <Box className="dq-listbox">
      <RowList current={on ? `REQ-${on}` : null} count={total}>
        {groups.filter(([, rows]) => rows.length).map(([title, rows]) => [
          <li key={title} className="grp-h"><h2>{title} <span className="num">{rows.length}</span></h2></li>,
          ...rows.map((r) => (
            <RowItem key={r.id} rowRef={`REQ-${r.id}`} on={on === r.id} label={label(r.type)}
              tags={r.status === "PENDING"
                ? [...(r.blocking ? [t("decide.tag.blocking")] : []), ...(ms(r.expires_at) < now ? [t("decide.tag.overdue")] : []),
                  ...(strongConfirm(r) ? [t("label.status.strong_approve")] : [])]
                : []}
              title={r.title} meta={meta(r)} to={`/inbox?req=${r.id}`} target={{ key: "req", id: r.id }}
              button={r.status === "PENDING" ? t("decide.row.decide") : t("decide.row.view")} />
          )),
        ])}
      </RowList>
      <p className="dq-keys small muted"><kbd>j</kbd><kbd>k</kbd> {t("decide.keys.move")} · <kbd>Enter</kbd> {t("decide.keys.open")}</p>
    </Box>
  );
}

/** Every project's progress and the decisions waiting under it, from the same answer that orders the list. */
function ByProject({ turn, error, retry, only }: { turn: YourTurn | null; error: boolean; retry: () => void; only: number | null }) {
  if (!turn) return error ? <QuietError text={t("path.view.error")} retry={retry} /> : <SkeletonRows />;
  const outline = only ? branchOf(turn.outline, only) : turn.outline;
  const loose = only ? [] : turn.cards.filter((c) => c.project_id == null);
  return (
    <Box>
      {error && <QuietError text={t("decide.inbox.refresh_failed")} retry={retry} />}
      <OutlineTree outline={outline} loose={loose} />
    </Box>
  );
}

/** Closed requests, newest first, with who closed them. */
function History({ rows, by, setBy, on, where, searching }: {
  rows: Row[]; by: Who | null; setBy: (w: Who | null) => void; on: number | null; where: (r: Row) => string; searching: boolean;
}) {
  const [more, setMore] = useState(false);
  const count = (w: Who) => rows.filter((r) => whoOf(r) === w).length;
  const list = (by ? rows.filter((r) => whoOf(r) === by) : [...rows]).sort(newestFirst);
  const visible = more ? list : list.slice(0, SHOWN);
  const filters: [Who | null, PlainKey, number][] = [
    [null, "decide.who.all", rows.length], ["owner", WHO.owner.name, count("owner")], ["project_manager", WHO.project_manager.name, count("project_manager")],
    ["system", WHO.system.name, count("system")],
  ];
  return (
    <>
      <div className="dq-filter" role="group" aria-label={t("decide.who.label")}>
        {filters.map(([k, l, n]) => (
          <button key={l} type="button" aria-pressed={by === k} className={by === k ? "on" : ""} onClick={() => setBy(k)}>
            {t(l)}<span className="num">{n}</span>
          </button>
        ))}
        <span className="dq-filter-note small muted">{t(by ? WHO[by].note : "decide.who.all_note")}</span>
      </div>
      {list.length ? (
        <Box pad={false}>
          <ul className="ilist dq-hist">
            <li className="dq-hist-h" aria-hidden="true">
              <span className="grow">{t("decide.hist.what")}</span>
              <span className="dq-who">{t("decide.hist.who")}</span><span className="dq-res">{t("decide.hist.result")}</span>
              <span className="dq-when">{t("decide.hist.when")}</span>
            </li>
            {visible.map((r) => {
              const w = whoOf(r);
              const { name, tone } = WHO[w];
              const cite = w === "project_manager" ? ruleCite(r) ?? t("decide.rule.any") : r.report?.closed ?? null;
              return (
                <li key={r.id} className={on === r.id ? "on" : ""} aria-current={on === r.id ? "true" : undefined}>
                  <div className="grow">
                    <PanelLink className="t1" to={`/inbox?req=${r.id}`} target={{ key: "req", id: r.id }}>{r.title}</PanelLink>
                    <p className="t2">{[label(r.type), agentName(r.from_agent), where(r), cite].filter(Boolean).join(" · ")}</p>
                  </div>
                  <span className="dq-who"><Chip tone={tone}>{t(name)}</Chip></span>
                  <span className="dq-res"><span className={`tag ${RESULT_TONE[r.status] ? `t-${RESULT_TONE[r.status]}` : ""}`}>{label(r.status)}</span></span>
                  <span className="end dq-when" title={when(decidedAt(r))}>{when(decidedAt(r))}</span>
                </li>
              );
            })}
          </ul>
          {list.length > visible.length && (
            <div className="box-foot"><button type="button" className="link" onClick={() => setMore(true)}>{t("decide.hist.more", { n: list.length - visible.length })}</button></div>
          )}
        </Box>
      ) : searching ? (
        <Hint title={t("decide.inbox.no_match")}>{t("decide.inbox.no_match_done")}</Hint>
      ) : (
        <Hint title={t(by ? WHO[by].none : "decide.hist.none")}>{t("decide.hist.none_body")}</Hint>
      )}
    </>
  );
}

const SEVERITY: Record<string, [PlainKey, Tone]> = { bad: ["decide.alert.failed", "bad"], warn: ["decide.alert.warning", "warn"] };
// Kinds whose `detail` is the stored error as the engine wrote it: the owner reads the server's sentence (`reason`, or
// the title), and the raw text folds under Technical details, as Model calls keeps it.
const RAW_DETAIL = new Set(["task_failed", "job_failed", "project_failed", "budget_stop"]);
/** The plain line under an alert's title, and the stored error behind it when that says something else. */
function alertLines(a: Row): { said: string | null; raw: string | null } {
  const said = (a.kind === "task_failed" && !a.handled) || a.kind === "project_failed" ? a.reason ?? null
    : RAW_DETAIL.has(a.kind) || a.kind === "budget_warning" || a.kind === "system_problem" ? null : a.detail ?? null;
  const raw = RAW_DETAIL.has(a.kind) && a.detail && a.detail !== said && a.detail !== a.reason ? a.detail : null;
  return { said, raw };
}
/** An older server sends no `retryable`: its retry is offered as before. */
const canRetry = (a: Row) => a.retryable !== false;

/** Failures and stops, each with the action that clears it. */
function Alerts({ alerts, onChange }: { alerts: Row[]; onChange: () => void }) {
  // what a retry or a rerun queued does next, as the task panel says it: nothing, until the worker runs
  const down = useWorker().down;
  if (!alerts.length) {
    return <Hint title={t("decide.alert.none_title")}>{t("decide.alert.none")}</Hint>;
  }
  const sorted = [...alerts].sort((a, b) => Number(alertTone(b) !== "calm") - Number(alertTone(a) !== "calm") || Number(b.severity === "bad") - Number(a.severity === "bad"));
  const counts = alertCounts(alerts);
  return (
    <Box pad={false}>
      <p className="t2 dq-alerts-sum">{t("decide.alert.summary", { n: counts.need })}{counts.redoing ? t("decide.alert.summary_redoing", { n: counts.redoing }) : ""}</p>
      <ul className="ilist dq-alerts">
        {sorted.map((a, i) => {
          const [sev, tone] = SEVERITY[a.severity] ?? ["decide.alert.other", "info" as Tone];
          const { said, raw } = alertLines(a);
          return (
            <li key={`${a.kind}-${a.task_id ?? a.dev_job_id ?? a.job_id ?? a.project_id ?? i}`}>
              <span className="dq-sev"><Chip tone={tone}>{t(sev)}</Chip></span>
              <div className="grow">
                <ItemLink to={alertHref(a)} className="t1 dq-wrap">{a.kind === "system_problem" ? <Fix text={alertText(a)} /> : alertText(a)}</ItemLink>
                {said && <p className="t2 dq-wrap clamp2">{said}</p>}
                {a.hint && <p className="t2 dq-wrap">{a.hint}</p>}
                {raw && (
                  <details className="dq-raw">
                    <summary>{t("decide.alert.technical")}</summary>
                    <p className="mono pre-wrap">{raw}</p>
                  </details>
                )}
                <p className="t2">{a.project_id && <><RefLink code={`P-${a.project_id}`} /> · </>}{a.owner && <>{agentName(a.owner)} · </>}{a.at && <span title={when(a.at)}>{ago(a.at)}</span>}</p>
              </div>
              <div className="dq-act">
                {(a.kind === "task_failed" || a.kind === "job_failed") && budgetShort(a) && <ItemLink className="btn-primary" to={budgetHref(a)}>{budgetAction(a)}</ItemLink>}
                {a.kind === "task_failed" && !budgetShort(a) && canRetry(a) && <ActionButton run={() => post(`/tasks/${a.task_id}/retry`, {}).then(onChange)} done={t(down ? "decide.alert.retry_marked_held" : "decide.alert.retry_marked")}>{t("decide.alert.retry")}</ActionButton>}
                {a.kind === "job_failed" && a.retryable && <ActionButton run={() => post(`/jobs/${a.job_id}/retry`, {}).then(onChange)} done={t(down ? "decide.alert.retry_queued_held" : "decide.alert.retry_queued")}>{t("decide.alert.retry")}</ActionButton>}
                {a.kind === "job_failed" && a.job_kind === "execute_action" && <ActionButton className="primary" run={() => post(`/jobs/${a.job_id}/re-request`, {}).then(onChange)} done={t("decide.alert.re_requested")}>{t("decide.alert.re_request")}</ActionButton>}
                {a.kind === "job_failed" && <ActionButton run={() => post(`/jobs/${a.job_id}/dismiss`, {}).then(onChange)}>{t("decide.alert.dismiss")}</ActionButton>}
                {(a.kind === "project_failed" || a.kind === "run_stalled") && <ActionButton className="primary" run={() => post(`/projects/${a.project_id}/retry`, {}).then(onChange)} done={t(down ? "decide.alert.rerun_queued_held" : "decide.alert.rerun_queued")}>{t("decide.alert.rerun")}</ActionButton>}
                {a.kind === "project_paused" && <ActionButton className="primary" run={() => post(`/projects/${a.project_id}/resume`, {}).then(onChange)}>{t("decide.alert.resume")}</ActionButton>}
                {a.kind === "dev_failed" && (a.actions?.length
                  ? <DevJobActions jobId={a.dev_job_id} actions={a.actions} onChange={onChange} />
                  : canRetry(a) && <ActionButton run={() => post(`/dev-jobs/${a.dev_job_id}/retry`, {}).then(onChange)} done={t("decide.alert.new_attempt")}
                      disabled={!!a.retry_wait} title={a.retry_wait ?? undefined}>{t("decide.alert.retry")}</ActionButton>)}
                {a.kind === "dev_stalled" && canRetry(a) && <ActionButton run={() => post(`/dev-jobs/${a.dev_job_id}/retry`, {}).then(onChange)} done={t("decide.alert.new_attempt")}>{t("decide.alert.retry")}</ActionButton>}
                {a.kind === "dev_delivered" && a.request_id && <ItemLink className="btn-primary" to={alertHref(a)}>{t("decide.alert.reapprove_merge")}</ItemLink>}
                {a.kind === "dev_delivered" && <DevJobActions jobId={a.dev_job_id} actions={a.actions} onChange={onChange} />}
                {a.kind === "budget_stop" && <ItemLink className="btn-primary" to={alertHref(a)}>{budgetAction(a)}</ItemLink>}
                {a.kind === "budget_warning" && <Link className="btn-primary" to={alertHref(a)}>{t("decide.alert.adjust_budget")}</Link>}
                {a.kind === "month_budget_warning" && <Link className="btn-ghost" to="/audit/costs">{t("decide.alert.see_limit")}</Link>}
              </div>
            </li>
          );
        })}
      </ul>
    </Box>
  );
}
