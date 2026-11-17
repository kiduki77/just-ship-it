import { useState } from "react";
import { Link } from "react-router-dom";
import { useCompanyLive } from "../business";
import { PlainKey, t } from "../i18n";
import { useDirtyWhen } from "./dirty";
import { Fields, SaveBar, Section } from "./parts";
import { ACTION_OF_CONNECTOR, Mode, SettingsData } from "./types";
import { Saver, useGuard, useSave } from "./useSave";

interface Connector {
  key: string;
  name: PlainKey;
  what: PlainKey;
  live?: { label: PlainKey; says: PlainKey };
}

const APPROVED_LIVE = { label: "settings.outgoing.mode.live", says: "settings.outgoing.says.live" } as const;

const CONNECTORS: Connector[] = [
  { key: "email", name: "settings.outgoing.email", what: "settings.outgoing.email.what", live: APPROVED_LIVE },
  { key: "slack", name: "settings.outgoing.slack", what: "settings.outgoing.slack.what",
    live: { label: "settings.outgoing.mode.live_now", says: "settings.outgoing.says.live_now" } },
  { key: "github", name: "settings.outgoing.github", what: "settings.outgoing.github.what" },
  { key: "cloudflare", name: "settings.outgoing.cloudflare", what: "settings.outgoing.cloudflare.what" },
  { key: "calendar", name: "settings.outgoing.calendar", what: "settings.outgoing.calendar.what" },
  { key: "survey", name: "settings.outgoing.survey", what: "settings.outgoing.survey.what" },
];

const LABEL: Record<Mode, PlainKey> = { off: "settings.outgoing.mode.off", outbox: "settings.outgoing.mode.outbox", live: APPROVED_LIVE.label };
const SAYS: Record<Mode, PlainKey> = { off: "settings.outgoing.says.off", outbox: "settings.outgoing.says.outbox", live: APPROVED_LIVE.says };
const RANK: Record<Mode, number> = { off: 0, outbox: 1, live: 2 };

/** A connector whose decision rule is Automatic sends without the owner's approval once it is live. */
const autoRule = (data: SettingsData, key: string) => {
  const action = ACTION_OF_CONNECTOR[key];
  return !!action && data.policy_modes[action] === "auto";
};

function Choice({ c, modes, value, onChange, auto, missing, blocks }: {
  c: Connector; modes: Mode[]; value: Mode; onChange: (m: Mode) => void; auto: boolean; missing: string[];
  /** What stops this choice from saving while it is newly chosen: the server refuses until each is done. */
  blocks: string[];
}) {
  const label = (m: Mode) => t(m === "live" && c.live ? c.live.label : LABEL[m]);
  const says = value === "live" && auto ? t("settings.outgoing.says.live_auto") : t(value === "live" && c.live ? c.live.says : SAYS[value]);
  const name = t(c.name);
  return (
    <fieldset className="out">
      <legend><span className="strong">{name}</span> <span className="muted">· {t(c.what)}</span></legend>
      <div className="set-seg" role="radiogroup" aria-label={t("settings.outgoing.mode_group", { name })}>
        {modes.map((m) => (
          <label key={m} className={`${value === m ? "on" : ""} ${m === "live" ? "live" : ""}`}>
            <input type="radio" name={`mode-${c.key}`} value={m} checked={value === m} onChange={() => onChange(m)} />
            {label(m)}
          </label>
        ))}
      </div>
      <p className={`small ${value === "live" ? "warn-t" : "muted"}`}>
        {says}
        {value === "live" && auto && <> <Link to={{ hash: "set-rules" }}>{t("settings.outgoing.to_rules")}</Link></>}
      </p>
      {value === "live" && blocks.length > 0 && (
        <div className="small warn-t set-gaps">
          <p>{t("settings.outgoing.blocked_live")}</p>
          <ul>{blocks.map((m) => <li key={m}>{m}</li>)}</ul>
          <p><Link to={{ hash: "set-connections" }}>{t("settings.outgoing.to_test")}</Link></p>
        </div>
      )}
      {value === "live" && !blocks.length && missing.length > 0 && (
        <div className="small warn-t set-gaps">
          <p>{t("settings.outgoing.cannot_yet")}</p>
          <ul>{missing.map((m) => <li key={m}>{m}</li>)}</ul>
        </div>
      )}
    </fieldset>
  );
}

