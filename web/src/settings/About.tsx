import { useState } from "react";
import { Link } from "react-router-dom";
import { Strengths, StrengthsPicker } from "../components/strengths";
import { t } from "../i18n";
import { useDirtyWhen } from "./dirty";
import { Fields, SaveBar, Section } from "./parts";
import { SettingsData } from "./types";
import { Saver, useSave } from "./useSave";

function AboutForm({ data, saver }: { data: SettingsData; saver: Saver }) {
  const saved = data.effective.owner.strengths;
  const [value, setValue] = useState<Strengths>(saved);
  const differs = JSON.stringify(value) !== JSON.stringify(saved);
  useDirtyWhen(differs);
  return (
    <Fields saver={saver} label={t("settings.about.group")} onSubmit={() => saver.save({ strengths: value })}>
      <StrengthsPicker value={value} onChange={setValue} disabled={saver.busy} legend={t("settings.about.group")} />
      {saved === null && <p className="small muted">{t("settings.about.unanswered")}</p>}
      <p className="small muted">{t("settings.about.time")} <Link to="/business">{t("settings.about.time_link")}</Link></p>
      {/* No way back to "not answered": the file has no default for the owner, and clearing every pick is that. */}
      <SaveBar saver={saver} changed={false} disabled={!differs} />
    </Fields>
  );
}

/** What the owner is good at. The company asks deeper and hands over the work there, and explains plainly elsewhere. */
export function About({ data, reload }: { data: SettingsData; reload: () => void }) {
  const saver = useSave("owner", reload);
  return (
    <Section id="set-about" title={t("settings.section.about")} lead={t("settings.about.lead")}>
      <AboutForm key={JSON.stringify(data.effective.owner.strengths)} data={data} saver={saver} />
    </Section>
  );
}
