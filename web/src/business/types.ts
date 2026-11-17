import type { Cite } from "../components/attach";
import { toneOf } from "../components/ui";
import { PlainKey, t } from "../i18n";
import { stageLabel } from "../components/project";
import { label } from "../labels";

export type Provenance = "said" | "confirmed" | "guess" | "unknown" | "blank";

export interface BriefLine {
  id: string;
  text: string;
  provenance: Provenance;
  source: string | null;
  quote?: string | null;
  note?: string | null;
  /** The file and page the quote is from, when it is from an attached file. */
  doc?: Cite;
}

export interface BriefSection {
  key: string;
  title: string;
  legacy: boolean;
  required: boolean;
  quiet: boolean;
  gate: boolean;
  holds: string;
  drives: string;
  default: string;
  state: "settled" | "guess" | "unknown" | "empty";
  lines: BriefLine[];
}

export interface BriefView {
  brief: {
    id: number;
    version: number;
    approved_at: string;
    sections: BriefSection[];
    empty_defaults: { key: string; title: string; state: string; default: string }[];
  } | null;
  draft_waiting: boolean;
}

export interface StageNumber {
  stage: "interest" | "first_value" | "repeat";
  value: number | null;
  why_unknown: string | null;
  series: { day: string; value: number }[];
  /** The row is shown because the CEO guesses the buyers are developers (interest only; absent on an older server). */
  guess?: boolean;
}

/** One goal of the done sentence, in the brief's words. `measure` names the step of the people list that counts it;
 * null means the list cannot count it and the owner checks it, so `current` is null too. `number` is null for a plan
 * goal line that states no number the company can read. */
export interface Goal {
  text: string;
  number: number | null;
  unit: string;
  measure: "contacted" | "talked" | "first_value" | "repeat" | "repeat_talked" | null;
  current: number | null;
}

export interface Completion {
  done: boolean;
  sentence: string;
  targets_from: "brief" | "default";
  goals: Goal[];
  /** The index in `goals` of the goal the overview shows. */
  headline: number;
  used_twice: number;
  talked_of_them: number;
  missing: string[];
}

export interface Numbers {
  stages: StageNumber[];
  completion: Completion;
}

export type Verdict = "continue" | "stop" | "change" | "routine";

export interface NextBet {
  title: string;
  pitch: string;
  question: string;
  in_scope: boolean;
  reason: string;
}

export interface Retro {
  mode: "stats" | "people_quotes";
  verdict: Verdict;
  verdict_reason: string;
  next_bets: NextBet[];
  notes: string[];
  did?: string;
  next?: string;
  users_said?: { person_ref: string; quote: string }[];
  hypothesis?: string;
  causes?: { cause: string; evidence_ids: string[]; confidence: "high" | "mid" | "low" }[];
  counter_evidence?: string[];
}

export interface BetRun {
  project_id: number;
  ref: string;
  name: string;
  status: string;
  phase: string;
  spent_usd: number;
  /** the project's one state (engineering.status.project_stage): a finished run whose build is not done is not done */
  stage: string;
}

const STAGE_TONE: Record<string, string> = { DEV_STOPPED: "bad", DEV_ACTIVE: "run", DEV_DELIVERED: "warn", MERGED: "warn" };

/** A bet's run as the owner reads it, in the same words as the project's own page and the Projects list. */
export const runStatus = (r: BetRun): { text: string; tone: string } =>
  ({ text: stageLabel(r), tone: STAGE_TONE[r.stage] ?? toneOf(r.status) });

export interface Bet {
  id: number;
  ref: string;
  title: string;
  pitch: string;
  audience: string | null;
  question: string | null;
  appetite_days: number | null;
  budget_usd: number | null;
  exit_signal: string | null;
  circuit_breaker: string | null;
  run_mode: "ai" | "owner";
  origin: "owner" | "project_manager" | "migration";
  source_bet_id: number | null;
  status: "PROPOSED" | "PICKED" | "RUNNING" | "LISTENING" | "REVIEW" | "DONE" | "STOPPED" | "DROPPED";
  status_reason: string | null;
  verdict: Verdict | null;
  retro: Retro | null;
  end_cause: string | null;
  started_at: string | null;
  finished_at: string | null;
  legacy: boolean;
  run: BetRun | null;
  window: BetWindow | null;
  attention: string[];
  /** The budget that refused the last retro round and still has no room; absent from a server before it. */
  budget_stop?: { cap: "project" | "month"; project_id: number | null; says: string } | null;
}

