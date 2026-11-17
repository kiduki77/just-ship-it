import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Row } from "../api";
import { useBusinessLive, useBusinessSelection } from "../business";
import { Icon } from "../components/icons";
import { Board, BoardCard, BoardColumn, Box, FirstRun, Hint, PageHead, Status, Tone, useView, ViewSwitch } from "../components/kit";
import { PanelLink, PanelTarget, usePanel } from "../components/panel";
import { CODE_SPAN } from "../components/task";
import { ElsewhereLine, KickoffLine, QuietError, RowItem, RowList, SkeletonRows, waitingTag } from "../components/today";
import { ago, Loading, when } from "../components/ui";
import { PlainKey, t } from "../i18n";
import { actionLabel, dayText, dueText, metaText, minutesText, panelTarget, stepsText, TodayItem } from "../today";
import { Cur, useBoardKeys } from "./Approvals";
import "./decide.css";
import "./todos.css";

const ORDER = ["check", "post", "publish", "contact", "follow_up", "onboard", "referral", "participate", "spend", "account", "other"];
const TIER_RANK: Record<string, number> = { blocking: 0, overdue: 1, today: 2, other: 3 };
/** Amber, not red: an item waiting on the owner or past its date is the owner's turn, not a failure. */
const TIER_TAG: Record<string, [PlainKey, Tone]> = {
  blocking: ["decide.todo.tier.blocking", "warn"], overdue: ["decide.todo.tier.overdue", "warn"], today: ["decide.todo.tier.today", "warn"],
};
const WEEK = 7 * 86_400_000;
const CLOSED_MAX = 12;
const JUST_MS = 6000;

const isTask = (it: TodayItem) => /^OT-\d+$/.test(it.ref);
/** The kind in the owner's language, as the server words it. */
const kindOf = (task: Row): string => task.kind_label ?? task.kind;
const ms = (v?: string | null) => (v ? new Date(v).getTime() : NaN);
const closedFirst = (a: Row, b: Row) => ms(b.done_at) - ms(a.done_at);
const closedText = (task: Row) =>
  t(task.status === "DONE" ? "decide.todo.done_ago" : "decide.todo.skipped_ago", { ago: ago(task.done_at) });
/** A closed column's hint once it holds more than it shows. */
const latest = (total: number) => (total > CLOSED_MAX ? t("decide.todo.col.latest", { shown: CLOSED_MAX, total }) : undefined);

function TierTag({ tier }: { tier?: string }) {
  const tag = tier ? TIER_TAG[tier] : undefined;
  return tag ? <span className={`tag t-${tag[1]}`}>{t(tag[0])}</span> : null;
}

/** A decision or a non-task item from the owner's list (kickoff, brief, idea), as a card. */
function ItemCard({ it, on }: { it: TodayItem; on: boolean }) {
  const decision = it.kind === "decision";
  // a decision expires on that day; only a to-do is due
  const due = dueText(it.meta.due_at, new Date(), decision);
  return (
    <Cur on={on}>
      <BoardCard title={it.title} to={it.link} panel={panelTarget(it)}
        tags={<>{!decision && <span className="tag t-info">{it.label}</span>}<TierTag tier={it.tier} /></>}
        sub={it.meta.reason}
        foot={<>
          {due && <span className={`dq-fact ${it.tier === "overdue" ? "dq-late" : ""}`}><Icon name="clock" size={12} />{due}</span>}
          {dayText(it.day) && <span className="dq-fact">{dayText(it.day)}</span>}
          <span className="sp">{t("home.list.about_min", { n: it.meta.estimate_min })}</span>
        </>} />
    </Cur>
  );
}

/** An owner task: what kind, why it is the owner's, when it is due or when it was closed. */
function TaskCard({ task, item, on }: { task: Row; item?: TodayItem; on: boolean }) {
  const open = task.status === "OPEN";
  const due = open ? dueText(task.due_at) : null;
  const card = task.card ?? {};
  const sub = open ? item?.meta.reason ?? card.channel_name ?? card.what : task.result?.note ?? card.channel_name ?? card.what;
  return (
    <Cur on={on}>
      <BoardCard title={task.title} to={`/my-tasks?ot=${task.id}`} panel={{ key: "task", id: task.id }}
        tags={<><span className="tag">{kindOf(task)}</span>{open && <TierTag tier={item?.tier} />}</>}
        sub={typeof sub === "string" ? sub.replace(CODE_SPAN, "$1") : undefined}
        foot={<>
          {open && due && <span className={`dq-fact ${item?.tier === "overdue" ? "dq-late" : ""}`}><Icon name="clock" size={12} />{due}</span>}
          {open && item && <span className="dq-fact">{t("home.list.about_min", { n: item.meta.estimate_min })}</span>}
          {open && item && stepsText(item) && <span className="dq-fact">{stepsText(item)}</span>}
          {open && item && dayText(item.day) && <span className="dq-fact">{dayText(item.day)}</span>}
          {open
            ? <span className="sp" title={when(task.created_at)}>{t("decide.todo.added", { ago: ago(task.created_at) })}</span>
            : <span className="sp" title={when(task.done_at)}>{closedText(task)}</span>}
        </>} />
    </Cur>
  );
}

