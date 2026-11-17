import { ReactNode, useState } from "react";
import { ItemLink } from "./panel";
import { Link } from "react-router-dom";
import { Row } from "../api";
import { cleanName, useBusinessSelection } from "../business";
import { locale, lookup, PlainKey, t, tx } from "../i18n";
import { agentName, artifactKindLabel, entityLabel, label, prose, verbLabel } from "../labels";
import { Empty, RefLink, refHref, Time, toneOf } from "./ui";

/** The business screen's tab for a record that lives there. */
const BUSINESS_TAB: Record<string, string> = { brief: "brief", person: "people", bet: "bets", idea: "ideas" };

/** Where an activity row points: the record it is about, or the screen that holds it. Null when nothing does. */
export function eventHref(e: Row): string | null {
  const id = e.entity_id;
  const pid = e.project_id;
  switch (e.entity) {
    case "project": return `/projects/${id}`;
    case "task": return refHref("T", id, pid);
    case "request": return refHref("REQ", id, pid);
    case "decision": return refHref("DEC", id, pid);
    case "meeting": return refHref("M", id, pid);
    case "dev_job": return refHref("J", id, pid);
    case "artifact": return pid ? `/projects/${pid}/docs?doc=${id}` : null;
    case "skill": return "/skills";
    case "company": return e.verb === "settings" ? "/settings" : "/audit/costs";
    case "owner_task": return id ? refHref("OT", id) : null;
    case "brief": case "person": case "bet": case "idea": return `/business?tab=${BUSINESS_TAB[e.entity]}`;
    case "cycle": case "stage": return "/";
    case "spec": return "/engineering";
    case "settings": return "/settings";
    default: return null;
  }
}

/** Runs that found nothing say nothing: they are routine, and a feed of zeros hides the events that matter. */
const FIND_RUNS = new Set(["people_find_ran", "threads_find_ran"]);
export function isNoise(e: Row): boolean {
  return FIND_RUNS.has(e.verb) && !Number(e.data?.added ?? 0);
}

/** The sentence a task, decision, build, stage or cycle change reads as. A code the table lacks falls to the generic
 * "now <state>" line, and a row whose data predates these sentences keeps the summary it was written with. */
const TASK_SAY = {
  RUNNING: "common.activity.task.running", DONE: "common.activity.task.done", FAILED: "common.activity.task.failed",
  REVIEW: "common.activity.task.review", REWORK: "common.activity.task.rework", READY: "common.activity.task.ready",
  CANCELLED: "common.activity.task.cancelled",
} as const;
const DECISION_SAY = {
  APPROVED: "common.activity.decision.approved", REJECTED: "common.activity.decision.rejected",
  DEFERRED: "common.activity.decision.deferred", ANSWERED: "common.activity.decision.answered",
  AUTO_APPROVED: "common.activity.decision.auto", DENIED_BY_POLICY: "common.activity.decision.denied",
  EXPIRED: "common.activity.decision.expired",
  // a request goes back to PENDING only when the owner reopens it (requests.reopen)
  PENDING: "common.activity.decision.reopened",
} as const;
const BUILD_SAY = {
  DELIVERED: "common.activity.build.delivered", ACCEPTED: "common.activity.build.accepted",
  MERGED: "common.activity.build.merged", FAILED: "common.activity.build.failed",
} as const;
const STAGE_SAY = {
  stage_running: "common.activity.stage.running", stage_closing: "common.activity.stage.closing",
  stage_awaiting_owner: "common.activity.stage.awaiting_owner", stage_done: "common.activity.stage.done",
  stage_skipped: "common.activity.stage.skipped", stage_stopped: "common.activity.stage.stopped",
  stage_planned: "common.activity.stage.planned",
} as const;
const CYCLE_SAY = {
  cycle_opened: "common.activity.cycle.opened", cycle_started: "common.activity.cycle.started",
  cycle_revise: "common.activity.cycle.revise", cycle_stopped: "common.activity.cycle.stopped",
  cycle_closed: "common.activity.cycle.closed",
} as const;

