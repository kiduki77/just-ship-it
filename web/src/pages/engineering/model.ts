import { Row } from "../../api";
import { PlainKey } from "../../i18n";

/** The engineering stages a build passes through, in order (the engineer tab lists them all). */
export const PIPE = ["QUEUED", "INTAKE", "PLAN_REVIEW", "IMPLEMENTING", "QA", "REVIEWING", "SHIPPING", "DELIVERED", "MERGED"];

export type Group = "needs" | "queued" | "running" | "delivered" | "merged" | "failed";

const GROUP_OF: Record<string, Group> = {
  CLARIFICATION_REQUIRED: "needs",
  QUEUED: "queued", INTAKE: "queued",
  PLAN_REVIEW: "running", IMPLEMENTING: "running", REVIEWING: "running", QA: "running", SHIPPING: "running",
  DELIVERED: "delivered", MERGED: "merged", FAILED: "failed", DISCARDED: "failed",
};
/** A build's state group; a status this page does not know reads as waiting. */
export const groupOf = (status: string): Group => GROUP_OF[status] ?? "queued";

/** The owner's view of a build's way: five steps instead of the nine engineering stages, which stay on the engineer tab. */
export type Phase = "start" | "build" | "check" | "decide" | "merged";
export const PHASES: Phase[] = ["start", "build", "check", "decide", "merged"];
export function phaseOf(status: string, ownerTurn: boolean): Phase {
  if (status === "MERGED") return "merged";
  if (status === "CLARIFICATION_REQUIRED" || (status === "DELIVERED" && ownerTurn)) return "decide";
  if (status === "DELIVERED") return "check";
  if (["QUEUED", "INTAKE"].includes(status)) return "start";
  // the tests and the separate code review are the company checking its own build
  if (["QA", "REVIEWING", "SHIPPING"].includes(status)) return "check";
  return "build";
}

/** Where a build stands among the five steps; a stopped build stands at the last step it reached. */
export function phaseState(job: Row, turn: boolean) {
  const stopped = groupOf(job.status) === "failed";
  const reached = stopped
    ? [...(job.steps ?? [])].reverse().map((s: Row) => s.status).find((s: string) => PIPE.includes(s) || s === "CLARIFICATION_REQUIRED") ?? "QUEUED"
    : job.status;
  return { stopped, at: PHASES.indexOf(phaseOf(reached, turn)) };
}

/** One development as the server explains it (engineering/status.py). */
export type Stage = {
  key: string; tone: "bad" | "warn" | "run" | "wait" | "ok"; ref: string; title: string; headline: string;
  detail: string | null; since: string | null; project_id: number | null; link: { label: string; to: string } | null;
  dev_job_id: number | null; actions: string[]; retry_dev: boolean; retry_job: number | null;
  /** Until when the server refuses a rebuild, as the owner reads it: the old worker may still be finishing. */
  retry_wait: string | null;
  /** The decision a delivered build waits on in Decisions. */
  waits: "merge" | "fix" | null;
};
export const DOT: Record<Stage["tone"], string> = { bad: "bad", warn: "warn", run: "run pulse", wait: "", ok: "ok" };
/** A stage the owner must answer (warn) or unstick (bad). */
export const ownerTurn = (st?: Stage) => !!st && (st.tone === "warn" || st.tone === "bad");
export const hasWay = (st: Stage) => !!st.link || st.actions.length > 0 || !!st.retry_job || st.retry_dev;

/** A merged build nobody can open yet (`dev_use` of the project, usable.state): the step after the merge. */
export type AfterMerge = { p: Row; u: Row };
export const USE_WAITS = ["decide", "todo", "later", "closed"];
export const USE_LINE: Record<string, PlainKey> = {
  decide: "work.line.merged_decide", todo: "work.line.merged_todo", later: "work.line.merged_later", closed: "work.line.merged_closed",
};

/** One item of a build's plan (worker.work_done): its number, its name and what works once it is done as plain text,
 * and whether its commit is made. A plan written before items had `detail`, or in another shape, gives none. */
export type BuildItem = { n: number; title: string; detail?: string; done: boolean };
/** A build's items; `current` is the lowest one not done, null once all are. */
export type Items = { tasks: BuildItem[]; current: number | null };
/** The items of a build whose plan had a list; a build from before them, or before its plan, has none. */
export const itemsOf = (j: Row): Items | null => {
  const p = j.result?.progress;
  return Array.isArray(p?.tasks) && p.tasks.length > 0 ? { tasks: p.tasks, current: p.current ?? null } : null;
};
/** How many planned items or tasks a build has done, for the list of builds; none while it only counts its commits. */
export const countOf = (j: Row): { done: number; total: number } | null => {
  const p = j.result?.progress;
  return typeof p?.total === "number" && p.total > 0 ? { done: p.done ?? 0, total: p.total } : null;
};

/** When the job last moved: its newest step, else when it was made. */
export const lastMoved = (j: Row): string | undefined => (j.steps?.length ? j.steps[j.steps.length - 1].at : j.updated_at ?? j.created_at);

/** Builds as the master list shows them: what needs the owner, what is moving, and what is over. */
export function sectionsOf(jobs: Row[], needsYou: (j: Row) => boolean) {
  const turn = jobs.filter(needsYou);
  const rest = jobs.filter((j) => !needsYou(j));
  const over = (j: Row) => ["merged", "failed"].includes(groupOf(j.status));
  return { turn, active: rest.filter((j) => !over(j)), finished: rest.filter(over) };
}

/** The build that opens when none is picked: the one that needs the owner, else one that is running, else the newest. */
export function defaultJob(jobs: Row[], needsYou: (j: Row) => boolean): Row | undefined {
  const newest = [...jobs].sort((a, b) => String(lastMoved(b) ?? "").localeCompare(String(lastMoved(a) ?? "")))[0];
  return jobs.find(needsYou) ?? jobs.find((j) => groupOf(j.status) === "running") ?? newest;
}
