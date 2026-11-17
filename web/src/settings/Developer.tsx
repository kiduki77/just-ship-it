import { t } from "../i18n";
import { useShowTest } from "../components/ui";
import { Section } from "./parts";

/** Switches for whoever builds or tests the console, kept apart from the owner's own choices. Per browser. */
export function Developer() {
  const [showTest, setShowTest] = useShowTest();
  return (
    <Section id="set-dev" title={t("settings.section.dev")} lead={t("settings.dev.lead")}>
      <label className="check-l set-check">
        <input type="checkbox" checked={showTest} onChange={(e) => setShowTest(e.target.checked)} aria-describedby="set-dev-test" /> {t("shell.nav.show_test")}
      </label>
      <p id="set-dev-test" className="small muted">{t("settings.screen.test_says")}</p>
    </Section>
  );
}