/** What the dashboard's feed shows: the changes an owner follows (an agent starting or finishing a task, a decision
 * raised or settled, a build delivered, a stage or cycle moving, the owner's own to-dos and ideas). Everything else is
 * the system's bookkeeping, and the Activity page keeps it. */
const FEED_TASK = new Set(["RUNNING", "DONE", "FAILED", "REWORK"]);
const FEED_DECISION = new Set(Object.keys(DECISION_SAY));
const FEED_VERBS = new Set([
  "stage_running", "stage_awaiting_owner", "stage_done", "stage_skipped", "stage_stopped", ...Object.keys(CYCLE_SAY),
  "owner_task_created", "owner_task_done", "owner_task_skipped", "idea_posted", "idea_triaged", "people_found", "bet_retro",
  "kickoff_done", "launch_fact_confirmed", "routine_started", "directive", "comment",
]);
export function isOwnerEvent(e: Row): boolean {
  const to = eventData(e).to ?? "";
  switch (e.entity) {
    case "task": return e.verb === "status" && FEED_TASK.has(to);
    case "request": return e.verb === "created" || (e.verb === "status" && FEED_DECISION.has(to));
    case "dev_job": return e.verb === "status" && to in BUILD_SAY;
    case "company": case "agent": return e.verb === "stopped" || e.verb === "resumed";
    default: return FEED_VERBS.has(e.verb);
  }
}

/**
 * An event's data as the writer stored it (services/activity.py), with the fields read as words kept only when they are
 * words. Nothing types the stored JSON: an `edited` row stores `{"title": true}` to mean "the title changed", and a
 * sentence built from it must not take `true` for a title. A number reads as its digits.
 */
const WORD_FIELDS = ["title", "ref", "owner", "to", "kind", "name", "platform", "outcome", "section", "mode", "status"];
export function eventData(e: Row): Row {
  const d: Row = e.data && typeof e.data === "object" && !Array.isArray(e.data) ? { ...e.data } : {};
  for (const k of WORD_FIELDS) {
    if (typeof d[k] === "number") d[k] = String(d[k]);
    else if (k in d && typeof d[k] !== "string") delete d[k];
  }
  return d;
}

const NUMBERED = new Set(["cycle", "brief"]);
const quoted = (title: unknown) => String(title ?? "").trim();
/** An outside action by what it does ("Log a conversation"); a kind the catalog lacks is shown as it is. */
const actionName = (kind: unknown) => lookup(`settings.rules.kind.${String(kind)}`) ?? String(kind ?? "");