function OutgoingForm({ data, saver }: { data: SettingsData; saver: Saver }) {
  const current = data.effective.connectors;
  const saved = Object.fromEntries(CONNECTORS.map((c) => [c.key, current[c.key].mode])) as Record<string, Mode>;
  const [modes, setModes] = useState<Record<string, Mode>>(saved);
  const guard = useGuard(saver);
  const changes = CONNECTORS.filter((c) => modes[c.key] !== saved[c.key]);
  useDirtyWhen(changes.length > 0);
  const noLive = CONNECTORS.filter((c) => !data.supported_modes[c.key]?.includes("live")).map((c) => t(c.name));
  const missing = (key: string) => data.sending[key]?.missing ?? [];
  // only a change to live is refused; a connector already live keeps sending as it is
  const blocks = (key: string) => (saved[key] === "live" ? [] : data.sending[key]?.blocks_live ?? []);
  const channel = data.effective.notifications.slack_channel;
  const label = (c: Connector, m: Mode) => t(m === "live" && c.live ? c.live.label : LABEL[m]);
  const summary = changes.length > 0 && t("settings.outgoing.will_change", {
    changes: changes.map((c) => `${t(c.name)} ${label(c, saved[c.key])} → ${label(c, modes[c.key])}`).join(" · "),
  });
  // Only a change toward sending asks first; turning something down saves at once. What one still lacks before it can
  // send comes from the server's dry run, after these lines.
  const up = changes.filter((c) => RANK[modes[c.key]] > RANK[saved[c.key]] && modes[c.key] === "live");
  // a connection that still lacks something says what happens once it can send, so it never claims "from now on"
  // right above the server's "won't really go out yet"
  const lines = up.map((c) => {
    const later = missing(c.key).length > 0;
    if (c.key === "slack") {
      return channel && !later ? t("settings.outgoing.confirm.slack", { channel }) : t("settings.outgoing.confirm.slack_later");
    }
    if (c.key === "email") return t(later ? "settings.outgoing.confirm.email_later" : "settings.outgoing.confirm.email");
    return t(later ? "settings.outgoing.confirm.other_later" : "settings.outgoing.confirm.other", { name: t(c.name) });
  });
  const body = Object.fromEntries(CONNECTORS.map((c) => [c.key, { mode: modes[c.key] }]));
  return (
    <Fields saver={saver} label={t("settings.outgoing.form")} onSubmit={() => guard.run(body, { lines, check: up.length > 0 })}>
      {CONNECTORS.map((c) => (
        <Choice key={c.key} c={c} modes={data.supported_modes[c.key] ?? ["off", "outbox"]} value={modes[c.key]}
          auto={autoRule(data, c.key)} missing={missing(c.key)} blocks={blocks(c.key)} onChange={(m) => setModes({ ...modes, [c.key]: m })} />
      ))}
      {noLive.length > 0 && <p className="small muted">{t("settings.outgoing.no_live", { names: noLive.join(", ") })}</p>}
      <SaveBar saver={saver} guard={guard} disabled={!changes.length} summary={summary}
        changed={Object.keys(data.overrides.connectors ?? {}).length > 0} resetSays={t("settings.outgoing.reset_says")} />
    </Fields>
  );
}

interface ActionRow {
  mode?: string;
  status?: string;
  sent_by_owner?: object | null;
}

/** How many sends sit in the outbox, never sent: the actions the gateway kept instead of sending, less those the owner
 * sent by hand. */
function OutboxLine() {
  const { data } = useCompanyLive<ActionRow[]>("/actions", ["action_executions", "activity_events"]);
  if (!data) return null;
  const n = data.filter((a) => a.mode === "outbox" && a.status === "done" && !a.sent_by_owner).length;
  return (
    <p className="small set-outbox">
      {t(n ? "settings.outgoing.outbox_n" : "settings.outgoing.outbox_none", { n })}{" "}
      <Link to="/routines#sent">{t("settings.outgoing.outbox_see")}</Link>
    </p>
  );
}

export function Outgoing({ data, reload }: { data: SettingsData; reload: () => void }) {
  const saver = useSave("connectors", reload);
  return (
    <Section id="set-outgoing" title={t("settings.section.connectors")} lead={t("settings.outgoing.lead")}>
      <OutboxLine />
      <OutgoingForm key={JSON.stringify([data.effective.connectors, data.policy_modes])} data={data} saver={saver} />
    </Section>
  );
}
