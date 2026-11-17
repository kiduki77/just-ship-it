import { useState } from "react";
import { PlainKey, t } from "../i18n";
import { Link } from "react-router-dom";
import { useDirtyWhen } from "./dirty";
import { FieldError, Fields, SaveBar, Section } from "./parts";
import { SettingsData } from "./types";
import { Saver, useSave } from "./useSave";

const KINDS: [string, PlainKey][] = [
  ["decision_needed", "settings.notify.kind.decision_needed"],
  ["failed", "settings.notify.kind.failed"],
  ["usage_wait", "settings.notify.kind.usage_wait"],
  ["owner_task", "settings.notify.kind.owner_task"],
  ["daily_briefing", "settings.notify.kind.daily_briefing"],
];

const DEFAULT_QUIET = { start: "23:00", end: "07:00" };
/** The server's own rule for a time of day (app_settings._HHMM): 24-hour HH:MM. */
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function NotificationForm({ data, saver }: { data: SettingsData; saver: Saver }) {
  const n = data.effective.notifications;
  const [channel, setChannel] = useState(n.slack_channel);
  const [kinds, setKinds] = useState<string[]>(n.kinds);
  const [briefing, setBriefing] = useState(n.briefing_time);
  const [quiet, setQuiet] = useState(n.quiet_hours);
  const [url, setUrl] = useState(n.console_url);
  const slackMode = data.effective.connectors.slack.mode;
  const toggle = (k: string) => setKinds(kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k]);
  const body = { slack_channel: channel.trim(), kinds, briefing_time: briefing, quiet_hours: quiet, console_url: url.trim() };
  const saved = { slack_channel: n.slack_channel, kinds: n.kinds, briefing_time: n.briefing_time, quiet_hours: n.quiet_hours, console_url: n.console_url };
  useDirtyWhen(JSON.stringify(body) !== JSON.stringify(saved));
  const badTime = (v: string) => (HHMM.test(v) ? null : t("settings.notify.time_bad"));
  const errs = { briefing: badTime(briefing), qs: quiet ? badTime(quiet.start) : null, qe: quiet ? badTime(quiet.end) : null };
  const invalid = Object.values(errs).filter(Boolean).length;
  return (
    <Fields saver={saver} label={t("settings.notify.form")} onSubmit={() => saver.save(body)}>
      {slackMode !== "live" && (
        <p className="small set-help warn-t">
          {t(slackMode === "off" ? "settings.notify.slack_off" : "settings.notify.slack_outbox")}{" "}
          <Link to={{ hash: "set-outgoing" }}>{t("settings.notify.to_outgoing")}</Link>
        </p>
      )}
      <div className="field set-narrow">
        <label htmlFor="nt-ch">{t("settings.notify.channel")}</label>
        <input id="nt-ch" value={channel} maxLength={80} autoComplete="off" aria-describedby="nt-ch-h" onChange={(e) => setChannel(e.target.value)} />
        <p id="nt-ch-h" className="small muted set-help">{t("settings.notify.channel_help")}</p>
      </div>
      <fieldset className="checks">
        <legend>{t("settings.notify.when")}</legend>
        {KINDS.map(([k, text]) => (
          <label key={k}><input type="checkbox" checked={kinds.includes(k)} onChange={() => toggle(k)} /> {t(text)}</label>
        ))}
      </fieldset>
      <div className="set-grid">
        <div className="field">
          <label htmlFor="nt-time">{t("settings.notify.briefing")} <span className="muted">{t("settings.notify.time_hint")}</span></label>
          <input id="nt-time" className="set-time" inputMode="numeric" placeholder="09:00" maxLength={5} value={briefing} required
            aria-invalid={!!errs.briefing} aria-describedby="nt-time-e" onChange={(e) => setBriefing(e.target.value.trim())} />
          <FieldError id="nt-time-e" text={errs.briefing} />
        </div>
        <div className="field">
          <label htmlFor="nt-url">{t("settings.notify.url")}</label>
          <input id="nt-url" value={url} maxLength={200} autoComplete="off" aria-describedby="nt-url-h" onChange={(e) => setUrl(e.target.value)} />
          <p id="nt-url-h" className="small muted set-help">{t("settings.notify.url_help")}</p>
        </div>
      </div>
      <div className="field">
        <label className="check-l"><input type="checkbox" checked={quiet !== null} onChange={(e) => setQuiet(e.target.checked ? DEFAULT_QUIET : null)} /> {t("settings.notify.quiet")}</label>
        {quiet && (
          <div className="row wrap">
            <label className="sr-only" htmlFor="nt-qs">{t("settings.notify.quiet_start")}</label>
            <input id="nt-qs" className="set-time" inputMode="numeric" placeholder="23:00" maxLength={5} value={quiet.start}
              aria-invalid={!!errs.qs} aria-describedby="nt-q-e" onChange={(e) => setQuiet({ ...quiet, start: e.target.value.trim() })} />
            <span className="muted">{t("settings.notify.between")}</span>
            <label className="sr-only" htmlFor="nt-qe">{t("settings.notify.quiet_end")}</label>
            <input id="nt-qe" className="set-time" inputMode="numeric" placeholder="07:00" maxLength={5} value={quiet.end}
              aria-invalid={!!errs.qe} aria-describedby="nt-q-e" onChange={(e) => setQuiet({ ...quiet, end: e.target.value.trim() })} />
            {t("settings.notify.after") && <span className="muted">{t("settings.notify.after")}</span>}
          </div>
        )}
        <FieldError id="nt-q-e" text={errs.qs ?? errs.qe} />
        <p className="small muted set-help">{t("settings.notify.quiet_help")}</p>
      </div>
      <SaveBar saver={saver} invalid={invalid} changed={Object.keys(data.overrides.notifications ?? {}).length > 0} />
    </Fields>
  );
}

export function Notifications({ data, reload }: { data: SettingsData; reload: () => void }) {
  const saver = useSave("notifications", reload);
  return (
    <Section id="set-notify" title={t("settings.section.notifications")} lead={t("settings.notify.lead")}>
      <NotificationForm key={JSON.stringify(data.effective.notifications)} data={data} saver={saver} />
    </Section>
  );
}