/**
 * A to-do that just left the open list, from here or from any other screen, kept for a few seconds so the owner sees
 * where it went.
 */
function useJustFinished(tasks: Row[] | undefined): Row | null {
  const before = useRef<Map<number, string> | null>(null);
  const [just, setJust] = useState<Row | null>(null);
  useEffect(() => {
    if (!tasks) return;
    const prev = before.current;
    before.current = new Map(tasks.map((x) => [x.id, x.status]));
    const left = prev && tasks.find((x) => x.status !== "OPEN" && prev.get(x.id) === "OPEN");
    if (left) setJust(left);
  }, [tasks]);
  useEffect(() => {
    if (!just) return;
    const timer = window.setTimeout(() => setJust(null), JUST_MS);
    return () => window.clearTimeout(timer);
  }, [just]);
  return just;
}

function JustLine({ task }: { task: Row }) {
  return <p className="todo-just">{t(task.status === "DONE" ? "decide.todo.just_done" : "decide.todo.just_skipped", { title: task.title })}</p>;
}

/** Where owner tasks come from, for the owner who has none yet. */
function Sources() {
  return (
    <div className="dq-after">
      <Hint title={t("decide.todo.sources_title")}
        action={
          <ul className="dq-sources">
            <li><Icon name="check" /><span><b>{t("decide.todo.src_post")}</b>{t("decide.todo.src_post_body")}</span></li>
            <li><Icon name="routines" /><span><b>{t("decide.todo.src_weekly")}</b>{t("decide.todo.src_weekly_body")}</span></li>
            <li><Icon name="users" /><span><b>{t("decide.todo.src_people")}</b>{t("decide.todo.src_people_body")}</span></li>
          </ul>
        }>
        {t("decide.todo.sources_body")}
      </Hint>
    </div>
  );
}

