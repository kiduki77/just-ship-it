import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { useLive } from "../api";
import { Chip, Section } from "../components/kit";
import { ExtLink } from "../components/panel";
import { RULES_ANCHOR, dayOf } from "../components/rules";
import { Skeleton, landOn } from "../components/ui";
import { locale, t } from "../i18n";
import type { ChannelPlace, ChannelPlaces } from "./types";
import "./channel-rules.css";

/** A change to the business, its dropped places or the launch checklist can move a row. */
const TABLES = ["businesses", "channel_drops", "owner_tasks"];

const joined = (items: string[]) => {
  try {
    return new Intl.ListFormat(locale(), { style: "long", type: "conjunction" }).format(items);
  } catch {
    return items.join(", ");
  }
};

/** A link that leaves the console: its name carries the place and says it opens a new tab. */
function Out({ href, label, place }: { href: string; label: string; place: string }) {
  return <ExtLink href={href}>{label}<span className="sr-only">{t("business-rules.link_sr", { place })}</span></ExtLink>;
}

/** Quiet facts about a place for this business, one line each, and only when they hold. */
function Facts({ p }: { p: ChannelPlace }) {
  const needs = p.requires_unmet.map((r) => r.label);
  if (p.fits && !p.dropped && !needs.length) return null;
  return (
    <ul className="cr-facts">
      {!p.fits && <li>{t("business-rules.no_fit")}</li>}
      {p.dropped && <li>{t("business-rules.dropped")}</li>}
      {needs.length > 0 && <li>{t("business-rules.needs", { items: joined(needs) })}</li>}
    </ul>
  );
}

function Place({ p }: { p: ChannelPlace }) {
  const checked = p.state === "checked";
  return (
    <li className="cr-place">
      <p className="cr-head">
        <span className="cr-name">{p.name}</span>
        {checked
          ? <Chip tone="ok">{p.checked_on ? t("business-rules.checked_dated", { date: dayOf(p.checked_on) }) : t("term.rules_checked")}</Chip>
          : <Chip tone="warn">{t("term.rules_unchecked")}</Chip>}
      </p>
      <div className="cr-body">
        <p className="cr-line">
          {checked
            ? (p.does !== "nothing" && <span>{t(p.does === "points" ? "business-rules.does.points" : "business-rules.does.draft")}</span>)
            : (p.unchecked_text && <span>{p.unchecked_text}</span>)}
          {checked
            ? (p.source_url && <Out href={p.source_url} label={t("business-rules.source")} place={p.name} />)
            : (p.background_url && <Out href={p.background_url} label={t("business-rules.background")} place={p.name} />)}
        </p>
        <Facts p={p} />
        {p.rules && (
        <details className="cr-fold">
          <summary>{t(checked ? "business-rules.rules_text" : "business-rules.notes")}<span className="sr-only"> ({p.name})</span></summary>
          <p>{p.rules}</p>
        </details>
        )}
      </div>
    </li>
  );
}

function Group({ id, title, count, lead, places }: { id: string; title: string; count: number; lead?: string; places: ChannelPlace[] }) {
  return (
    <section className="cr-group" aria-labelledby={id}>
      <h3 id={id}>{title}<span className="sec-n">{count}</span></h3>
      {lead && <p className="cr-lead">{lead}</p>}
      <ul className="cr-list">{places.map((p) => <Place key={p.key} p={p} />)}</ul>
    </section>
  );
}

/**
 * "Places and rules" on the Numbers tab, next to "Where people came from": every place for the business's market with
 * whether its rules were read at the place itself. Checked places come first; the unchecked ones sit below with the one
 * sentence that says the company writes nothing for them. A post card links here by `#places-rules`.
 */
export function ChannelRules({ businessId }: { businessId: number }) {
  const { data, error, reload } = useLive<ChannelPlaces>(`/businesses/${businessId}/channels`, TABLES);
  const { hash } = useLocation();
  const landed = !!data;

  // the tab is ready before this list is, so a link to it lands once the rows are here
  useEffect(() => {
    if (!landed || hash !== `#${RULES_ANCHOR}`) return;
    const id = requestAnimationFrame(() => landOn(RULES_ANCHOR));
    return () => cancelAnimationFrame(id);
  }, [landed, hash]);

  const title = t("business-rules.title");
  if (!data) {
    return (
      <Section id={RULES_ANCHOR} title={title} className="cr">
        {error ? (
          <p className="quiet-error" role="alert">{t("business-rules.load_failed")} <button type="button" className="link" onClick={reload}>{t("common.retry")}</button></p>
        ) : <Skeleton rows={4} />}
      </Section>
    );
  }

  const places = data.places.filter((p) => !p.generic);
  const checked = places.filter((p) => p.state === "checked");
  const unchecked = places.filter((p) => p.state !== "checked");
  return (
    <Section id={RULES_ANCHOR} title={title} className="cr">
      {error && <p className="quiet-error" role="alert">{t("business-rules.stale_error")} <button type="button" className="link" onClick={reload}>{t("common.retry")}</button></p>}
      {places.length === 0 ? (
        <p className="cr-lead">{t("business-rules.none")}</p>
      ) : (
        <>
          <p className="cr-lead">{t("business-rules.lead")}</p>
          {checked.length > 0
            ? <Group id="cr-checked" title={t("term.rules_checked")} count={checked.length} places={checked} />
            : <p className="cr-note" role="note">{t("business-rules.no_checked")}</p>}
          {unchecked.length > 0 && (
            <Group id="cr-unchecked" title={t("term.rules_unchecked")} count={unchecked.length} lead={t("business-rules.unchecked_lead")} places={unchecked} />
          )}
        </>
      )}
    </Section>
  );
}
