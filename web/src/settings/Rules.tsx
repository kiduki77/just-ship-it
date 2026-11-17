import { useState } from "react";
import { PlainKey, lookup, t } from "../i18n";
import { useDirtyWhen } from "./dirty";
import { Fields, SaveBar, Section } from "./parts";
import { CONNECTOR_OF_ACTION, POLICY_ORDER, PolicyMode, SettingsData } from "./types";
import { Saver, useGuard, useSave } from "./useSave";

const GROUPS: [PlainKey, string[]][] = [
  ["settings.rules.group.outward", ["external.email_send", "external.social_post", "external.survey_distribute", "external.calendar_create",
    "external.interview_outreach", "external.landing_publish", "external.official_inquiry", "external.legal_consult", "external.pii_share",
    "external.other", "crm.contact_upsert"]],
  ["settings.rules.group.money", ["budget.raise", "budget.spend", "data.purchase", "external.ads_run", "external.contract_sign"]],
  ["settings.rules.group.code", ["dev.spec_approve", "dev.implementation_start", "dev.pull_request_create", "dev.merge", "dev.deploy_production"]],
  ["settings.rules.group.inside", ["research.web_search", "research.web_fetch", "internal.report_draft", "internal.task_create", "internal.meeting_call",
    "internal.other", "owner.check", "skill.create_candidate", "skill.activate_knowledge", "skill.activate_workflow", "skill.activate_executable",
    "project.direction_change", "project.new_business", "project.reassess", "bet.routine", "crm.interaction_log"]],
];

const MODE_TEXT: Record<PolicyMode, PlainKey> = {
  auto: "settings.rules.mode.auto", approve: "settings.rules.mode.approve",
  strong_approve: "settings.rules.mode.strong_approve", deny: "settings.rules.mode.deny",
};

const nameOf = (kind: string) => lookup(`settings.rules.kind.${kind}`) ?? kind;

function Rule({ kind, base, floor, value, onChange, live }: {
  kind: string; base: PolicyMode; floor: PolicyMode; value: PolicyMode; onChange: (m: PolicyMode) => void;
  /** Its connector really sends now, so Automatic means it leaves without the owner. */
  live: boolean;
}) {
  const options = POLICY_ORDER.slice(POLICY_ORDER.indexOf(floor));
  const fixed = floor !== "auto";
  const outward = kind.startsWith("external.") || kind === "crm.contact_upsert";
  const id = `rule-${kind}`;
  return (
    <li className="rule">
      <div className="rule-main">
        <label htmlFor={id} className="strong">{nameOf(kind)}</label>
        <p className="small muted">
          {t("settings.rules.default", { mode: t(MODE_TEXT[base]) })}{value !== base && t("settings.rules.changed")}
          {fixed && t("settings.rules.fixed")}
        </p>
        {outward && value === "auto" && <p className="small warn-t">{t(live ? "settings.rules.outward_live" : "settings.rules.outward")}</p>}
      </div>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as PolicyMode)}>
        {options.map((m) => <option key={m} value={m}>{t(MODE_TEXT[m])}</option>)}
      </select>
    </li>
  );
}

function RulesForm({ data, saver }: { data: SettingsData; saver: Saver }) {
  const [modes, setModes] = useState<Record<string, PolicyMode>>(data.policy_modes);
  const listed = new Set(GROUPS.flatMap(([, kinds]) => kinds));
  const other: [PlainKey, string[]] = ["settings.rules.group.other", Object.keys(data.tool_policy).filter((k) => !listed.has(k))];
  const groups = [...GROUPS, other]
    .map(([title, kinds]) => [title, kinds.filter((k) => k in data.tool_policy)] as [PlainKey, string[]])
    .filter(([, kinds]) => kinds.length > 0);
  const differs = (k: string) => modes[k] !== data.tool_policy[k];
  const edited = Object.keys(modes).filter((k) => modes[k] !== data.policy_modes[k]);
  useDirtyWhen(edited.length > 0);
  const guard = useGuard(saver);
  const isLive = (k: string) => !!CONNECTOR_OF_ACTION[k] && data.effective.connectors[CONNECTOR_OF_ACTION[k]]?.mode === "live";
  // an outward rule turned to Automatic is the change that can send without approval: the server's dry run says if it does
  const opensUp = edited.some((k) => modes[k] === "auto" && (k.startsWith("external.") || k === "crm.contact_upsert"));
  const body = { overrides: Object.fromEntries(Object.keys(modes).filter(differs).map((k) => [k, modes[k]])) };
  const summary = edited.length > 0 && t("settings.rules.will_change", {
    changes: edited.map((k) => `${nameOf(k)} ${t(MODE_TEXT[data.policy_modes[k]])} → ${t(MODE_TEXT[modes[k]])}`).join(" · "),
  });
  return (
    <Fields saver={saver} label={t("settings.rules.form")} onSubmit={() => guard.run(body, { check: opensUp })}>
      {groups.map(([title, kinds], i) => (
        <details key={title} className="rules" open={i === 0 || kinds.some(differs)}>
          <summary>
            <span className="strong">{t(title)}</span>{" "}
            <span className="muted">{t("settings.rules.count", { n: kinds.length })}{kinds.some(differs) && t("settings.rules.some_changed")}</span>
          </summary>
          <ul className="plain">
            {kinds.map((k) => (
              <Rule key={k} kind={k} base={data.tool_policy[k]} floor={data.policy_floors[k]} value={modes[k]} live={isLive(k)}
                onChange={(m) => setModes({ ...modes, [k]: m })} />
            ))}
          </ul>
        </details>
      ))}
      <SaveBar saver={saver} guard={guard} disabled={!edited.length} summary={summary} confirmLabel={t("settings.confirm.rules_go")}
        changed={Object.keys(data.effective.policy.overrides).length > 0} resetSays={t("settings.rules.reset_says")} />
    </Fields>
  );
}

export function Rules({ data, reload }: { data: SettingsData; reload: () => void }) {
  const saver = useSave("policy", reload);
  const fixed = Object.keys(data.tool_policy).filter((k) => data.policy_floors[k] !== "auto").map(nameOf);
  return (
    <Section id="set-rules" title={t("settings.section.policy")} lead={t("settings.rules.lead", { fixed: fixed.join(", ") })}>
      <RulesForm key={JSON.stringify([data.policy_modes, data.effective.connectors])} data={data} saver={saver} />
    </Section>
  );
}
