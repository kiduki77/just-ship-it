import { Link } from "react-router-dom";
import { useLive } from "../api";
import { PlainKey, t } from "../i18n";
import { SendingState } from "./types";

const LIVE_NAME: Record<string, PlainKey> = {
  email: "settings.outgoing.email", slack: "settings.outgoing.slack", github: "settings.outgoing.github", cloudflare: "settings.outgoing.cloudflare",
  calendar: "settings.outgoing.calendar", survey: "settings.outgoing.survey",
};

export interface Sending {
  /** Live and able to send: these really go out. */
  sends: string[];
  /** Live but missing a part (no channel, no token, no mail server): nothing goes out yet. */
  blocked: string[];
  /** One sentence for both, or null when nothing is live. */
  sentence: string | null;
}

/** What really leaves the company, read from the server's `sending`; a live connector that lacks a part never counts. */
export function sendingOf(sending: Record<string, SendingState>): Sending {
  const name = (k: string) => (LIVE_NAME[k] ? t(LIVE_NAME[k]) : k);
  const live = Object.entries(sending).filter(([, c]) => c.mode === "live");
  const sends = live.filter(([, c]) => c.sends).map(([k]) => name(k));
  const blocked = live.filter(([, c]) => !c.sends).map(([k]) => name(k));
  const sentence = live.length ? [
    sends.length ? t("settings.status.live", { names: sends.join(", ") }) : t("settings.status.none_sending"),
    blocked.length ? t("settings.status.blocked", { names: blocked.join(", ") }) : null,
  ].filter(Boolean).join(" ") : null;
  return { sends, blocked, sentence };
}

/** The top bar's word on sending: shown only while something is set to send for real, and a way to Sending out. */
export function SendingChip() {
  const { data } = useLive<Record<string, SendingState>>("/settings/sending", ["activity_events"]);
  if (!data) return null;
  const { blocked, sentence } = sendingOf(data);
  if (!sentence) return null;
  return (
    <div className={`cstate ${blocked.length ? "t-bad" : "t-warn"}`}>
      <Link to="/settings#set-outgoing" className="cstate-btn" title={sentence} aria-label={sentence}>
        <span className="dot" aria-hidden="true" />
        <span className="cstate-t">{t(blocked.length ? "settings.chip.blocked" : "settings.chip.live")}</span>
      </Link>
    </div>
  );
}