/** Where the owner raises the budget that stopped a bet's work. */
export const budgetLink = (stop: NonNullable<Bet["budget_stop"]>) =>
  stop.project_id ? `/projects/${stop.project_id}/settings#budget` : "/audit/costs";

/** A live bet's market window: when it closes and what came in since the bet started (the business's counts). */
export interface BetWindow {
  ends_at: string;
  days_left: number;
  so_far: { posts: number; owner_tasks: number; numbers: number; contacted: number | null };
}

export interface Rung {
  id: string;
  name: string;
  exit_signal: string;
  reached_at: string | null;
  state: "reached" | "current" | "ahead";
}

export interface Checkpoint {
  id: string;
  on: string;
  title: string;
  kind: "date" | "rung_exit";
  rung_id: string | null;
  done_at: string | null;
  days_until: number;
}

export interface Cycle {
  id: number;
  number: number;
  starts_on: string;
  ends_on: string;
  days_left: number;
}

export type Lane = "now" | "next" | "later" | "done";

export interface Roadmap {
  business: { cycle_days: number };
  bets: Bet[];
  rungs: Rung[];
  checkpoints: Checkpoint[];
  cycle: Cycle | null;
  lanes: Record<Lane, number[]>;
  limits: { max_running: number; running: number; max_routines: number };
}

export interface Routine {
  bet_id: number;
  title: string;
  state: "proposed" | "active" | "stopped" | "declined";
  request_id: number | null;
  every_days: number | null;
  per_run_budget_usd: number | null;
  next_run_at: string | null;
}

export interface Person {
  id: number;
  ref: string;
  handle: string;
  display_name: string;
  platform: string;
  profile_url: string | null;
  where_found_url: string;
  source: string;
  why: string;
  stage: string;
  quotes: { text: string; url?: string | null; at?: string }[];
  next_action: string | null;
  next_action_at: string | null;
  /** Stages the owner may move this person to, in the server's own order (forward on the ladder, dropped, back to found). */
  next_stages?: string[];
  /** How they heard about the product: a channel key, friend, search or other, and their own words. Null until the owner records it. */
  heard_via?: string | null;
  heard_words?: string | null;
  /** The row the list counts them in (the answer, or what their source says) and its name, shown while there is no answer. */
  heard_row?: string;
  heard_row_label?: string;
}

/** What the owner may pick for "how did they hear about you?": the business's channels, then friend, search and other. */
export interface HeardChoice { key: string; kind: "channel" | "word"; label: string }

export interface People {
  items: Person[];
  by_stage: Record<string, number>;
  by_source: Record<string, number>;
  total: number;
  /** Where the company looks for this business, where only the owner can (a login hides it), and every platform key
   * with this business's own places first. */
  places: { searched: string[]; by_hand: string[]; platforms: string[] };
  heard_choices?: HeardChoice[];
}

export interface Idea {
  id: number;
  display_id: string;
  text: string;
  links: string[];
  status: "NEW" | "TRIAGING" | "TRIAGED" | "FAILED";
  status_ko: string;
  disposition: string | null;
  disposition_ko: string | null;
  needs_owner: boolean;
  reason: string | null;
  note: string | null;
  owner_override: { from: string | null; to: string; note: string | null } | null;
  created_at: string;
  connections: { kind: string; id: number; label: string; merged?: boolean }[];
}

export interface Ideas {
  ideas: Idea[];
  counts: { waiting: number; failed: number; discard_proposed: number; this_week: number; total: number };
}

const DAY = 86_400_000;

/** Whole days until a bet's window closes, counted from when it started; negative once it is over. */
export function daysLeft(bet: Bet, now = Date.now()): number | null {
  if (bet.window) return Math.ceil((new Date(bet.window.ends_at).getTime() - now) / DAY);
  if (!bet.appetite_days || !bet.started_at) return null;
  return Math.ceil((new Date(bet.started_at).getTime() + bet.appetite_days * DAY - now) / DAY);
}

