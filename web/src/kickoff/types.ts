import type { Cite, Doc } from "../components/attach";
export type Provenance = "said" | "confirmed" | "guess" | "unknown" | "blank";
export type TurnKind = "question" | "playback" | "proposal";
export type SessionStatus = "OPEN" | "WAITING_OWNER" | "PAUSED" | "DONE" | "ABANDONED";

export interface BriefLine {
  id: string;
  text: string;
  provenance: Provenance;
  provenance_ko?: string;
  source: string | null;
  /** The owner's own words this line rests on, kept by the server when the line is 오너가 말함 or 확인함. */
  quote?: string | null;
  /** The file and page the quote is from, when it is from an attached file. */
  doc?: Cite;
}

export interface BriefSection {
  key: string;
  title: string;
  required: boolean;
  /** A section of an older brief that the registry no longer has; shown for reference only. */
  legacy: boolean;
  quiet: boolean;
  gate: boolean;
  drives: string;
  state: "settled" | "guess" | "unknown" | "empty";
  lines: BriefLine[];
}

export interface PromoteLine {
  id: string;
  section: string;
  title: string;
  text: string;
}

/** Why the server stored a line the CEO marked 오너가 말함 as something else. Older notes have none. */
export type DowngradeReason = "no_quote" | "not_backing" | "guess_reply" | "chip_elsewhere" | "chip";

/** A line the server stored under another provenance, or ignored, while posting a turn. */
export interface PatchNote {
  what: "downgraded" | "skipped" | "kept" | string;
  section?: string;
  text?: string;
  /** The provenance the server stored the line as. Older notes have none and were all stored as a guess or 확인함. */
  to?: Provenance;
  reason?: DowngradeReason;
  why?: string;
}

export interface Turn {
  id: number;
  n: number;
  kind: TurnKind;
  ceo_text: string;
  chips: string[];
  section: string | null;
  closing: boolean;
  /** The guess lines a "맞아요" to this turn turns into 확인함. Empty on a question. */
  promote: PromoteLine[];
  patch_notes: PatchNote[];
  owner_text: string | null;
  owner_choice: string | null;
  created_at: string;
  answered_at: string | null;
  /** "strengths" on the server's opening question (turn 0): what the owner is good at. Its answer is in
   * `owner_choice` as the picked codes joined by commas, "none" for none of these yet, or "unknown" for a skip. */
  asks?: "strengths";
}

export interface Brief {
  id: number;
  version: number;
  approved_at: string | null;
  sections: BriefSection[];
  assumptions: unknown[];
}

export interface Session {
  id: number;
  business_id: number;
  status: SessionStatus;
  turns_used: number;
  max_turns: number;
  turns_left: number;
  note: string | null;
  /** The plan text the owner pasted when opening the business; null when they pasted nothing. */
  material?: string | null;
  can_continue: boolean;
  created_at: string;
  ended_at: string | null;
  end_reason: string | null;
}

export interface Coverage {
  required_total: number;
  required_settled: number;
  remaining: string[];
  percent: number;
  empty_optional: string[];
}

export interface EmptyDefault {
  key: string;
  title: string;
  state: BriefSection["state"];
  default: string;
}

export interface Approval {
  can_approve: boolean;
  blockers: string[];
  blocked_by: string[];
  empty_defaults: EmptyDefault[];
}

/** How far a feature gets: works end to end, started, or promised by the README or a screen but not built. */
export type FeatureState = "works" | "partial" | "missing";
export type TestsState = "strong" | "some" | "few" | "none";
export type ClaimVerdict = "holds" | "partly" | "not_found";

export interface ProductFeature {
  name: string;
  state: FeatureState;
  /** A file path, with the function or line when the CPO named one. */
  evidence: string;
}

export interface ProductFinding {
  text: string;
  evidence: string;
}

export interface ProductTests {
  state: TestsState;
  summary: string;
  evidence: string;
}

export interface ProductClaim {
  claim: string;
  verdict: ClaimVerdict;
  reality: string;
  evidence: string;
}

