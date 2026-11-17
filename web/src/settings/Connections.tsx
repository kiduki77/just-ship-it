import { useState } from "react";
import { Link } from "react-router-dom";
import { api, refreshLive } from "../api";
import { useCompanyLive } from "../business";
import { Chip } from "../components/kit";
import { ActionButton } from "../components/ui";
import { PlainKey, t } from "../i18n";
import { Guide, GUIDES } from "./guides";
import { Section } from "./parts";
import { Alert, Mode, SecretStatus } from "./types";

type Tone = "ok" | "bad" | "muted";

function Steps({ guide }: { guide: Guide }) {
  return (
    <div className="conn-guide">
      {guide.minutes !== null && <p className="small muted">{t("settings.connections.time", { n: guide.minutes })}</p>}
      <ol className="conn-steps">
        {guide.steps.map((s) => (
          <li key={s.text}>
            {t(s.text)}
            {s.link?.href.startsWith("#") && <> <Link to={{ hash: s.link.href.slice(1) }}>{t(s.link.label)}</Link></>}
            {s.link && !s.link.href.startsWith("#") && (
              <>
                {" "}
                <a href={s.link.href} target="_blank" rel="noopener noreferrer">{t(s.link.label)}</a>
                <span className="muted small">{t("settings.connections.external")}</span>
              </>
            )}
          </li>
        ))}
      </ol>
      {guide.note && <p className="small muted">{t(guide.note)}</p>}
    </div>
  );
}

/** The value goes from this box to the server and nowhere else: it is cleared after saving and never read back. */
function TokenBox({ guide, secret, reload }: { guide: Guide; secret: string; reload: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const id = `tok-${secret}`;

  async function save() {
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await api(`/settings/secrets/${secret}`, { method: "PUT", body: JSON.stringify({ value }) });
      setValue("");
      setSaved(true);
      reload();
    } catch (e: any) {
      setError(e?.kind === "offline" || e?.kind === "server" ? t("settings.save_failed.offline") : e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="conn-token">
      <label htmlFor={id}>{t("settings.connections.paste", { name: guide.name })}</label>
      <div className="row wrap">
        <input id={id} type="password" autoComplete="off" spellCheck={false} value={value} placeholder={t("settings.connections.paste_hint")}
          aria-describedby={`${id}-h`} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") save(); }} />
        <button type="button" disabled={busy || !value.trim()} onClick={save}>{busy ? t("settings.saving") : t("settings.save")}</button>
      </div>
      <p id={`${id}-h`} className="small muted">{t("settings.connections.kept")}</p>
      {saved && <p className="small ok-t" role="status">{t("settings.saved")}</p>}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  );
}

function SlackTest({ mode, reload }: { mode: Mode; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  async function run() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api<{ message: string }>("/settings/test/slack", { method: "POST", body: "{}" });
      setResult({ ok: true, text: r.message });
    } catch (e: any) {
      setResult({ ok: false, text: e.message ?? String(e) });
    } finally {
      setBusy(false);
      // A refused token marks Slack disconnected and a passing test clears it, with no change event: the header, the
      // top bar and the alert read it again now, and Sending out reads whether Slack may go live.
      refreshLive(["activity_events"]);
      reload();
    }
  }
  return (
    <div className="conn-test">
      <button type="button" disabled={busy} onClick={run}>{busy ? t("settings.connections.testing") : t("settings.connections.test")}</button>
      <span className="small muted">{t(mode === "outbox" ? "settings.connections.test_says_outbox" : "settings.connections.test_says")}</span>
      {result && <div className={result.ok ? "conn-ok" : "error"} role={result.ok ? "status" : "alert"}>{result.text}</div>}
    </div>
  );
}

function Row({ guide, status, down, reload, slackMode }: {
  guide: Guide; status: SecretStatus | undefined; down: Alert | undefined; reload: () => void; slackMode: Mode;
}) {
  const secret = guide.secret;
  const [open, setOpen] = useState(false);
  const set = !!status?.set;
  const show = open || !!down;
  const state: { label: PlainKey; tone: Tone } = guide.soon ? { label: "settings.connections.soon", tone: "muted" }
    : down ? { label: "settings.connections.down", tone: "bad" } : set ? { label: "settings.connections.on", tone: "ok" }
    : { label: "settings.connections.off", tone: "muted" };
  const panel = `conn-${guide.name}`;

  return (
    <li className="conn">
      <div className="conn-head">
        <div className="conn-main">
          <p className="conn-t">
            <span className="strong">{guide.name}</span> <Chip tone={state.tone}>{t(state.label)}</Chip>
            {set && status?.last4 && <span className="mono muted"> ····{status.last4}</span>}
          </p>
          <p className="small muted">{t(guide.soon ?? guide.why)}</p>
          {secret === "slack_bot_token" && set && slackMode !== "live" && (
            <p className="small warn-t">
              {t(slackMode === "off" ? "settings.connections.slack_mode_off" : "settings.connections.slack_mode_outbox")}{" "}
              <Link to={{ hash: "set-outgoing" }}>{t("settings.connections.slack_to_outgoing")}</Link>
            </p>
          )}
        </div>
        {secret && (
          <button type="button" className="btn-ghost" aria-expanded={show} aria-controls={panel} onClick={() => setOpen(!open)}>
            {t(down ? "settings.connections.reconnect" : set ? "settings.connections.change" : "settings.connections.connect")}
          </button>
        )}
      </div>
      {secret && show && (
        <div id={panel} className="conn-body">
          {down && (
            <div className="conn-down" role="alert">
              <span className="strong">{t("settings.connections.slack_refused")}</span> {down.detail}
            </div>
          )}
          <Steps guide={guide} />
          <TokenBox guide={guide} secret={secret} reload={reload} />
          {secret === "slack_bot_token" && <SlackTest mode={slackMode} reload={reload} />}
          {set && (
            <ActionButton className="quiet" confirm={t("settings.connections.remove_confirm")} run={async () => { await api(`/settings/secrets/${secret}`, { method: "DELETE" }); reload(); }}>
              {t("settings.connections.remove")}
            </ActionButton>
          )}
        </div>
      )}
    </li>
  );
}

export function Connections({ secrets, reload, slackMode }: { secrets: Record<string, SecretStatus>; reload: () => void; slackMode: Mode }) {
  // Slack is the company's connection: its alert is company-wide
  const { data: alerts } = useCompanyLive<Alert[]>("/alerts", ["jobs", "activity_events"]);
  const down = alerts?.find((a) => a.kind === "slack_disconnected");
  return (
    <Section
      id="set-connections"
      title={t("settings.section.connections")}
      lead={t("settings.connections.lead")}
    >
      <ul className="conns plain">
        {GUIDES.map((g) => <Row key={g.name} guide={g} status={g.secret ? secrets[g.secret] : undefined} down={g.secret === "slack_bot_token" ? down : undefined} reload={reload} slackMode={slackMode} />)}
      </ul>
    </Section>
  );
}
