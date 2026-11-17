import { Link } from "react-router-dom";
import { Row } from "../api";
import { usd } from "../components/ui";
import { lookup, t, tx } from "../i18n";
import { SettingsData } from "./types";

/** What the server files fix and no screen changes: the company, its limits, the model CLIs and the agents. */
export function ServerValues({ data }: { data: SettingsData }) {
  const limits = Object.entries(data.limits as Record<string, number>);
  return (
    <details className="set-sec box set-server" id="set-server">
      <summary><span className="set-h">{t("settings.server.title")}</span> <span className="muted">{t("settings.server.read_only")}</span></summary>
      <p className="set-lead">{tx("settings.server.lead", { dir: <span className="mono">{data.config_dir}</span> })}</p>
      <dl className="facts">
        {data.month && (
          <div><dt>{t("settings.server.month")}</dt><dd>{usd(data.month.limit_usd)} · <Link to="/audit/costs">{t("settings.server.month_change")}</Link></dd></div>
        )}
        <div><dt>{t("settings.server.company")}</dt><dd>{data.company.name}</dd></div>
        <div><dt>{t("settings.server.mission")}</dt><dd>{data.company.mission}</dd></div>
      </dl>
      <details className="set-dev">
        <summary>{t("settings.server.dev")}</summary>
        <dl className="facts">
        {limits.map(([k, v]) => (
          <div key={k}><dt>{lookup(`settings.server.limit.${k}`) ?? k}</dt><dd>{k.endsWith("_usd") ? usd(v) : v}</dd></div>
        ))}
        <div>
          <dt>{t("settings.server.isolation")}</dt>
          <dd>{t(data.engineering.isolation === "container" ? "settings.server.isolation.container" : "settings.server.isolation.host")}</dd>
        </div>
        <div><dt>{t("settings.server.fix_rounds")}</dt><dd>{t("settings.server.fix_rounds.value", { n: data.engineering.max_fix_iterations })}</dd></div>
        {Object.entries(data.providers as Record<string, Row>).map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{t("settings.server.provider", { n: v.max_concurrency, model: v.default_model ?? t("settings.server.default_model") })}</dd></div>
        ))}
        </dl>
      </details>
      <p className="small muted">{tx("settings.server.note", { agents: <Link to="/agents">{t("settings.server.agents")}</Link> })}</p>
    </details>
  );
}