export const isLive = (bet: Bet) => bet.status === "RUNNING" || bet.status === "LISTENING";

export const usd = (n: number | null | undefined) => (n == null ? "" : `$${n.toFixed(n < 10 ? 2 : 0)}`);

/** The cleaned business name: the test marker is not for the owner. */
export const cleanName = (name: string) => name.replace(/^\[TEST\]\s*/, "");

export const VERDICT: Record<Verdict, PlainKey> = {
  continue: "business-bets.verdict.continue", stop: "business-bets.verdict.stop", change: "business-bets.verdict.change",
  routine: "business-bets.verdict.routine",
};

const CAUSE: Record<string, PlainKey> = {
  run_done: "business-bets.cause.run_done", run_failed: "business-bets.cause.run_failed",
  time_limit: "business-bets.cause.time_limit", budget_limit: "business-bets.cause.budget_limit", owner: "business-bets.cause.owner",
};

/** How a bet ended, from its `end_cause` code, or null for a code with no words. */
export const causeText = (code: string | null): string | null => (code && CAUSE[code] ? t(CAUSE[code]) : null);

export const CONFIDENCE: Record<"high" | "mid" | "low", PlainKey> = {
  high: "business-bets.confidence.high", mid: "business-bets.confidence.mid", low: "business-bets.confidence.low",
};

/** Where a plan line came from, in the owner's words: `kickoff:3:5` is the fifth turn of the kickoff chat. */
export function sourceText(source: string | null): string | null {
  const m = /^kickoff:\d+:(\w+)$/.exec(source ?? "");
  if (!m) return null;
  if (m[1] === "0") return t("business.brief.src.first_line");
  if (m[1] === "material") return t("business.brief.src.material");
  return t("business.brief.src.turn", { n: m[1] });
}

export type ProfileLineKey = "buyer" | "reach" | "has";

/** One value of a customer-description line. A coded value has a `label`, a free one a `sentence`. */
export interface ProfileValue {
  code: string | null;
  label: string | null;
  sentence: string | null;
  provenance: Provenance;
  provenance_label: string;
}

export interface ProfileLine {
  line: ProfileLineKey;
  label: string;
  /** The server's composed sentence. It starts with `label`, so the card prints `values` instead. */
  sentence: string;
  values: ProfileValue[];
  weakest: Provenance;
  weakest_label: string;
  known: boolean;
  asked: boolean;
  guessed: boolean;
  confirmable: boolean;
  stale: boolean;
  stale_since: string | null;
  /** The live row ids the owner is looking at; an edit sends them back as `if_live`. */
  live_ids: number[];
  uses: string[];
}

export interface ProfileChoice {
  code: string;
  label: string;
}

/** `GET /businesses/{id}/profile`: who buys, how they are reached and what the owner has, each with who stands behind it. */
export interface Profile {
  business_id: number;
  state: "not_written" | "ready";
  reading: boolean;
  backfilled: boolean;
  question: string;
  lines: ProfileLine[];
  choices: Record<ProfileLineKey, ProfileChoice[]>;
}

export interface ProfileEdit {
  profile: Profile;
  event_id: number | null;
}

/** On a post to-do: the lines of the customer description it was picked from a guess about. */
export interface FitView {
  guessed: { line: string; line_label: string; labels: string[] }[];
  changed: boolean;
}

/** A tracked post card's link (`track` on the to-do and on its card). `state` says which line explains it. */
export interface TrackView {
  tag: string;
  link: "tagged" | "plain" | "none" | null;
  links: { door: string; url: string; param: string | null }[];
  state: "tagged" | "plain" | "say" | "no_door";
  derived: boolean;
  say?: string;
}

/** A count and what it rests on. A missing figure (null) means none, never zero. */
export interface TrackFigure {
  n: number;
  basis: "read" | "told" | "typed" | "at_least";
  basis_label: string;
  refs: string[];
  as_of: string | null;
}

export interface TrackTag { tag: string; post: string; visits: number | null; signups: number | null }

