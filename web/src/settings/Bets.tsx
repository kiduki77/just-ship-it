import { useState } from "react";
import { t } from "../i18n";
import { useDirtyWhen } from "./dirty";
import { FieldError, Fields, SaveBar, Section } from "./parts";
import { BetValues, SettingsData } from "./types";
import { Saver, useSave } from "./useSave";

type Unit = "count" | "minutes" | "days";

interface Spec {
  key: keyof BetValues;
  unit: Unit;
  min: number;
  max: number;
}

const SPECS: Spec[] = [
  { key: "max_running", unit: "count", min: 1, max: 3 },
  { key: "weekly_post_cap", unit: "count", min: 0, max: 7 },
  { key: "max_routines", unit: "count", min: 0, max: 10 },
  { key: "failed_grace_minutes", unit: "minutes", min: 0, max: 1440 },
  { key: "window_days", unit: "days", min: 1, max: 60 },
];

const RANGE: Record<Unit, "settings.bets.range.count" | "settings.bets.range.minutes" | "settings.bets.range.days"> = {
  count: "settings.bets.range.count", minutes: "settings.bets.range.minutes", days: "settings.bets.range.days",
};

function BetsForm({ data, saver }: { data: SettingsData; saver: Saver }) {
  const bets = data.effective.bets;
  const [text, setText] = useState<Record<string, string>>(() => Object.fromEntries(SPECS.map((s) => [s.key, String(bets[s.key])])));
  const value = (s: Spec) => (/^\d+$/.test(text[s.key]) ? Number(text[s.key]) : NaN);
  const bad = (s: Spec) => !(value(s) >= s.min && value(s) <= s.max);
  useDirtyWhen(SPECS.some((s) => text[s.key] !== String(bets[s.key])));
  const save = () => saver.save(Object.fromEntries(SPECS.map((s) => [s.key, value(s)])));
  return (
    <Fields saver={saver} label={t("settings.bets.form")} onSubmit={save}>
      {SPECS.map((s) => (
        <div className="field set-narrow" key={s.key}>
          <label htmlFor={`bet-${s.key}`}>{t(`settings.bets.${s.key}` as const)}</label>
          <div className="set-unit">
            <input id={`bet-${s.key}`} inputMode="numeric" value={text[s.key]} aria-invalid={bad(s)} aria-describedby={`bet-${s.key}-e bet-${s.key}-h`}
              onChange={(e) => setText({ ...text, [s.key]: e.target.value })} />
            <span className="muted">{t(RANGE[s.unit], { min: s.min, max: s.max })}</span>
          </div>
          <FieldError id={`bet-${s.key}-e`} text={bad(s) ? t("settings.field_range", { min: s.min, max: s.max }) : null} />
          <p id={`bet-${s.key}-h`} className="small muted set-help">{t(`settings.bets.${s.key}.help` as const)}</p>
        </div>
      ))}
      <SaveBar saver={saver} invalid={SPECS.filter(bad).length} changed={Object.keys(data.overrides.bets ?? {}).length > 0} />
    </Fields>
  );
}

export function Bets({ data, reload }: { data: SettingsData; reload: () => void }) {
  const saver = useSave("bets", reload);
  return (
    <Section id="set-bets" title={t("settings.section.bets")} lead={t("settings.bets.lead")}>
      <BetsForm key={JSON.stringify(data.effective.bets)} data={data} saver={saver} />
    </Section>
  );
}
