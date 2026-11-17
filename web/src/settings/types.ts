import { Row } from "../api";
import { Strengths } from "../components/strengths";

export type Mode = "off" | "outbox" | "live";
export type PolicyMode = "auto" | "approve" | "strong_approve" | "deny";

export interface UsageValues {
  subscription: string;
  day_usd_eq: number | null;
  week_usd_eq: number | null;
  capacity_week_usd_eq: number | null;
  timezone: string;
  owner_reserve_pct: number;
}

export interface NotificationValues {
  slack_channel: string;
  kinds: string[];
  briefing_time: string;
  quiet_hours: { start: string; end: string } | null;
  console_url: string;
}

export interface BetValues {
  max_running: number;
  weekly_post_cap: number;
  max_routines: number;
  failed_grace_minutes: number;
  window_days: number;
}

export interface EffectiveSettings {
  owner: { strengths: Strengths };
  usage: UsageValues;
  connectors: Record<string, { mode: Mode }>;
  policy: { overrides: Record<string, PolicyMode> };
  notifications: NotificationValues;
  bets: BetValues;
}

export interface SecretStatus {
  label: string;
  set: boolean;
  last4: string | null;
  problem?: string;
}

export interface Need {
  what: string;
  usd_eq: number;
}

/** A live-capable connector as the server reads it: `sends` only when it is live and nothing it needs is missing. */
export interface SendingState {
  mode: Mode;
  missing: string[];
  sends: boolean;
  /** What stops a save from turning it live now (Slack: every missing part and a passed connection test). */
  blocks_live?: string[];
}

export interface SettingsData extends Row {
  effective: EffectiveSettings;
  overrides: Record<string, Row>;
  supported_modes: Record<string, Mode[]>;
  tool_policy: Record<string, PolicyMode>;
  policy_modes: Record<string, PolicyMode>;
  policy_floors: Record<string, PolicyMode>;
  secrets: Record<string, SecretStatus>;
  problems: { section: string; message: string }[];
  usage_needs: { owner: Need; background: Need };
  sending: Record<string, SendingState>;
  /** The monthly budget in force (Costs reads the same): this month's limit, or the standing one before the month has a row. */
  month_limit_usd: number;
}

export interface UsageWindow {
  cap_usd_eq: number | null;
  used_usd_eq: number;
  reserved_usd_eq: number;
  used_pct: number | null;
  background_limit_pct: number;
  starts_at: string;
  resets_at: string;
}

export interface UsageView {
  subscription: string;
  timezone: string;
  owner_reserve_pct: number;
  day: UsageWindow;
  week: UsageWindow;
}

export interface Alert extends Row {
  kind: string;
  detail?: string;
  waiting?: number;
}

/** The decision rule that decides whether a connector's sends need the owner: the server's ACTION_CONNECTOR, reversed. */
export const ACTION_OF_CONNECTOR: Record<string, string> = {
  email: "external.email_send", calendar: "external.calendar_create", survey: "external.survey_distribute",
};
export const CONNECTOR_OF_ACTION: Record<string, string> = Object.fromEntries(Object.entries(ACTION_OF_CONNECTOR).map(([c, a]) => [a, c]));

export const POLICY_ORDER: PolicyMode[] = ["auto", "approve", "strong_approve", "deny"];
