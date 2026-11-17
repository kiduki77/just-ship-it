// What GET /api/businesses/{id}/your-turn and GET /api/projects/{id}/path say (backend/justshipit/contracts/your_turn.py).
import { t } from "./i18n";
import { Known } from "./components/pathContext";

export type HoldScope = "none" | "tasks" | "planning" | "run";

export interface TurnCard {
  id: number;
  ref: string;
  title: string;
  type: string;
  project_id: number | null;
  scope: HoldScope;
  /** What it sorts as: `none` once everything it held is moot. */
  effective: HoldScope;
  frees: number;
  behind: number;
  nothing_live: boolean;
  expires_at: string | null;
  /** Its place in the one order, from 1. */
  rank: number;
}

export interface Counts { done: number; total: number; held: number; failed: number; cards: number; todos: number }

export interface Attention { id: number; ref: string; key: string; title: string; status: string }

export interface OutlineNode {
  kind: "cycle" | "stage" | "project";
  id: number;
  ref: string | null;
  title: string | null;
  number: number | null;
  status: string | null;
  experiment: { id: number; title: string; status: string } | null;
  counts: Counts;
  cards: TurnCard[];
  attention: Attention[];
  children: OutlineNode[];
}

export interface Outline { goal: string | null; counts: Counts; children: OutlineNode[] }

export interface YourTurn { business_id: number | null; cards: TurnCard[]; outline: Outline; as_of: string }

export type NodeKind = "goal" | "cycle" | "stage" | "experiment" | "project" | "task" | "decision" | "project_manager"
  | "todo" | "origin" | "group";
export type EdgeKind = "contains" | "depends_on" | "holds" | "raised_by" | "followed";

export interface PathNode {
  id: string;
  kind: NodeKind;
  ref: string | null;
  ref_id: number | null;
  label_key: string;
  vars: Record<string, string | number>;
  state: string | null;
  known: Known;
  focus: boolean;
  chain: boolean;
  waiting: boolean;
  held: boolean;
  scope: HoldScope | null;
  frees: number | null;
  behind: number | null;
  count: number | null;
  members: string[];
}

export interface PathEdge {
  id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  label_key: string;
  known: Known;
  phase: "start" | "finish" | null;
  effect: string | null;
  via_group: boolean;
}

export interface PathView {
  project_id: number;
  focus: string | null;
  rev: string;
  too_big: boolean;
  neighbourhood: boolean;
  can_narrow: boolean;
  unfolded: string[];
  total: number;
  shown: number;
  limits: { default: number; unfolded: number; too_big: number };
  nodes: PathNode[];
  edges: PathEdge[];
  outline: Outline;
  as_of: string;
}

/** What answering a decision frees, in the words the card block uses: "Holds 2 tasks · and 5 tasks after them". */
export function freesText(c: Pick<TurnCard, "scope" | "effective" | "frees" | "behind" | "nothing_live">): string {
  if (c.nothing_live) return t("path.frees.moot");
  if (c.scope === "run") return t("path.frees.run");
  if (c.scope === "planning") return t("path.frees.planning");
  if (c.scope === "none") return t("path.frees.none");
  const holds = c.frees > 0 ? t("path.frees.tasks", { n: c.frees }) : t("path.frees.shared");
  const behind = c.behind > 0 ? t("path.frees.behind", { n: c.behind }) : null;
  return [holds, behind].filter(Boolean).join(" · ");
}
