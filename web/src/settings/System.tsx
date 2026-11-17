import { useState } from "react";
import { api, useLive } from "../api";
import { Fix } from "../components/fix";
import { Chip, Tone } from "../components/kit";
import { Loading } from "../components/ui";
import { t } from "../i18n";
import { Section } from "./parts";

type Check = { id: string; title: string; state: "ok" | "problem" | "skipped"; problem: string | null; fix: string | null; note: string | null };
type SystemData = { ok: boolean; checks: Check[] };

const TONE: Record<Check["state"], Tone> = { ok: "ok", problem: "bad", skipped: "muted" };

/** Whether this PC has what the company needs: Docker, the sandbox, the Claude login, the database and a worker. */
export function System() {
  const { data, error, reload } = useLive<SystemData>("/system", []);
  const [fresh, setFresh] = useState<SystemData | null>(null);
  const [busy, setBusy] = useState(false);
  const shown = fresh ?? data;
  async function recheck() {
    setBusy(true);
    try {
      setFresh(await api<SystemData>("/system?fresh=true"));
    } catch {
      reload();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Section id="set-system" title={t("settings.system.title")} lead={t("settings.system.lead")}>
      {!shown ? <Loading error={error} retry={reload} /> : (
        <>
          <ul className="sys-list">
            {shown.checks.map((c) => (
              <li key={c.id} className={`sys-row t-${TONE[c.state]}`}>
                <div className="sys-name">
                  <b>{c.title}</b>
                  <Chip tone={TONE[c.state]}>{c.id === "worker" && c.state === "ok" ? t("settings.system.worker_ok") : t(`settings.system.${c.state}`)}</Chip>
                </div>
                {c.note && <p className="sys-why muted">{c.note}</p>}
                {c.problem && (
                  <p className="sys-why">{c.problem} <span className="sys-fix">{t("settings.system.fix")} {c.fix && <Fix text={c.fix} />}</span></p>
                )}
              </li>
            ))}
          </ul>
          <div className="row">
            <button type="button" disabled={busy} onClick={recheck}>{busy ? t("settings.system.checking") : t("settings.system.recheck")}</button>
          </div>
        </>
      )}
    </Section>
  );
}
