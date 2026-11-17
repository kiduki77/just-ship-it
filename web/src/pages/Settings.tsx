import { ReactNode, useEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { useLive } from "../api";
import { Banner, PageHead, Status } from "../components/kit";
import { Loading } from "../components/ui";
import { PlainKey, t } from "../i18n";
import { Bets } from "../settings/Bets";
import { Developer } from "../settings/Developer";
import { DirtyProvider, useDirty } from "../settings/dirty";
import { Language } from "../settings/Language";
import { About } from "../settings/About";
import { Connections } from "../settings/Connections";
import { Notifications } from "../settings/Notifications";
import { Outgoing } from "../settings/Outgoing";
import { Rules } from "../settings/Rules";
import { sendingOf } from "../settings/sending";
import { Screen } from "../settings/Screen";
import { ServerValues } from "../settings/ServerValues";
import { System } from "../settings/System";
import { SettingsData } from "../settings/types";
import { Usage } from "../settings/Usage";
import "../settings/settings.css";

const SECTION_NAMES: Record<string, PlainKey> = {
  general: "settings.section.language", owner: "settings.section.about", usage: "settings.section.usage", connectors: "settings.section.connectors",
  policy: "settings.section.policy", notifications: "settings.section.notifications", bets: "settings.section.bets",
};

/**
 * The tabs and the sections each one holds, in the order they show. The small sections share General; a tab's body below
 * must render the same sections, so the dirty mark and a `#set-…` link find the right tab.
 */
const TABS: { id: string; name: PlainKey; risk: PlainKey; sections: string[] }[] = [
  { id: "general", name: "settings.tab.general", risk: "settings.risk.general", sections: ["set-system", "set-language", "set-screen", "set-about", "set-server", "set-dev"] },
  { id: "usage", name: "settings.section.usage", risk: "settings.risk.usage", sections: ["set-usage"] },
  // Slack in the order it is set up: the token, where notices go, then whether they really send
  { id: "connections", name: "settings.tab.connections", risk: "settings.risk.connections", sections: ["set-connections", "set-notify", "set-outgoing"] },
  { id: "rules", name: "settings.tab.rules", risk: "settings.risk.rules", sections: ["set-rules", "set-bets"] },
];

/** The section a problem's server section is shown in. */
const PROBLEM_SECTION: Record<string, string> = {
  general: "set-language", owner: "set-about", notifications: "set-notify", usage: "set-usage", connectors: "set-outgoing", policy: "set-rules",
  bets: "set-bets",
};

/** The one thing to know before touching anything here: does anything leave the company for real right now. */
function sendingStatus(data: SettingsData | null): Status | null {
  if (!data) return null;
  const { sentence } = sendingOf(data.sending);
  return sentence ? { tone: "warn", text: sentence } : { tone: "ok", text: t("settings.status.none_live") };
}

function Problems({ items }: { items: SettingsData["problems"] }) {
  if (!items.length) return null;
  return (
    <Banner tone="warn">
      <b>{t("settings.problems.title", { n: items.length })}</b>{t("settings.problems.body")}
      <ul className="set-problems">
        {items.map((p, i) => {
          const name = SECTION_NAMES[p.section] ? t(SECTION_NAMES[p.section]) : p.section;
          const to = PROBLEM_SECTION[p.section];
          return (
            <li key={i}>
              {to ? <Link to={{ search: location.search, hash: to }} replace className="strong">{name}</Link> : <span className="strong">{name}</span>} · {p.message}
            </li>
          );
        })}
      </ul>
    </Banner>
  );
}

/**
 * The open tab comes from the hash: a tab id (`#usage`) or, for links from elsewhere, a section id (`#set-usage`), which
 * also scrolls to that section. No hash opens General.
 */
function useTab(ready: boolean): string {
  const { hash } = useLocation();
  const id = decodeURIComponent(hash.slice(1));
  const tab = (TABS.find((tb) => tb.id === id || tb.sections.includes(id)) ?? TABS[0]).id;
  const first = useRef(true);
  useEffect(() => {
    const isSection = TABS.some((tb) => tb.sections.includes(id));
    if (isSection) {
      // a link to the folded server values opens them
      const el = document.getElementById(id);
      if (el instanceof HTMLDetailsElement) el.open = true;
      // focus follows the link to the section (its heading, or the fold's summary), so keyboard and screen reader land there
      const f = requestAnimationFrame(() => {
        const sec = document.getElementById(id);
        sec?.scrollIntoView({ block: "start" });
        const to = sec instanceof HTMLDetailsElement ? sec.querySelector("summary") : document.getElementById(`${id}-h`);
        to?.focus({ preventScroll: true });
      });
      return () => cancelAnimationFrame(f);
    }
    // A new tab starts at its top, not wherever the last one was scrolled to.
    if (!first.current) window.scrollTo({ top: 0 });
    first.current = false;
  }, [id, ready]);
  return tab;
}

/** The rules and keys the owner sets for the company. The files' values are the defaults; what is saved here wins. */
export default function Settings() {
  return (
    <DirtyProvider>
      <SettingsScreen />
    </DirtyProvider>
  );
}

function SettingsScreen() {
  const { data, error, reload } = useLive<SettingsData>("/settings", []);
  const cur = useTab(!!data);
  const { sections } = useDirty();
  const { search } = useLocation();
  const wait = <Loading error={error} retry={reload} />;
  const tabBody = (id: string, children: ReactNode) => {
    const tab = TABS.find((tb) => tb.id === id)!;
    return (
      <div className="set-body" id={`set-tab-${id}`} tabIndex={-1} hidden={cur !== id}>
        <p className="set-risk">{t(tab.risk)}</p>
        {children}
      </div>
    );
  };
  return (
    <div className="owner-page set kui">
      <PageHead title={t("settings.title")} status={sendingStatus(data)} sub={t("settings.sub")} />
      {data && <Problems items={data.problems} />}
      {sections.size > 0 && (
        <Banner tone="warn">
          <b>{t("settings.dirty.title")}</b>{t("settings.dirty.body", { sections: [...sections.values()].join(", ") })}
        </Banner>
      )}
      <div className="set-layout">
        <a className="skip" href={`#set-tab-${cur}`}>{t("settings.skip_to_body")}</a>
        <nav aria-label={t("settings.index")} className="set-index">
          <ul>
            {TABS.map((tb) => (
              <li key={tb.id}>
                <Link to={{ search, hash: tb.id }} replace className={cur === tb.id ? "on" : ""} aria-current={cur === tb.id ? "page" : undefined}>
                  {t(tb.name)}
                  {tb.sections.some((s) => sections.has(s)) && (
                    <span className="set-dirty" role="img" aria-label={t("settings.dirty.mark")} title={t("settings.dirty.mark")} />
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        {/* Every tab stays mounted and only the open one shows, so edits not yet saved survive a tab switch. */}
        {tabBody("general", <>
          <System />
          <Language />
          <Screen />
          {!data ? wait : (
            <>
              <About data={data} reload={reload} />
              <ServerValues data={data} />
            </>
          )}
          <Developer />
        </>)}
        {tabBody("usage", !data ? wait : <Usage data={data} reload={reload} />)}
        {tabBody("connections", !data ? wait : (
          <>
            <Connections secrets={data.secrets} reload={reload} slackMode={data.effective.connectors.slack.mode} />
            <Notifications data={data} reload={reload} />
            <Outgoing data={data} reload={reload} />
          </>
        ))}
        {tabBody("rules", !data ? wait : (
          <>
            <Rules data={data} reload={reload} />
            <Bets data={data} reload={reload} />
          </>
        ))}
      </div>
    </div>
  );
}
