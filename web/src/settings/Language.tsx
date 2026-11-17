import { useEffect, useState } from "react";
import { api } from "../api";
import { LANG_NAMES, LANGS, Lang, language, setLanguage, t, useLanguage } from "../i18n";
import { useDirty } from "./dirty";
import { Section } from "./parts";

interface General {
  effective?: { general?: { language?: string } };
}

/**
 * Follow the company's language setting: read it once the owner is logged in. Until it arrives the console shows the
 * language this browser saw last, so a reload does not flash the other language.
 */
export function useServerLanguage(ready: boolean): void {
  useEffect(() => {
    if (!ready) return;
    let live = true;
    api<General>("/settings").then((s) => live && setLanguage(s.effective?.general?.language)).catch(() => {});
    return () => {
      live = false;
    };
  }, [ready]);
}

/**
 * The owner's language, for the whole company: the console's words and everything the company writes from now on.
 * It is saved at once, like the theme, because the choice is the whole change. A switch remounts the console
 * (main.tsx), so it waits while another section holds unsaved edits.
 */
export function Language() {
  const lang = useLanguage();
  const unsaved = [...useDirty().sections.values()];
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choose = async (next: Lang) => {
    if (next === language() || unsaved.length > 0) return;
    setBusy(true);
    setError(null);
    try {
      await api("/settings/general", { method: "PUT", body: JSON.stringify({ language: next }) });
      setLanguage(next);
    } catch (e: any) {
      setError(t("settings.language.failed", { error: e.message ?? String(e) }));
      setBusy(false);
    }
  };
  return (
    <Section id="set-language" title={t("settings.section.language")} lead={t("settings.language.lead")}>
      <fieldset className="out" disabled={busy || unsaved.length > 0}>
        <legend className="sr-only">{t("settings.language.group")}</legend>
        <div className="set-seg" role="radiogroup" aria-label={t("settings.language.group")}>
          {LANGS.map((k) => (
            <label key={k} className={lang === k ? "on" : ""} lang={k}>
              <input type="radio" name="language" value={k} checked={lang === k} onChange={() => choose(k)} />
              {LANG_NAMES[k]}
            </label>
          ))}
        </div>
        <p className="small muted" role="status">
          {busy ? t("settings.language.saving")
            : unsaved.length > 0 ? t("settings.language.unsaved", { sections: unsaved.join(", ") })
            : t("settings.language.stays")}
        </p>
        {error && <div className="error" role="alert">{error}</div>}
      </fieldset>
    </Section>
  );
}
