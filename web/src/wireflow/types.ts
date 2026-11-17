/**
 * The planned screens of a spec card, as `GET /requests/{id}` serves them in `screens` (engineering/wireflow.py
 * `for_card`). A card the owner sent back carries the notes it was sent back with and where the redraft stands; the
 * card of a redraft carries what changed and where each note landed.
 */

/** The server's BlockKind (contracts/handoff.py). The renderer is a `Record<BlockKind, ...>`, so a kind added here
 * without a drawing fails `tsc`. */
export type BlockKind = "nav" | "title" | "text" | "notice" | "actions" | "field" | "upload" | "list" | "table"
  | "chips" | "media";
export interface Block { kind: BlockKind; text: string; items: string[] }

/** A question the spec asked, with the owner's answer when they gave one after the screen was drawn. */
export interface Asked { question: string; answer: string | null }

/** Where one note of the owner's landed in the redraft. `screen` is the screen it was written on (`name` is that
 * screen's name in the draft it was written on), `on` the screens that changed for it. */
export interface Landing {
  screen: string;
  name: string;
  note: string;
  outcome: "changed" | "moved" | "dropped" | "missed";
  reason: string;
  on: string[];
}

export interface PlanScreen {
  name: string;
  state: string;
  shows: string;
  blocks: Block[];
  asks: Asked[];
  /** Against the draft the owner sent back; null on a first draft. */
  change: "new" | "changed" | null;
  /** The notes that landed here, and this screen's own note whatever became of it. */
  landed: Landing[];
  /** The note the owner sent this card back with, on a card that was sent back. */
  note: string | null;
}

/** Where the redraft a sent-back card asked for stands. `card` is the card that replaced it; `retry_job` is the
 * failed or stopped job the owner can run again. `dropped`: the owner gave up on the notes and declined the spec. */
export interface Redraft {
  state: "writing" | "failed" | "stopped" | "dropped" | "done";
  card: number | null;
  retry_job: number | null;
}

/** One step a user takes: the screen they are on, what they do there, and the screens that branch off it. */
export interface Step { screen: string; do: string; branches: string[] }

/** One story's walk through the screens: its steps, what the user gets at the end, and the empty case. */
export interface Row {
  story: string;
  steps: Step[];
  then: string;
  empty: { screen: string | null; then: string } | null;
}

export interface Plan {
  mode: "plan";
  screens: Record<string, PlanScreen>;
  rows: Row[];
  /** Screens no row walks and no step branches from. */
  loose: string[];
  /** Screens the draft the owner sent back had and this one does not. */
  removed: { id: string; name: string; state: string; landed: Landing[] }[];
  /** Notes whose screen is gone and that landed nowhere. */
  unplaced: Landing[];
  redraft: Redraft | null;
  /** How many screens, steps or blocks the server clipped to fit. */
  dropped: number;
}

/** The spec writer's story, as the card lists it (`action_params.stories`). */
export interface Story { id: string; priority: "P1" | "P2" | "P3"; says: string }
