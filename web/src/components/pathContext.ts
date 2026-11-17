// What GET /api/requests/{id} says about a card's place in the work: `contracts/card_context.py`, serialised.
import { keys, t } from "../i18n";

// The server names each text by a catalog key and its values (contracts/card_context.py), so a key the console lacks
// reads as "Not recorded" and never throws.
export const KNOWN = new Set(keys("path."));
type Say = (key: string, vars?: Record<string, string | number>) => string;
export const say = t as unknown as Say;
export const worded = (key: string, vars: Record<string, string | number> = {}) => (KNOWN.has(key) ? say(key, vars) : t("path.unknown"));

export type Known = "recorded" | "derived" | "backfilled" | "matched" | "unknown";
export type StepKind =
  | "goal" | "cycle" | "stage" | "experiment" | "project" | "task" | "round" | "decision" | "chat" | "server" | "call"
  | "origin" | "this";

export interface PathStep {
  kind: StepKind;
  ref: string | null;
  ref_id: number | null;
  /** A `path.step.*` catalog key; the values it takes are in `vars`. */
  label_key: string;
  vars: Record<string, string | number>;
  /** The record's own status code. */
  state: string | null;
  known: Known;
}

export interface PathRef { kind: "evidence" | "task" | "request" | "round"; id: number; label: string | null; state: string | null }

export interface HeldTask {
  id: number;
  key: string;
  title: string;
  phase: "start" | "finish";
  state: string;
  live: boolean;
  ended_on: string | null;
}

export interface AfterLine {
  source: "declared" | "server" | "followed";
  option_key: string | null;
  effect: string | null;
  /** The server's own words, for source "server". */
  text: string | null;
  targets: PathRef[];
  at: string | null;
}

export interface CardContext {
  request_id: number;
  status: string;
  decided: boolean;
  why: {
    reason: string | null;
    refs: PathRef[];
    note: string | null;
    agent: string;
    at: string;
    recorded: boolean;
    stale: boolean;
    changed: PathRef[];
  };
  chain: PathStep[];
  also_asked: PathStep[];
  parks_project: boolean;
  holds: {
    scope: "none" | "tasks" | "planning" | "run";
    tasks: HeldTask[];
    frees: number;
    behind: number;
    nothing_live: boolean;
  } | null;
  after: AfterLine[];
  unanswered: {
    policy: "close" | "default" | "keep_waiting" | null;
    when: string | null;
    default_option: string | null;
    affects: HeldTask[];
  } | null;
  complete: boolean;
  recorded_since: string | null;
  as_of: string;
}

/** What the card carries: nothing yet (the panel is still catching up), the context, or the server's "couldn't build it". */
export type CardContextField = CardContext | { error: true } | undefined;

export const contextFailed = (c: CardContextField): c is { error: true } => !!c && "error" in c;
