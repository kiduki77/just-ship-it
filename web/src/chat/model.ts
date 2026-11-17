import { useCallback, useEffect, useRef, useState } from "react";
import { ApiFailure, failureOf, post, useLiveEvents } from "../api";
import { locale, lookup, PlainKey, t } from "../i18n";
import { Tone } from "../components/kit";
import { worded } from "../components/pathContext";
import { agentName } from "../labels";
import { PageRef, Subject, subjectKey } from "./subject";

/** The tables a conversation's views refetch on: its lines, its threads, and the builds and jobs a task card reads. */
export const CHAT_TABLES = ["agent_messages", "chat_threads", "jobs", "dev_jobs"];

export interface Agent { id: string; department: string | null }
/** `filed`: the step that filed a card, as a `path.step.*` catalog key and its values, or `known: "unknown"` for none. */
export interface Fact { k: string; ref?: string; v?: string; n?: number; key?: string; vars?: Record<string, string | number>; known?: string }

export interface ThreadRow {
  id: number;
  subject: { kind: string; ref: string };
  business_id: number | null;
  facts: Fact[];
  default_agent: Agent;
  last_seq: number;
  owner_read_seq: number;
  unread: boolean;
  unread_count: number;
  archived: boolean;
  last_message_at: string | null;
  last: { from: Agent; text: string } | null;
  open_tasks: string[];
  /** The conversation holds an answer a recalled role rebuilt from records. */
  recall: boolean;
}

/** What `POST /chat/subject` says about a screen's subject. It makes nothing: `thread_id` is null before the first line. */
export interface Resolved {
  subject: { kind: string; ref: string };
  business_id: number | null;
  facts: Fact[];
  default_agent: Agent;
  work: string[];
  thread_id: number | null;
}

export type TaskState = "received" | "budget" | "running" | "checking" | "done" | "needs_fixing" | "stopped" | "cancelled";

export interface TaskInfo {
  title: string;
  branch?: string | null;
  state: { state: TaskState; who: string[]; progress?: { done: number; total: number }; request_id?: number };
}

export interface Line {
  id: number;
  seq: number;
  at: string;
  kind: "text" | "comment" | "task" | "status" | "handoff" | "escalation" | "decision";
  from: Agent;
  to: Agent;
  body: string;
  text: string | null;
  scrubbed: { kind: string; n: number }[];
  in_reply_to: number | null;
  task?: TaskInfo;
  decision?: { request_id: number | null };
  /** An answer a recalled role rebuilt from the job's records: the ids it cites, and whether the records ran out. */
  recall?: { rebuilt: boolean; cites: string[]; no_record: boolean; in_progress: boolean };
}

export const TASK_WORD: Record<TaskState, PlainKey> = {
  received: "chat.task.received", budget: "chat.task.budget", running: "chat.task.running", checking: "chat.task.checking",
  done: "chat.task.done", needs_fixing: "chat.task.needs_fixing", stopped: "chat.task.stopped", cancelled: "chat.task.cancelled",
};
export const TASK_TONE: Record<TaskState, Tone> = {
  received: "muted", budget: "warn", running: "run", checking: "run", done: "ok", needs_fixing: "bad", stopped: "warn", cancelled: "muted",
};
/** A state code the server sent, when this console knows it. */
export const taskState = (code: string): TaskState | null => (code in TASK_WORD ? (code as TaskState) : null);

export interface Replying { agent: string; status: string; wait_reason: string | null; run_after: string | null; waits_on: string | null }
export interface Page { thread: ThreadRow; messages: Line[]; more: boolean; replying: Replying[] }
export interface Sent { thread_id: number; message_id: number; seq: number; scrubbed: { kind: string; n: number }[] }
export interface Who { id: string; department: string | null; name: string }

// The words for what a screen is called, from the sidebar's own catalog, so a screen goes by one name.
const PAGE_NAME: Record<PageRef, PlainKey> = {
  home: "shell.nav.dashboard", my_tasks: "shell.nav.todos", business: "shell.nav.business", new_business: "shell.crumb.new_business",
  kickoff: "shell.crumb.kickoff", inbox: "shell.nav.decisions", tasks: "shell.nav.tasks", projects: "shell.nav.projects",
  agents: "shell.nav.agents", audit: "shell.nav.activity", crews: "shell.nav.crews", engineering: "shell.nav.engineering",
  skills: "shell.nav.skills", routines: "shell.nav.routines", settings: "shell.nav.settings",
};
const pageName = (ref: string): string => (ref in PAGE_NAME ? t(PAGE_NAME[ref as PageRef]) : ref);

export interface ChipData { key: string; text: string; title?: string }