export default function MyTasks() {
  const { businesses, current, today: data, todayError: error, reloadToday: reload, error: listError, reload: reloadList } = useBusinessSelection();
  const { data: owned, error: ownedError, reload: reloadOwned } = useBusinessLive<Row>(current ? "/owner-tasks" : null, ["owner_tasks"]);
  const [view, setView] = useView("my-tasks");
  const panel = usePanel().current;
  const boardRef = useBoardKeys(view === "board");
  const just = useJustFinished(owned?.items);
  const [showClosed, setShowClosed] = useState(false);

  if (!businesses) {
    return (
      <div className="owner-page dq-page">
        <PageHead title={t("decide.todo.title")} />
        {listError ? <Loading error={listError} failure="server" retry={reloadList} /> : <SkeletonRows />}
      </div>
    );
  }
  if (!current) {
    return (
      <div className="owner-page dq-page">
        <PageHead title={t("decide.todo.title")} />
        <FirstRun>{t("decide.todo.firstrun")}</FirstRun>
      </div>
    );
  }

  const now = Date.now();
  const items = data?.items ?? [];
  const fromToday = new Map(items.map((it) => [it.ref, it]));
  const decisions = items.filter((it) => it.kind === "decision");
  const others = items.filter((it) => !isTask(it) && it.kind !== "decision");
  const all: Row[] = owned?.items ?? [];
  const counts: Record<string, number> = owned?.counts ?? { OPEN: 0, DONE: 0, SKIPPED: 0 };
  const known = new Set(all.map((task) => task.ref ?? `OT-${task.id}`));
  // the home's list also carries tasks that belong to no business; keep them so the two screens agree
  const stray = items.filter((it) => isTask(it) && !known.has(it.ref));
  const rank = (task: Row) => TIER_RANK[fromToday.get(task.ref)?.tier ?? "other"];
  const openTasks = all.filter((task) => task.status === "OPEN")
    .sort((a, b) => rank(a) - rank(b) || (ms(a.due_at) || Infinity) - (ms(b.due_at) || Infinity) || a.id - b.id);
  const done = all.filter((task) => task.status === "DONE").sort(closedFirst);
  const skipped = all.filter((task) => task.status === "SKIPPED").sort(closedFirst);
  const doneWeek = done.filter((task) => now - ms(task.done_at) < WEEK).length;
  // the tile counts exactly what the To do column shows
  const openCount = openTasks.length + stray.length + others.length;
  const taskMin = items.filter((it) => it.kind !== "decision").reduce((n, it) => n + it.meta.estimate_min, 0);
  const late = items.filter((it) => isTask(it) && it.tier === "overdue").length;
  const blockingDecisions = decisions.filter((it) => it.tier === "blocking").length;
  const onRef = panel ? `${panel.key === "req" ? "REQ" : "OT"}-${panel.id}` : null;
  // the primary button opens the most urgent to-do; a decision only when no to-do is open
  const firstTask: Row | undefined = openTasks[0];
  const next = items.find((it) => it.kind !== "decision" && panelTarget(it)) ?? items.find((it) => panelTarget(it));
  const nextTarget: PanelTarget | null = next ? panelTarget(next) : null;
  const loaded = !!data && !!owned;
  const nothingOpen = openCount + decisions.length === 0;
  // with a panel open the board keeps the open work in view; the closed columns fold to one line
  const fold = !!panel && !showClosed;

  const sub = !loaded
    ? t("decide.todo.loading")
    : openCount || decisions.length
      ? t("decide.todo.open", { n: openCount }) + (taskMin ? t("decide.todo.time", { time: minutesText(taskMin) }) : "")
        + (decisions.length ? t("decide.todo.decisions", { n: decisions.length }) : "") + "."
      : t("decide.todo.none");
  const status: Status = {
    tone: !loaded ? "muted" : openCount || decisions.length ? "warn" : "ok",
    text: <>{sub}{late ? t("decide.todo.late", { n: late }) : null}{blockingDecisions ? t("decide.todo.blocking", { n: blockingDecisions }) : null}</>,
  };

  const taskCard = (task: Row) => <TaskCard key={task.id} task={task} item={fromToday.get(task.ref)} on={onRef === task.ref} />;
  const itemCard = (it: TodayItem) => <ItemCard key={it.ref} it={it} on={onRef === it.ref} />;
  const allColumns: BoardColumn[] = [
    { key: "todo", label: t("decide.todo.col.todo"), tone: "run", count: openCount,
      items: [...(just ? [<JustLine key="just" task={just} />] : []), ...openTasks.map(taskCard), ...stray.map(itemCard), ...others.map(itemCard)],
      hint: t("decide.todo.col.todo_hint"), empty: t("decide.todo.col.todo_empty") },
    { key: "decide", label: t("decide.todo.col.decide"), tone: "warn", items: decisions.map(itemCard),
      hint: t("decide.todo.col.decide_hint"), empty: t("decide.todo.col.decide_empty") },
    { key: "done", label: t("decide.todo.col.done"), tone: "ok", items: done.slice(0, CLOSED_MAX).map(taskCard),
      hint: latest(done.length), empty: t("decide.todo.col.done_empty") },
    { key: "skipped", label: t("decide.todo.col.skipped"), tone: "muted", items: skipped.slice(0, CLOSED_MAX).map(taskCard),
      hint: latest(skipped.length), empty: t("decide.todo.col.skipped_empty") },
  ];
  const columns = fold ? allColumns.filter((c) => c.key !== "done" && c.key !== "skipped") : allColumns;

  return (
    <div className="owner-page dq-page">
      <PageHead title={t("decide.todo.title")} status={status}
        sub={owned ? t(counts.SKIPPED ? "decide.todo.week" : "decide.todo.week_none", { done: doneWeek, skipped: counts.SKIPPED ?? 0 }) : null}
        actions={<>
          {decisions.length > 0 && <Link className="btn-ghost" to="/inbox"><Icon name="inbox" />{t("decide.todo.board_link")}</Link>}
          {firstTask ? (
            <PanelLink className="btn-primary" to={`/my-tasks?ot=${firstTask.id}`} target={{ key: "task", id: firstTask.id }}>{t("decide.todo.open_first")}<Icon name="arrow" /></PanelLink>
          ) : next && nextTarget && (
            <PanelLink className="btn-primary" to={next.link} target={nextTarget}>{t("decide.todo.open_first")}<Icon name="arrow" /></PanelLink>
          )}
        </>} />
      <ElsewhereLine today={data} what="todos" to="/my-tasks" />
      <KickoffLine business={current} />

      {error && <QuietError text={data ? t("decide.todo.refresh_failed") : t("decide.todo.today_failed")} retry={reload} />}
      {ownedError && <QuietError text={owned ? t("decide.todo.history_refresh_failed") : t("decide.todo.history_failed")} retry={reloadOwned} />}


      <div className="toolbar">
        <span className="small muted"><kbd>j</kbd><kbd>k</kbd> {t(view === "board" ? "decide.keys.move_card" : "decide.keys.move")} · <kbd>Enter</kbd> {t("decide.keys.open")}</span>
        <span className="sp"><ViewSwitch value={view} onChange={setView} /></span>
      </div>

      {!loaded ? (!error && !ownedError && <SkeletonRows />) : view === "board" ? (
        <div ref={boardRef}>
          <Board label={t("decide.todo.board")} columns={columns} />
          {panel && (done.length > 0 || skipped.length > 0) && (
            <p className="quiet-line todo-closed">
              {t("decide.todo.closed_folded", { done: done.length, skipped: skipped.length })}{" "}
              <button type="button" className="link" aria-expanded={!fold} onClick={() => setShowClosed(fold)}>
                {t(fold ? "decide.todo.closed_show" : "decide.todo.closed_hide")}
              </button>
            </p>
          )}
          {nothingOpen && <Sources />}
        </div>
      ) : (
        <>
          {just && <JustLine task={just} />}
          <TaskList decisions={decisions} openTasks={openTasks} stray={stray} others={others} done={done} skipped={skipped}
            fromToday={fromToday} onRef={onRef} empty={nothingOpen} now={now} />
        </>
      )}
    </div>
  );
}

