import type { Elsewhere } from "./api";
import { shortDate } from "./components/ui";
import { locale, t } from "./i18n";

export type Tier = "blocking" | "overdue" | "today" | "other";

export interface TodayItem {
  kind: string;
  label: string;
  ref: string;
  title: string;
  /** `estimate_min` of a to-do with steps is the minutes of the steps not ticked yet; `steps` counts them. */
  meta: { estimate_min: number; due_at: string | null; reason: string; steps?: { done: number; total: number } };
  tier: Tier;
  action: "decide" | "open";
  link: string;
  /** The day the item falls on, 0 being today, so no day asks for more than the owner's time (`Today.daily_min`). */
  day: number;
}

export interface Today {
  business_id: number;
  items: TodayItem[];
  total: number;
  total_min: number;
  /** The minutes a day the owner gave at kickoff; null when the plan names none, and then every item is today's. */
  daily_min: number | null;
  today_min: number;
  today_count: number;
  yesterday: string[];
  number: {
    repeat: number | null;
    first_value: number | null;
    /** The brief's headline goal in its own words; `measure` and `current` are null when the people list cannot count
     * it, and `number` when the plan's goal line states none the company can read. */
    goal: { text: string; number: number | null; unit: string; measure: string | null; current: number | null };
    goal_from_brief: boolean;
    stars: number | null;
    link: string;
  };
  banner: { kind: string; text: string; link: string } | null;
  /** The owner's stop of the whole company (services/stops.py), or null while it runs. */
  company_stop: { at: string; by: string; reason: string | null } | null;
  /** Other businesses with decisions, to-dos or failures waiting, by id; absent from servers before it (treat as none). */
  elsewhere?: ElsewhereRow[];
}

/** One other business with something waiting: `blocking` counts its decisions that hold up work. `turns` (QA-REG-005,
 * additive): the owner's turns there that are neither a decision nor an owner task (a kickoff answer, a plan to approve,
 * a cycle to start, a stage at its gate). `failed` (additive): its work that stopped or failed and waits on the owner.
 * An older server sends neither, read as 0. */
export type ElsewhereRow = Elsewhere & { blocking?: number; turns?: number; failed?: number };

/** What waits in another business, counted the way the business's own screens count it: every item of its list that is
 * not a decision is a to-do (`todayCounts().mine`), the kickoff answer and the plan to approve included. */
export const elsewhereCounts = (b: ElsewhereRow) => ({ decisions: b.decisions, todos: b.todos + (b.turns ?? 0), failed: b.failed ?? 0 });

/** The tables whose changes can move something on the owner's list. The cycle's three are there because "Read the cycle
 * and start a stage" goes away the moment a stage starts: without them the list kept it until something else changed. */
export const TODAY_TABLES = [
  "agent_requests", "owner_tasks", "owner_ideas", "kickoff_sessions", "bets", "businesses", "people", "jobs", "activity_events",
  "cycles", "cycle_stages", "cycle_proposals",
];

export const actionLabel = (action: TodayItem["action"]) => t(action === "decide" ? "home.list.decide" : "home.list.open");

/** Every count of the owner's work on screen (sidebar, home, decisions, to-dos), read off the one list so none disagrees. */
export function todayCounts(today: Today | null): { decisions: number; blocking: number; mine: number } {
  const items = today?.items ?? [];
  const decisions = items.filter((it) => it.kind === "decision");
  return { decisions: decisions.length, blocking: decisions.filter((it) => it.tier === "blocking").length,
    mine: items.length - decisions.length };
}

const DAY = 86_400_000;

function dayStart(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** "3 days overdue", "due today", "due Nov 5": the due date in the owner's own days. A decision does not fall due, it
 * expires (it can no longer be approved), so `expires` words it that way. */
export function dueText(due: string | null, now = new Date(), expires = false): string | null {
  if (!due) return null;
  const d = new Date(due);
  const days = Math.round((dayStart(d) - dayStart(now)) / DAY);
  if (expires) {
    if (days < 0) return t("home.list.expired");
    if (days === 0) return t("home.list.expires_today");
    if (days === 1) return t("home.list.expires_tomorrow");
    return t("home.list.expires_on", { date: shortDate(d) });
  }
  if (days < 0) return t("home.list.overdue", { n: -days });
  if (days === 0) return t("home.list.due_today");
  if (days === 1) return t("home.list.due_tomorrow");
  return t("home.list.due_on", { date: shortDate(d) });
}

/** The gray line under a title: how long it takes, when it is due, why it is the owner's. */
export function metaText(item: TodayItem, now = new Date()): string {
  const parts = [t("home.list.about_min", { n: item.meta.estimate_min }), stepsText(item), dayText(item.day),
    dueText(item.meta.due_at, now, item.kind === "decision"), item.meta.reason];
  return parts.filter(Boolean).join(" · ");
}

/** "1 of 2 steps done", once the owner has ticked a step of a to-do that has steps. */
export function stepsText(item: TodayItem): string | null {
  const s = item.meta.steps;
  return s && s.done > 0 ? t("home.list.steps", { done: s.done, total: s.total }) : null;
}

/** "tomorrow", "in 3 days": the day the owner's list puts an item on to keep each day within the owner's time. */
export function dayText(day: number): string | null {
  return day === 1 ? t("home.list.tomorrow") : day > 1 ? t("home.list.later", { n: day }) : null;
}

export function minutesText(min: number): string {
  if (min < 60) return t("home.list.min", { n: min });
  const h = Math.floor(min / 60);
  return min % 60 ? t("home.list.hours_min", { h, m: min % 60 }) : t("home.list.hours", { h });
}

export function panelTarget(item: TodayItem): { key: "req" | "task"; id: number } | null {
  const m = /^(REQ|OT)-(\d+)$/.exec(item.ref);
  return m ? { key: m[1] === "REQ" ? "req" : "task", id: Number(m[2]) } : null;
}

/** A cycle's proposal or a stage's end waits on the dashboard's cycle cards: its row goes to them, on the dashboard of
 * the item's business, which scrolls to the cards (`#cycle-waiting`). Null for every other item. */
export function cycleTarget(item: TodayItem): string | null {
  if (!/^(CYCLE|STAGE)-\d+$/.test(item.ref)) return null;
  return `${item.link.split("#")[0]}#cycle-waiting`;
}