/** The CPO's reading of an imported product's code (`CurrentStateOut`): at most 12 features, 6 risks, 6 unfinished, 8 claims. */
export interface ProductReport {
  summary: string;
  for_owner: string;
  what_it_does: string;
  features: ProductFeature[];
  stack: string[];
  tests: ProductTests;
  risks: ProductFinding[];
  unfinished: ProductFinding[];
  readme_vs_reality: ProductClaim[];
  files_read: string[];
}

interface ProductBase {
  /** The folder name under products/. */
  repo: string;
  /** `products/<repo>`, the company's own copy. */
  folder: string;
  /** Where the owner's copy is: the path as they gave it. */
  source: string;
  commit: string;
  base_branch: string;
  /** Why a reading in progress cannot move right now, in the owner's words; null when nothing holds it up. */
  waiting?: string | null;
}

/** The business's imported product. The report is there exactly when `report_state` is "ready". */
export type ProductView = ProductBase &
  (
    | { report_state: "reading"; report: null }
    | { report_state: "ready"; report: ProductReport }
    | { report_state: "failed"; report: null }
  );

export interface KickoffView {
  session: Session;
  turns: Turn[];
  /** The files the owner attached in this chat; `after_turn` places each one. */
  documents?: Doc[];
  open_turn: number | null;
  brief: Brief | null;
  coverage: Coverage;
  approval: Approval;
  /** The CEO's last question once the chat is done, until the owner answers or skips it; the server's text, not a turn. */
  customer_question?: { text: string } | null;
  /** Null for a business that started from an idea. */
  product: ProductView | null;
}

export interface BusinessInfo {
  id: number;
  name: string;
  one_liner: string;
  project_ids: number[];
  cycle_id: number | null;
}

export type Stage =
  | { kind: "thinking" }
  | { kind: "usage_wait"; note: string }
  | { kind: "owner"; turn: Turn; mode: "strengths" | "question" | "check" | "closing" }
  | { kind: "paused"; note: string | null }
  | { kind: "done" }
  | { kind: "abandoned" };

export const isClosing = (t: Turn) => t.closing;

export function stageOf(v: KickoffView): Stage {
  const { session } = v;
  switch (session.status) {
    case "OPEN":
      return session.note ? { kind: "usage_wait", note: session.note } : { kind: "thinking" };
    case "WAITING_OWNER": {
      const turn = v.turns.find((t) => t.n === v.open_turn);
      if (!turn) return { kind: "thinking" };
      const mode = turn.asks === "strengths" ? "strengths" : isClosing(turn) ? "closing" : turn.kind === "question" ? "question" : "check";
      return { kind: "owner", turn, mode };
    }
    case "PAUSED":
      return { kind: "paused", note: session.note };
    case "DONE":
      return { kind: "done" };
    default:
      return { kind: "abandoned" };
  }
}

/**
 * Required sections the owner settled by saying or confirming, and those left as 모름. The server's coverage counts
 * both as done so the conversation can end; the owner reads "6칸 중 몇 칸을 내가 정했나", so they are told apart.
 */
export function fill(v: KickoffView): { total: number; settled: number; unknown: number } {
  const req = (v.brief?.sections ?? []).filter((s) => s.required);
  if (!req.length) return { total: v.coverage.required_total, settled: v.coverage.required_settled, unknown: 0 };
  return {
    total: req.length,
    settled: req.filter((s) => s.state === "settled").length,
    unknown: req.filter((s) => s.state === "unknown").length,
  };
}

/** `kickoff:<session>:<n>` names the turn a line came from; turn 0 is what the owner typed to open the session. */
export function sourceTurn(source: string | null): number | null {
  const m = /^kickoff:\d+:(\d+)$/.exec(source ?? "");
  return m ? Number(m[1]) : null;
}

export const ANSWER_LIMIT = 4000;
export const MATERIAL_LIMIT = 20000;
export const COUNTER_FROM = 3600;
/** The most questions a new kickoff asks: a session starts with max_turns 20. */
export const MAX_TURNS = 20;