/** The facts of a subject as the chips the dock shows, worded from the catalog. A fact this console does not know is left out. */
export function chipsOf(facts: Fact[]): ChipData[] {
  const out: ChipData[] = [];
  for (const f of facts) {
    const ref = f.ref ?? "";
    const v = f.v ?? "";
    const key = f.k;
    switch (f.k) {
      case "general": out.push({ key, text: t("chat.general") }); break;
      case "merge": out.push({ key, text: t("chat.subject.merge", { ref }) }); break;
      case "build": out.push({ key, text: t("chat.subject.build", { ref }) }); break;
      case "product": out.push({ key, text: t("chat.subject.product", { name: v }), title: v }); break;
      case "branch": out.push({ key, text: t("chat.subject.branch", { name: v }), title: v }); break;
      case "fix": out.push({ key, text: t("chat.subject.fix", { n: f.n ?? 0 }) }); break;
      case "request": out.push({ key, text: t("chat.subject.request", { ref }), title: v }); break;
      case "owner_task": out.push({ key, text: t("chat.subject.owner_task", { ref }), title: v }); break;
      case "plan": out.push({ key, text: t("chat.subject.plan", { name: v }), title: v }); break;
      case "spec": out.push({ key, text: t("chat.subject.spec", { ref }), title: v }); break;
      case "project": out.push({ key, text: t("chat.subject.project", { name: v }), title: v }); break;
      case "task": out.push({ key, text: t("chat.subject.task", { ref }), title: v }); break;
      case "business": out.push({ key, text: t("chat.subject.business", { name: v }), title: v }); break;
      case "page": out.push({ key, text: t("chat.subject.page", { name: pageName(v) }) }); break;
      case "filed": out.push({ key, text: f.known === "unknown" || !f.key ? t("chat.subject.filed_unknown") : worded(f.key, f.vars) }); break;
    }
  }
  return out;
}

export const dept = (id: string | null): string => (id ? lookup(`company.dept.${id}`) ?? id : "");

/** Name and role, the way the Agents page says it: "Engineer · Engineering". The owner has no role. */
export const roleLine = (a: Agent): string => [agentName(a.id), dept(a.department)].filter(Boolean).join(" · ");

/** "Engineer, Code reviewer and Product" in the owner's language. */
export const names = (ids: string[]): string => new Intl.ListFormat(locale(), { style: "long", type: "conjunction" }).format(ids.map(agentName));

export const removed = (s: { n: number }[]): number => s.reduce((sum, x) => sum + x.n, 0);

export const newAttempt = (): string => globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;

const DRAFT = "ship.chat.draft.";

/** What the owner typed and has not sent, per conversation, for this browser tab. Storage may be off: a draft then lasts until the dock closes. */
export function readDraft(key: string): string {
  try {
    return sessionStorage.getItem(DRAFT + key) ?? "";
  } catch {
    return "";
  }
}

export function writeDraft(key: string, text: string): void {
  try {
    if (text) sessionStorage.setItem(DRAFT + key, text);
    else sessionStorage.removeItem(DRAFT + key);
  } catch {
    /* private mode: nothing to keep */
  }
}

export const subjectBody = (s: Subject, businessId: number | null) =>
  s.kind === "general" ? { kind: "general", ref: null, business_id: null } : { kind: s.kind, ref: s.ref, business_id: businessId };

/**
 * What the server says about a subject, again whenever a line, a thread or a build changes. Only the answer for the
 * subject now asked for counts, so a slow answer for the screen the owner just left never shows.
 */
export function useResolved(subject: Subject, businessId: number | null, enabled: boolean) {
  const key = `${subjectKey(subject)}@${businessId ?? ""}`;
  const [state, setState] = useState<{ key: string; data: Resolved | null; error: string | null; failure: ApiFailure | null }>(
    { key: "", data: null, error: null, failure: null });
  const wanted = useRef(key);
  wanted.current = key;
  const timer = useRef<number | undefined>(undefined);
  const load = useCallback(() => {
    if (!enabled) return;
    post<Resolved>("/chat/subject", subjectBody(subject, businessId))
      .then((data) => wanted.current === key && setState({ key, data, error: null, failure: null }))
      .catch((e) => wanted.current === key && setState({ key, data: null, error: String(e.message ?? e), failure: failureOf(e) ?? "server" }));
  }, [key, enabled]);
  useEffect(load, [load]);
  useLiveEvents((e) => {
    if (!enabled || (e.table !== "*" && !CHAT_TABLES.includes(e.table) && e.table !== "agent_requests")) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, e.now ? 0 : 600);
  });
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return state.key === key ? { data: state.data, error: state.error, failure: state.failure, reload: load }
    : { data: null, error: null, failure: null, reload: load };
}

/** Whether a media query matches now and as the window changes. */
export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const f = () => setOn(m.matches);
    f();
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, [query]);
  return on;
}
