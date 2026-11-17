import { useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Row, useLive } from "../api";
import { PlainKey, t } from "../i18n";
import { label } from "../labels";
import { evidenceHref, KindTag, Verification, verifyTone } from "./ui";

/*
 * How well is a conclusion supported? Shown next to the conclusion itself: a two-row confidence bar (what kind of
 * evidence, how far it was checked) and chips that open the claim in place.
 */

/** Checks the server runs itself (a fetch, a computation): no source URL is needed to say what the claim rests on. */
export const SERVER_CHECKS = new Set(["computed", "reproduced", "derived", "transcribed"]);
/** The record of a check the server ran itself: its note names the file or the run. */
export const serverRecord = (e: Row) => SERVER_CHECKS.has(e.verification_status) && !!e.verification_note;
/** A server check with no record kept: nothing on screen can show it happened, so it isn't drawn as checked. */
const unrecordedCheck = (e: Row) => SERVER_CHECKS.has(e.verification_status) && !e.verification_note;
/** A claim labelled a verified fact with neither a source nor the record of a check: the list can't back its label. */
export const unbacked = (e: Row) => e.kind === "verified_fact" && !e.source_url && !serverRecord(e);
/** The kind a row reads as: its stored kind, or "unconfirmed" when nothing on record backs a verified fact. */
export const shownKind = (e: Row) => (unbacked(e) ? "unconfirmed" : e.kind);
/** How far a row's check got, as a colour: the status's own tone, or muted when that check left no record. */
export const checkTone = (e: Row) => (unrecordedCheck(e) ? "muted" : verifyTone(e.verification_status));
/** A source's name: its title, else its address without the scheme, so a link never reads as a bare "Source". */
export const sourceName = (e: Row) => e.source_title || String(e.source_url ?? "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");

type Group = [PlainKey, string, string[]];
const KIND_GROUPS: Group[] = [
  ["decide.ev.kind.verified", "var(--ok)", ["verified_fact"]],
  ["decide.ev.kind.sourced", "var(--run)", ["sourced_claim", "calculation"]],
  ["decide.ev.kind.guess", "var(--warn)", ["assumption", "hypothesis", "unconfirmed"]],
];
const VERIFY_GROUPS: Group[] = [
  ["decide.ev.check.supported", "var(--ok)", ["supported", "reproduced", "quote_found", "computed", "deterministic"]],
  ["decide.ev.check.sourced", "var(--run)", ["url_ok", "derived", "transcribed"]],
  ["decide.ev.check.weak", "var(--warn)", ["weak", "unverifiable"]],
  ["decide.ev.check.unchecked", "var(--line-strong)", ["unchecked"]],
  ["decide.ev.check.contradicted", "var(--bad)", ["contradicted", "url_unreachable"]],
];

const idsOf = (ids: (string | number)[]) =>
  [...new Set(ids.map((x) => Number(String(x).replace(/^EV-/i, ""))).filter((n) => Number.isFinite(n) && n > 0))];

export function useEvidence(ids: (string | number)[]) {
  const wanted = idsOf(ids);
  const key = wanted.join(",");
  return useLive<Row[]>(key ? `/evidence?ids=${key}` : null, []);
}

function Stacked({ rows, groups, value, title }: { rows: Row[]; groups: Group[]; value: (r: Row) => string; title: string }) {
  const counts = groups.map(([name, color, keys]) => ({ name: t(name), color, n: rows.filter((r) => keys.includes(value(r))).length }));
  const total = rows.length;
  const text = counts.filter((c) => c.n).map((c) => t("decide.ev.group", { name: c.name, n: c.n })).join(", ");
  return (
    <div className="cbar-row">
      <span className="cbar-l">{title}</span>
      <div className="cbar" role="img" aria-label={`${title}: ${text}`}>
        {counts.filter((c) => c.n).map((c) => (
          <span key={c.name} style={{ flexGrow: c.n, background: c.color }} title={`${c.name} ${c.n}/${total}`} />
        ))}
      </div>
      <span className="cbar-t small">{text}</span>
    </div>
  );
}

export function ConfidenceBar({ rows }: { rows: Row[] }) {
  if (!rows.length) return null;
  return (
    <div className="cbars">
      <Stacked rows={rows} groups={KIND_GROUPS} value={shownKind} title={t("decide.ev.kind")} />
      <Stacked rows={rows} groups={VERIFY_GROUPS} value={(r) => (unrecordedCheck(r) ? "unchecked" : r.verification_status)}
        title={t("decide.ev.check")} />
    </div>
  );
}

/** Cited evidence for one conclusion: the bar, then chips; a chip opens the claim right below. */
export function EvidenceSummary({ ids, projectId, title: given, note }: {
  ids: (string | number)[]; projectId: number | null | undefined; title?: string; note?: string;
}) {
  const title = given ?? t("decide.ev.cited");
  const { pathname, search } = useLocation();
  const { data, error } = useEvidence(ids);
  const [open, setOpen] = useState<number | null>(null);
  const rows = useMemo(() => data ?? [], [data]);
  const wanted = idsOf(ids);
  // no evidence is a confidence too, the weakest one: it reads as a warning, like a mostly-guessed set below
  if (!wanted.length) return <p className="small evnone">{t("decide.ev.none", { title })}{note && ` ${note}`}</p>;
  if (error) return <p className="small error">{t("decide.ev.load_failed", { error })}</p>;
  if (!data) return <p className="small muted">{t("decide.ev.loading", { n: wanted.length })}</p>;
  const cur = rows.find((r) => r.id === open);
  const weakShare = rows.filter((r) => ["assumption", "hypothesis"].includes(r.kind)).length / Math.max(1, rows.length);
  return (
    <section className="evsum" aria-label={title}>
      <div className="row between wrap">
        <b className="small">{t("decide.ev.title_count", { title, n: rows.length })}</b>
        {weakShare >= 0.5 && <span className="small warn-t">{t("decide.ev.mostly_guesses")}</span>}
      </div>
      {note && <p className="small muted evsum-note">{note}</p>}
      <ConfidenceBar rows={rows} />
      {/* the bar is the summary; the claims one by one are for checking, so they wait behind a count */}
      <details className="evchips-fold">
        <summary className="small">{t("decide.ev.one_by_one", { n: rows.length })}</summary>
        <div className="evchips">
          {rows.map((r) => (
            <button key={r.id} className={`evchip k-${r.kind} ${open === r.id ? "on" : ""}`} aria-expanded={open === r.id}
              onClick={() => setOpen(open === r.id ? null : r.id)} title={`${r.claim}\n${label(r.kind)} · ${label(r.verification_status)}`}>
              {r.claim}
            </button>
          ))}
        </div>
      </details>
      {cur && (
        <div className="evpop" role="region" aria-label={t("decide.ev.claim_detail")}>
          <div className="row wrap tight"><KindTag kind={shownKind(cur)} /> <Verification value={cur.verification_status} tone={checkTone(cur)} /></div>
          <p className="evpop-c">{cur.claim}</p>
          {cur.quote && <div className="quote">“{cur.quote}”</div>}
          {cur.verification_note && <div className="small muted pre-wrap">{cur.verification_note}</div>}
          <div className="row wrap small">
            {cur.source_url && /^https?:/.test(cur.source_url) && (
              <a href={cur.source_url} target="_blank" rel="noreferrer">{t("decide.ev.external", { source: sourceName(cur) })}</a>
            )}
            {projectId && <Link to={evidenceHref(cur.id, projectId, pathname + search)!}>{t("decide.ev.in_list")}</Link>}
          </div>
        </div>
      )}
    </section>
  );
}
