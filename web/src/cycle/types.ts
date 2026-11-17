// The dashboard's cycle board: the shape of GET /businesses/{id}/cycle-board (backend/justshipit/contracts/cycle.py,
// CycleBoardOut) and of the decisions POST /cycles/{id}/decisions takes (services/cycle.py, Decision).

export type BoardState =
  | "none" | "proposing" | "proposal" | "legacy" | "revising" | "proposal_failed"
  | "running" | "closing" | "gate" | "stage_stopped" | "retro" | "stopped";
export type StageStatus = "planned" | "running" | "closing" | "awaiting_owner" | "done" | "skipped" | "stopped";
export type CycleState = "draft" | "running" | "retro" | "closed" | "stopped";
export type EndsWhen = "run_settled" | "window_closed" | "owner_marks";
export type Confidence = "high" | "mid" | "low";

export interface CycleHead {
  id: number; number: number; state: CycleState; mode: "new" | "adopt";
  starts_on: string; ends_on: string; started_at: string | null;
}

/** Always a guess; the console says so next to the numbers. */
export interface StageEstimate { minutes: number | null; usd: number | null; basis: "guess" | "history" }

export interface ExistingRef { kind: "project" | "bet" | "schedule"; id: number }

export interface StageCard {
  key: string; title: string; doing: string; owner_does: string; decision_points: string[];
  estimate: StageEstimate; ends_when: EndsWhen;
  recurring: string[]; owner_work: string[]; takes_over: ExistingRef[];
  change: "added" | "edited" | "moved" | null;
}

export interface ChangeView { stage_key: string; kind: "added" | "edited" | "moved" | "removed"; fields: string[]; why: string | null }

export interface ProposalCard {
  kind: "proposal"; cycle_id: number; version: number; mode: "new" | "adopt";
  intro: string; reply: string | null; stages: StageCard[]; changes: ChangeView[];
  revising: boolean; revision_failed: string | null; since: string | null;
  actions: ("start_cycle" | "revise" | "retry_proposal")[];
}

export interface ClaimView { claim: string; evidence_ids: string[]; confidence: Confidence; capped: boolean }

export interface SummaryView {
  headline: string; done: string[]; not_done: string[]; claims: ClaimView[]; needs_owner: string[];
  next_key: string | null; next_reason: string | null; change_suggestions: string[];
  cost_usd: number; elapsed_s: number; tasks: { done: number; failed: number; total: number };
  by: "project_manager" | "server"; note: string | null;
}

export interface WaitingItem { kind: "decision" | "job"; id: number; title: string }

export interface GateCard {
  kind: "gate"; cycle_id: number; stage_id: number; stage_title: string; round: number;
  summary: SummaryView | null; next_stage: StageCard | null; proposal_version: number;
  waiting: WaitingItem[]; reply: string | null;
  actions: ("start_next" | "finish_cycle" | "revise" | "stop_here" | "retry_stage")[];
}

export interface StoppedCard {
  kind: "stopped_stage"; cycle_id: number; stage_id: number; stage_title: string; waiting: WaitingItem[];
  actions: ("resume_stage" | "stop_here")[];
}

export interface DecisionCard { kind: "decision"; request_id: number; title: string; project_id: number | null; blocking: boolean }

export type WaitingCard = ProposalCard | GateCard | StoppedCard | DecisionCard;

export interface StageChip {
  id: number; key: string; position: number; title: string; status: StageStatus;
  tasks_done: number | null; tasks_total: number | null; is_current: boolean; adopted: boolean;
}

export interface TaskRow { id: number; ref: string; title: string; agent: string; status: string; started_at: string | null; project_id: number }

export interface CurrentStage {
  stage: StageChip; doing: string; owner_does: string; estimate: StageEstimate;
  recurring: string[]; owner_work: string[];
  tasks: TaskRow[]; tasks_done: number; tasks_total: number; cost_usd: number; started_at: string | null;
  close_requested: boolean; summary: SummaryView | null;
  actions: ("stop_stage" | "resume_stage" | "close_stage_now")[];
  starting: "planning" | "queued" | null; starting_since: string | null;
}

export interface ActionItem {
  kind: "task" | "review" | "dev" | "job"; cycle_number: number | null; stage_id: number | null; stage_title: string | null;
  task_id: number | null; dev_job_id: number | null; job_id: number | null;
  title: string; project_id: number | null; started_at: string | null; spent_usd: number;
}

/** One per working agent; its `items` are what it is on, the one started last first. */
/** `stopping`: the owner stopped the company or this agent (`stopped`), and these are the steps it finishes first. */
export interface ActionCard { agent: string; items: ActionItem[]; stopping: boolean; stopped: boolean; }

export interface CycleRow {
  id: number; number: number; state: CycleState; started_at: string | null; closed_at: string | null;
  stages_done: number; stages_total: number; cost_usd: number;
}

export interface CycleBoard {
  state: BoardState; sentence: string; since: string | null; cycle: CycleHead | null;
  waiting: WaitingCard[]; strip: StageChip[]; current: CurrentStage | null;
  working: ActionCard[]; idle_agents: number; idle_stopped?: number; history: CycleRow[];
}

export type Decision =
  | { kind: "start_cycle"; version: number }
  | { kind: "revise"; note: string; base_version: number }
  | { kind: "retry_proposal"; version: number }
  | { kind: "start_next"; after_stage_id: number; version: number }
  | { kind: "finish_cycle" | "stop_here"; after_stage_id: number }
  | { kind: "retry_stage" | "stop_stage" | "resume_stage" | "close_stage_now"; stage_id: number };

/** GET /cycles/{id}: one cycle in full, for a stage picked in the strip and for the Business page. */
export interface CycleDetail {
  cycle: CycleHead; retro: Record<string, any> | null; stop_reason: string | null;
  proposals: { version: number; status: string; owner_note: string | null; intro: string | null; reply: string | null;
    stages: StageCard[]; changes: ChangeView[]; error: string | null; created_at: string | null }[];
  stages: (StageChip & { end_cause: string | null; started_at: string | null; finished_at: string | null;
    summary: SummaryView | null })[];
}

/** The tables whose changes refresh the board (useBusinessLive); `workers` is the live feed's word for a worker
 * starting or going silent, which the board's sentence says. */
export const BOARD_TABLES = ["cycles", "cycle_stages", "cycle_proposals", "projects", "tasks", "dev_jobs", "agent_requests", "jobs", "workers"];
