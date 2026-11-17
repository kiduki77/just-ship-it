import { useEffect, useState } from "react";
import { PlainKey, t } from "../i18n";
import { ThemePref, useTheme } from "../theme";
import { Section } from "./parts";

const CHOICES: [ThemePref, PlainKey][] = [
  ["light", "settings.screen.light"], ["dark", "settings.screen.dark"], ["system", "settings.screen.system"],
];

function useOsDark(): boolean {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setDark(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return dark;
}

/** Light or dark is a per-browser choice: it applies at once and never goes to the server. */
export function Screen() {
  const [pref, setPref] = useTheme();
  const osDark = useOsDark();
  const says: Record<ThemePref, PlainKey> = {
    light: "settings.screen.says_light",
    dark: "settings.screen.says_dark",
    system: osDark ? "settings.screen.says_system_dark" : "settings.screen.says_system_light",
  };
  return (
    <Section id="set-screen" title={t("settings.section.screen")} lead={t("settings.screen.lead")}>
      <fieldset className="out">
        <legend className="sr-only">{t("settings.screen.group")}</legend>
        <div className="set-seg" role="radiogroup" aria-label={t("settings.screen.group")}>
          {CHOICES.map(([k, key]) => (
            <label key={k} className={pref === k ? "on" : ""}>
              <input type="radio" name="theme" value={k} checked={pref === k} onChange={() => setPref(k)} />
              {t(key)}
            </label>
          ))}
        </div>
        <p className="small muted" role="status">{t(says[pref])}</p>
      </fieldset>
    </Section>
  );
}
