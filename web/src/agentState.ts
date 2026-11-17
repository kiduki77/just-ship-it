/**
 * What an agent is doing, decided in one place from its work: the agents list, an agent's page and the sidebar all
 * read `agentStatus`, so no two screens call the same agent two different things. The server's `state` reads the
 * latest model call; here a failed call alone does not make an agent "Failed" (its tasks decide that), and nothing is
 * "Working" while no worker runs.
 */
import type { Row } from "./api";
import type { Tone } from "./components/kit";
import type { PlainKey } from "./i18n";

export type AgentKind = "working" | "waiting" | "failed" | "stopped" | "idle";

export interface AgentStatus {
  kind: AgentKind;
  /** Work the agent holds now (tasks, reviews, builds), whether or not a worker runs it. */
  work: Row[];
  /** Its tasks in FAILED, which are what make an agent "Failed". */
  failed: Row[];
  /** The latest model call failed, which on its own does not touch any task. */
  lastCallFailed: boolean;
  stoppedSince: string | null;
}

/** The worker as `useWorker()` gives it: `down` (no worker) or `stopped` (the owner stopped the company). */
export type WorkerView = { down: boolean; stopped: boolean };

export const AGENT_KIND: Record<AgentKind, { label: PlainKey; tone: Tone; rank: number }> = {
  working: { label: "company.agents.state.running", tone: "run", rank: 3 },
  waiting: { label: "company-people.state.waiting", tone: "warn", rank: 3 },
  failed: { label: "company.agents.state.error", tone: "bad", rank: 2 },
  stopped: { label: "company.agents.state.stopped", tone: "warn", rank: 2 },
  idle: { label: "company.agents.state.idle", tone: "muted", rank: 0 },
};

export const ACTIVE_TASK = ["RUNNING", "REVIEW", "REWORK"];

export function agentStatus(a: Row, failed: Row[], worker: WorkerView): AgentStatus {
  const work: Row[] = a.running ?? [];
  const stoppedSince: string | null = a.stopped ? a.stopped.at ?? null : null;
  const lastCallFailed = a.last_status != null && a.last_status !== "ok";
  const kind: AgentKind = stoppedSince !== null || a.state === "stopped" ? "stopped"
    : work.length ? (worker.down || worker.stopped ? "waiting" : "working")
      : failed.length ? "failed" : "idle";
  return { kind, work, failed, lastCallFailed, stoppedSince };
}

/** FAILED tasks by owner, from `/tasks?status=FAILED`. */
export function failedByOwner(tasks: Row[] | null | undefined): Map<string, Row[]> {
  const out = new Map<string, Row[]>();
  for (const t of tasks ?? []) if (t.owner) out.set(t.owner, [...(out.get(t.owner) ?? []), t]);
  return out;
}

/** Working (or holding work) first, then failed and stopped, then the most recently active. The sidebar should use it too. */
export const compareAgents = (a: Row, sa: AgentStatus, b: Row, sb: AgentStatus) =>
  AGENT_KIND[sb.kind].rank - AGENT_KIND[sa.kind].rank || String(b.last_run_at ?? "").localeCompare(String(a.last_run_at ?? ""));

/** Never called and owns no task in this view: folded into one line on the list. */
export const neverWorked = (a: Row, s: AgentStatus) =>
  s.kind === "idle" && !a.calls_total && !a.tasks_total && !a.last_run_at;