function TaskList({ decisions, openTasks, stray, others, done, skipped, fromToday, onRef, empty, now }: {
  decisions: TodayItem[]; openTasks: Row[]; stray: TodayItem[]; others: TodayItem[]; done: Row[]; skipped: Row[];
  fromToday: Map<string, TodayItem>; onRef: string | null; empty: boolean; now: number;
}) {
  const nowDate = new Date(now);
  const itemRow = (it: TodayItem, labeled: boolean) => (
    <RowItem key={it.ref} rowRef={it.ref} on={it.ref === onRef} label={labeled ? it.label : undefined}
      tags={it.tier === "blocking" ? [waitingTag()] : it.tier === "overdue" ? [[t("decide.todo.tier.overdue"), "warn"]] : []}
      title={it.title} meta={metaText(it, nowDate)} to={it.link} target={panelTarget(it)} button={actionLabel(it.action)} />
  );
  const taskRow = (task: Row) => {
    const it = fromToday.get(task.ref);
    const open = task.status === "OPEN";
    const meta = open
      ? it ? metaText(it, nowDate) : [dueText(task.due_at, nowDate), t("decide.todo.added", { ago: ago(task.created_at) })].filter(Boolean).join(" · ")
      : [closedText(task), task.result?.note].filter(Boolean).join(" · ");
    return (
      <RowItem key={task.id} rowRef={task.ref} on={task.ref === onRef} label={open ? undefined : kindOf(task)}
        tags={open && it?.tier === "overdue" ? [t("decide.todo.tier.overdue")] : []}
        title={task.title} meta={meta} to={`/my-tasks?ot=${task.id}`} target={{ key: "task", id: task.id }}
        button={open ? t("decide.todo.row.open") : t("decide.todo.row.view")} />
    );
  };
  const kinds = ORDER.map((k) => openTasks.filter((task) => task.kind === k)).filter((g) => g.length);
  const extra = openTasks.filter((task) => !ORDER.includes(task.kind));
  // a kickoff, brief or stage gate that holds up work or is due is the owner's turn, not something "also worth a look"
  const turns = others.filter((it) => it.tier !== "other");
  const rest = others.filter((it) => it.tier === "other");
  const head = (key: string, title: string, n: number) => <li key={`h-${key}`} className="grp-h"><h2>{title} <span className="num">{n}</span></h2></li>;
  const total = decisions.length + openTasks.length + stray.length + others.length + done.length + skipped.length;
  if (!total) return <Sources />;
  return (
    <>
      <Box className="dq-listbox">
        <RowList current={onRef} count={total}>
          {decisions.length > 0 && [head("decide", t("decide.todo.col.decide"), decisions.length), ...decisions.map((it) => itemRow(it, false))]}
          {turns.length > 0 && [head("turns", t("decide.todo.grp.turns"), turns.length), ...turns.map((it) => itemRow(it, true))]}
          {kinds.map((g) => [head(g[0].kind, kindOf(g[0]), g.length), ...g.map(taskRow)])}
          {extra.length > 0 && [head("extra", t("decide.todo.grp.other"), extra.length), ...extra.map(taskRow)]}
          {stray.length > 0 && [head("stray", t("decide.todo.grp.stray"), stray.length), ...stray.map((it) => itemRow(it, true))]}
          {rest.length > 0 && [head("others", t("decide.todo.grp.others"), rest.length), ...rest.map((it) => itemRow(it, true))]}
          {done.length > 0 && [head("done", t("decide.todo.col.done"), done.length), ...done.slice(0, CLOSED_MAX).map(taskRow)]}
          {skipped.length > 0 && [head("skipped", t("decide.todo.col.skipped"), skipped.length), ...skipped.slice(0, CLOSED_MAX).map(taskRow)]}
        </RowList>
      </Box>
      {empty && <Sources />}
    </>
  );
}