/** One readable sentence for an event, in the current language; codes become labels. */
export function eventText(e: Row): string {
  const d = eventData(e);
  if (FIND_RUNS.has(e.verb)) {
    const n = Number(d.added ?? 0);
    return e.verb === "people_find_ran" ? t("common.activity.found_people", { n }) : t("common.activity.found_threads", { n });
  }
  // a cycle's or plan's ref is a bare number ("#3", "v2"), named here by its entity; older rows wrote it out in Korean
  const bare = NUMBERED.has(e.entity) && /^[#v]\d+$/.test(d.ref ?? "");
  // a record reads as its title; its code ("J-9", "REQ-104", a spec key) means nothing to the owner, so one with no
  // title reads as its kind
  const named = (ref: string) => (bare ? `${ref}${d.title ? ` ${d.title}` : ""}` : d.title || ref).trim();
  const what = named(bare ? `${entityLabel(e.entity)} ${d.ref}` : entityLabel(e.entity) || d.ref || "");
  const who = agentName(d.owner ?? e.actor);
  if (e.verb in STAGE_SAY && d.title) return t(STAGE_SAY[e.verb as keyof typeof STAGE_SAY], { title: quoted(d.title) });
  if (e.verb in CYCLE_SAY && d.n != null) return t(CYCLE_SAY[e.verb as keyof typeof CYCLE_SAY], { n: d.n });
  if (e.verb === "status" || e.verb === "phase") {
    const title = quoted(d.title);
    const to = String(d.to ?? "");
    if (e.entity === "task" && title && to in TASK_SAY) return t(TASK_SAY[to as keyof typeof TASK_SAY], { who, title });
    if (e.entity === "request" && title && to in DECISION_SAY) return t(DECISION_SAY[to as keyof typeof DECISION_SAY], { title });
    if (e.entity === "dev_job" && to in BUILD_SAY) {
      return t(BUILD_SAY[to as keyof typeof BUILD_SAY], { title: title || t("common.activity.build.untitled") });
    }
    // a record whose title is gone still reads by its kind and code ("Task T-12"), never by its kind alone
    // an outside action that finished says where it went: only into the outbox, or really sent (the server's outcome)
    if (e.entity === "action" && to === "done" && (d.outcome === "outbox" || d.outcome === "sent")) {
      return t(d.outcome === "outbox" ? "audit.event.action_outbox" : "audit.event.action_sent", { what: actionName(d.title) });
    }
    const thing = e.entity === "action" ? actionName(d.title)
      : title ? `${entityLabel(e.entity)} “${title}”` : [entityLabel(e.entity), d.ref].filter(Boolean).join(" ");
    return t("common.activity.state", { what: thing || t("common.activity.record"), status: label(to) });
  }
  if (e.entity === "company" && e.verb === "stopped") return t("common.activity.company_stopped");
  if (e.entity === "company" && e.verb === "resumed") return t("common.activity.company_resumed");
  if (e.entity === "agent" && e.verb === "stopped") return t("common.activity.agent_stopped", { name: agentName(d.agent) });
  if (e.entity === "agent" && e.verb === "resumed") return t("common.activity.agent_resumed", { name: agentName(d.agent) });
  if (e.verb === "people_found" && d.title) return t("common.activity.person_found", { name: quoted(d.title), platform: String(d.platform ?? "") });
  if (e.verb === "created") {
    if (e.entity === "action") return t("common.activity.action", { what: actionName(d.title) });
    if (e.entity === "request") return t(d.blocking ? "common.activity.request_blocking" : "common.activity.request", { who: agentName(e.actor), what });
    if (e.entity === "artifact") return t("common.activity.artifact", { kind: artifactKindLabel(d.kind), title: d.title ?? "" });
    // the sentence names the entity, so the ref goes bare: "Plan created: v1", not "Plan Plan v1"
    const entity = entityLabel(e.entity) || t("common.activity.record");
    const made = named(bare ? d.ref ?? "" : "");
    return made ? t("common.activity.created", { entity, what: made }) : t("common.activity.created_bare", { entity });
  }
  // an older entry, or one whose words are not in the catalog, keeps the summary in the language it was written in
  return FROM_DATA[e.verb]?.(d) ?? e.summary;
}

const jobName = (kind: unknown) => lookup(`company.job.${String(kind)}`) ?? String(kind ?? "");
const secretName = (name: unknown) => lookup(`company.secret.${String(name)}`) ?? t("company.secret.default");

/** The owner's name for each settings field, by the path a change stores ("usage/day_usd_eq"). A path under a
 * field that holds a table ("policy/overrides/<action>") is named by that field. tests/test_i18n_live.py fails when a
 * field of `app_settings.AppSettings` has no name here. */
const SETTING_FIELD: Record<string, PlainKey> = {
  "general/language": "settings.language.group", "owner/strengths": "settings.about.group",
  "usage/subscription": "settings.usage.subscription", "usage/day_usd_eq": "settings.usage.day_cap",
  "usage/week_usd_eq": "settings.usage.week_cap", "usage/capacity_week_usd_eq": "settings.usage.capacity",
  "usage/timezone": "settings.usage.timezone", "usage/owner_reserve_pct": "settings.usage.reserve",
  "connectors/email": "settings.outgoing.email", "connectors/slack": "settings.outgoing.slack",
  "connectors/github": "settings.outgoing.github", "connectors/cloudflare": "settings.outgoing.cloudflare",
  "connectors/calendar": "settings.outgoing.calendar", "connectors/survey": "settings.outgoing.survey",
  "policy/overrides": "settings.rules.form",
  "notifications/slack_channel": "settings.notify.channel", "notifications/kinds": "settings.notify.when",
  "notifications/briefing_time": "settings.notify.briefing", "notifications/quiet_hours": "settings.notify.quiet",
  "notifications/console_url": "settings.notify.url",
  "bets/max_running": "settings.bets.max_running", "bets/weekly_post_cap": "settings.bets.weekly_post_cap",
  "bets/max_routines": "settings.bets.max_routines", "bets/failed_grace_minutes": "settings.bets.failed_grace_minutes",
  "bets/window_days": "settings.bets.window_days",
};

/** Settings sections whose screen name is not their stored name. */
const SECTION_KEY: Record<string, string> = { general: "language", owner: "about" };

/** A changed field's name: the longest part of its path the table names ("connectors/slack/mode" is Slack). */
function settingName(path: string): string {
  const parts = String(path).split("/");
  for (let n = parts.length; n > 1; n--) {
    const key = SETTING_FIELD[parts.slice(0, n).join("/")];
    if (key) return t(key);
  }
  return lookup(`settings.section.${SECTION_KEY[parts[0]] ?? parts[0]}`) ?? t("company.secret.default");
}

/** Owner and system actions whose data says what happened, so the sentence reads in the current language. Null when
 * the row predates the data, and the summary stands. */

const FROM_DATA: Record<string, (d: Row) => string | null> = {
  secret_set: (d) => (d.name ? t("company.event.secret_set", { name: secretName(d.name) }) : null),
  secret_cleared: (d) => (d.name ? t("company.event.secret_cleared", { name: secretName(d.name) }) : null),
  connection_tested: (d) => (d.mode ? t(d.mode === "outbox" ? "company.event.connection_tested_outbox" : "company.event.connection_tested") : null),
  settings: (d) => {
    const changes: Row[] = d.changes ?? [];
    const language = changes.find((c) => c.key === "general/language");
    if (language && changes.length === 1) return lookup(`company.event.language.${language.to}`) ?? null;
    const section = lookup(`settings.section.${SECTION_KEY[d.section] ?? d.section}`);
    const fields = [...new Set(changes.map((c) => settingName(c.key)))].join(", ");
    return section && fields ? t("company.event.settings", { section, fields }) : null;
  },
  retried: (d) => (d.kind ? t("company.event.retried", { job: jobName(d.kind), id: d.job_id }) : null),
  dismissed: (d) => (d.kind ? t("company.event.dismissed", { job: jobName(d.kind), id: d.job_id }) : null),
  // the owner cancelled a queued job (POST /jobs/{id}/cancel): said in the current language, not the one it was saved in
  cancelled: (d) => (d.kind && d.job_id ? t("audit.event.job_cancelled", { job: jobName(d.kind), id: d.job_id }) : null),
  re_requested: (d) => (d.ref ? t("company.event.re_requested", { ref: d.ref, id: d.job_id }) : null),
  owner_task_created: (d) => (d.title ? t("audit.event.todo_created", { title: quoted(d.title) }) : null),
  ceo_decided: (d) => (d.title ? t("audit.event.ceo_decided", { title: quoted(d.title) }) : null),
  idea_triaged: (d) => (d.title ? t("audit.event.idea_triaged", { title: quoted(d.title) }) : t("audit.event.idea_triaged_bare")),
  promotion_week: (d) => (d.week != null ? t("audit.event.promotion_week", { week: String(d.week) }) : null),
  people_retention_ran: (d) => (d.archived ? t("company.event.people_retention", { n: Number(d.archived) }) : null),
};

export function eventTone(e: Row): string {
  const d = eventData(e);
  if (e.verb === "status" || e.verb === "phase") return toneOf(d.to);
  if (e.verb === "paused" || e.verb === "stopped" || e.verb === "budget") return "warn";
  if (e.verb === "directive" || e.verb === "comment") return "info";
  // a decision waits on the owner: amber, blocking or not. Red is for failures only (DESIGN.md, Failed Red).
  if (e.entity === "request" && e.verb === "created") return "warn";
  return "muted";
}

/** The sentence came from the catalog, so the kind is already in it; a stored summary keeps its kind label. */
const saysItsKind = (e: Row, text: string) => text !== e.summary;

/** `mark`: the activity page's row, whose link reads as a link and whose meta line drops what the sentence says. */
/** The business a row belongs to, by name, for a feed that spans every business. Null when the list doesn't hold it. */
function BusinessName({ id }: { id: number }) {
  const { businesses } = useBusinessSelection();
  const b = businesses?.find((x) => x.id === id);
  return b ? <span className="feed-biz">{cleanName(b.name)}</span> : null;
}

/** `showBusiness`: the feed spans every business (the activity page's whole-company scope), so each row names its own. */
export function ActivityItem({ e, showProject = true, plain = false, mark = false, showBusiness = false }:
  { e: Row; showProject?: boolean; plain?: boolean; mark?: boolean; showBusiness?: boolean }) {
  const href = eventHref(e);
  const said = eventText(e);
  // titles and summaries are agent words, which may name a colleague by id ("owner: research")
  const text = prose(said);
  const d = eventData(e);
  // a row the system wrote about an agent's work names that agent first, then who recorded it
  const owner = mark && d.owner && d.owner !== e.actor ? String(d.owner) : null;
  const who = owner ? [agentName(owner), t("audit.feed.recorded_by", { who: agentName(e.actor) })] : [agentName(e.actor)];
  const kind = mark && saysItsKind(e, said) ? null : verbLabel(e.verb);
  return (
    <li className="feed-i">
      <span className={`dot ${eventTone(e)}`} aria-hidden="true" />
      <div className="feed-b">
        <div className="feed-t" title={text}>
          {href
            ? <ItemLink to={href} className={mark ? "feed-link" : undefined}>{text}{mark && <span className="feed-go" aria-hidden="true"> →</span>}</ItemLink>
            : text}
        </div>
        {plain ? <div className="small muted"><Time v={e.at} /></div> : (
          <div className="small muted">
            {showBusiness && e.business_id != null && <><BusinessName id={e.business_id} /> · </>}
            {[...who, kind].filter(Boolean).join(" · ")}
            {showProject && e.project_id && e.entity !== "project" && (
              <> · {mark ? tx("audit.feed.project", { name: <RefLink code={`P-${e.project_id}`} /> }) : <RefLink code={`P-${e.project_id}`} />}</>
            )}
            {showProject && !e.project_id && !e.business_id && <> · {t("common.whole_company")}</>} · <Time v={e.at} />
          </div>
        )}
      </div>
    </li>
  );
}

/** Consecutive rows by the same actor doing the same thing to the same kind of record fold under the first. */
const sameRun = (a: Row, b: Row) => a.actor === b.actor && a.verb === b.verb && a.entity === b.entity;
const FOLD_FROM = 3;

function dayKey(at: string): string {
  const d = new Date(at);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function dayName(at: string): string {
  const d = new Date(at);
  const now = new Date();
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((start(now) - start(d)) / 86_400_000);
  if (days === 0) return t("audit.feed.today");
  if (days === 1) return t("audit.feed.yesterday");
  return d.toLocaleDateString(locale(), { weekday: "short", month: "short", day: "numeric",
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) });
}

/** A run of alike rows: the first shows, the rest open with one button and keep their links. */
function FoldedRun({ rows, showProject, showBusiness = false, plain = false }:
  { rows: Row[]; showProject: boolean; showBusiness?: boolean; plain?: boolean }) {
  const [open, setOpen] = useState(false);
  const mark = !plain;
  return (
    <>
      <ActivityItem e={rows[0]} showProject={showProject} showBusiness={showBusiness} mark={mark} plain={plain} />
      {open ? rows.slice(1).map((e) => <ActivityItem key={e.id} e={e} showProject={showProject} showBusiness={showBusiness} mark={mark} plain={plain} />) : (
        <li className="feed-fold">
          <button type="button" className="link small" aria-expanded={false} onClick={() => setOpen(true)}>
            {t("audit.feed.fold", { n: rows.length - 1 })}
          </button>
        </li>
      )}
    </>
  );
}

/**
 * `owner`: the dashboard's feed. Only the changes an owner follows, each one sentence and its time. `grouped`: the
 * activity page's record, under day headings, alike rows folded, and a line where the owner's last visit falls
 * (`since`). `dayLevel`: the day headings sit under the page title (2) or under a section (3).
 */
export function ActivityList({ events: all, empty, showProject = true, compact, owner = false, limit, grouped = false, since = null, dayLevel = 3,
  showBusiness = false }:
  { events: Row[]; empty?: string; showProject?: boolean; compact?: boolean; owner?: boolean; limit?: number;
    grouped?: boolean; since?: string | null; dayLevel?: 2 | 3; showBusiness?: boolean }) {
  const kept = all.filter((e) => !isNoise(e) && (!owner || isOwnerEvent(e)));
  if (owner && !grouped) {
    // the dashboard's feed folds a run of alike rows as the activity record does, then shows `limit` of what is left
    const runs: Row[][] = [];
    for (const e of kept) {
      const last = runs[runs.length - 1];
      if (last && sameRun(last[0], e)) last.push(e);
      else runs.push([e]);
    }
    const shown = runs.flatMap((r) => (r.length >= FOLD_FROM ? [r] : r.map((e) => [e]))).slice(0, limit);
    if (!shown.length) return <Empty>{empty ?? t("common.activity.empty")}</Empty>;
    return (
      <ol className={`feed ${compact ? "compact" : ""}`}>
        {shown.map((r) => (r.length > 1 ? <FoldedRun key={`run-${r[0].id}`} rows={r} showProject={showProject} showBusiness={showBusiness} plain />
          : <ActivityItem key={r[0].id} e={r[0]} showProject={showProject} showBusiness={showBusiness} plain />))}
      </ol>
    );
  }
  const events = kept.slice(0, limit);
  if (!events.length) return <Empty>{empty ?? t("common.activity.empty")}</Empty>;
  if (!grouped) {
    return <ol className={`feed ${compact ? "compact" : ""}`}>{events.map((e) => <ActivityItem key={e.id} e={e} showProject={showProject} showBusiness={showBusiness} plain={owner} />)}</ol>;
  }
  const cut = since ? new Date(since).getTime() : null;
  const older = (e: Row) => cut !== null && new Date(e.at).getTime() <= cut;
  const out: ReactNode[] = [];
  let day = "";
  // no line when nothing is new, or everything on screen is
  let crossed = cut === null || older(events[0]) || !events.some(older);
  for (let i = 0; i < events.length;) {
    const e = events[i];
    if (!crossed && older(e)) {
      crossed = true;
      out.push(<li key={`since-${e.id}`} className="feed-since" role="separator">{t("audit.feed.since_line")}</li>);
    }
    if (dayKey(e.at) !== day) {
      day = dayKey(e.at);
      const H = dayLevel === 2 ? "h2" : "h3";
      out.push(<li key={`day-${e.id}`} className="feed-day"><H>{dayName(e.at)}</H></li>);
    }
    let j = i + 1;
    while (j < events.length && sameRun(events[j], e) && dayKey(events[j].at) === day && (crossed || !older(events[j]))) j++;
    const run = events.slice(i, j);
    out.push(run.length >= FOLD_FROM ? <FoldedRun key={`run-${e.id}`} rows={run} showProject={showProject} showBusiness={showBusiness} />
      : run.map((x) => <ActivityItem key={x.id} e={x} showProject={showProject} showBusiness={showBusiness} mark />));
    i = j;
  }
  return <ol className="feed feed-grouped">{out}</ol>;
}