/** One place people came from: a channel, a word (friend, search, other), a derived row or the people who came on their own. */
export interface TrackRow {
  key: string;
  label: string;
  kind: "channel" | "word" | "derived" | "organic";
  posts: number;
  visits: TrackFigure | null;
  visits_at_least: TrackFigure | null;
  signups: TrackFigure | null;
  people: TrackFigure;
  got_value: TrackFigure;
  tallied: TrackFigure | null;
  tallied_today: number;
  tags: TrackTag[];
}

export interface TrackHost {
  host: string;
  channels: { key: string; label: string }[];
  shared: boolean;
  organic: boolean;
  last_14d: TrackFigure;
  at_least: TrackFigure;
}

export interface TrackMilestone {
  n: number;
  have: number;
  reached_on: string | null;
  estimated: number;
  by_key: { key: string; label: string; n: number }[];
}

export interface Door { kind: string; label: string; url: string; param: string | null; derived: boolean }

export interface Tracking {
  business_id: number;
  today: string;
  rows: TrackRow[];
  hosts: TrackHost[];
  milestones: TrackMilestone[];
  answers: number;
  too_few: boolean;
  min_answers: number;
  referrers: { state: "no_repo" | "none_yet" | "unreadable" | "ok"; reason: string | null; as_of: string | null; stale: boolean };
  doors: Door[];
  homepage_offer: { url: string; host: string; text: string } | null;
  choices: HeardChoice[];
  signup: { title: string; question: string; options: string[]; note: string };
}

/** One text of the First 10 kit as `GET /businesses/{id}/kit` sends it. `body` is null when the text was refused, and then
 * `refused_text` says why in the owner's language. `chars` and `limit` count characters, so no count is worked out here. */
export interface KitText {
  id: number;
  body: string | null;
  refused: string | null;
  refused_text: string | null;
  lang: string;
  chars: number;
  limit: number;
  stale: boolean;
  guess: boolean;
  made_at: string;
  basis_view: { quotes: string[]; link: string | null };
  /** Set when this text stands and the latest try to write it again was refused: what was refused, why, and when. */
  last_refused: { refused: string; refused_text: string; at: string } | null;
}

export interface KitToday {
  person: Person;
  reason: "overdue" | "new";
  reason_text: string;
  draft: KitText | null;
  no_draft_reason: "public_first" | "not_written" | null;
  no_draft_text: string | null;
}

export type KitPart = "message" | "profile" | "invite";

/** How the business's latest write of the kit went. `error_text` is set for a write that failed or was cancelled, and
 * `waiting_text` (with `waiting_until`) for one parked on a usage limit, both in the owner's language. */
export interface KitJob {
  status: string;
  error_text: string | null;
  waiting_until: string | null;
  waiting_text: string | null;
}

export interface Kit {
  business_id: number;
  milestone: { n: number; have: number; estimated: number };
  today: KitToday[];
  profile: { line: KitText | null; short: KitText | null; about: KitText | null };
  invite: { known: KitText | null; problem: KitText | null };
  link: { url: string | null; state: "ready" | "no_place" };
  owner_said: boolean;
  prepared: boolean;
  /** When a write last stored a text: it moving is how the console knows the kit was updated. */
  prepared_at: string | null;
  writing: boolean;
  can_write: boolean;
  job: KitJob | null;
}

/** One place in `GET /businesses/{id}/channels`. `checked_on` is a date (no time) and is set only for a checked place. */
export interface ChannelPlace {
  key: string;
  name: string;
  region: "global" | "en" | "ko";
  /** The registry's catch-all entries (`other`, `reddit`): they name no place, so the list leaves them out. */
  generic: boolean;
  state: "checked" | "unchecked";
  checked_on: string | null;
  source_url: string | null;
  /** A page that was read but is not this place's rules. Only an unchecked place has one. */
  background_url: string | null;
  rules: string;
  unchecked_why: "not_read" | "per_unit" | "no_source" | null;
  unchecked_text: string | null;
  does: "points" | "draft" | "nothing";
  fits: boolean;
  repost_days: number;
  requires_unmet: { item: string; label: string }[];
  dropped: boolean;
}

export interface ChannelPlaces {
  places: ChannelPlace[];
  counts: { checked: number; unchecked: number };
}
