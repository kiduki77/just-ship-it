import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { BusinessFiles, citeText } from "../components/attach";
import { Box, Hint, Section } from "../components/kit";
import { PlainKey, t, tx } from "../i18n";
import { ProvBadge } from "../kickoff/Brief";
import { CustomersLink } from "./Customers";
import { dayText } from "./parts";
import { BriefLine, BriefSection, BriefView, Provenance, sourceText } from "./types";

/** Who stands behind a line, in the owner's words. The badge already carries the shape; this says what it means. */
const PROV_MEANS: [Provenance, PlainKey][] = [
  ["said", "business.brief.prov.said"],
  ["confirmed", "business.brief.prov.confirmed"],
  ["guess", "business.brief.prov.guess"],
  ["unknown", "business.brief.prov.unknown"],
];

function Line({ l }: { l: BriefLine }) {
  const src = l.doc ? citeText(l.doc) : sourceText(l.source);
  return (
    <li className="bl">
      <ProvBadge provenance={l.provenance} />
      <div className="bl-body">
        <span className="bl-t">{l.text}</span>
        {l.quote && (
          <p className="biz-bq">
            <span className="k">{t(l.provenance === "confirmed" ? "business.brief.picked" : "business.brief.said")}</span> “{l.quote}”{src && <span className="muted"> · {src}</span>}
          </p>
        )}
        {!l.quote && src && l.provenance !== "unknown" && <p className="biz-bsrc">{t("business.brief.from", { src })}</p>}
        {l.note && <p className="biz-bsrc">{l.note}</p>}
      </div>
    </li>
  );
}

function Sec({ s, fallback, ask }: { s: BriefSection; fallback?: string; ask?: Ask }) {
  return (
    <li className="biz-bs" data-section={s.key}>
      <div className="biz-bs-top">
        <h3 className="biz-bs-h">{s.title}</h3>
        {ask && <Link className="small biz-ask" to={ask(s.title)}>{t("business.brief.ask_change")}</Link>}
      </div>
      {s.drives && <p className="biz-bs-d">{s.drives}</p>}
      {!!s.lines.length && <ul className="bs-lines">{s.lines.map((l) => <Line key={l.id} l={l} />)}</ul>}
      {fallback && <p className="biz-default">{fallback}</p>}
      {s.key === "customer_problem" && <p className="biz-bsrc"><CustomersLink>{t("business-profile.from_plan")}</CustomersLink></p>}
    </li>
  );
}

function Group({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <Section title={title}>
      <Box className="biz-brief">
        {note && <p className="biz-note-s biz-brief-note">{note}</p>}
        <ul className="biz-bs-list">{children}</ul>
      </Box>
    </Section>
  );
}

function NoBrief({ data, change }: { data: BriefView; change: string | null }) {
  const body = change
    ? [t("business.brief.none_body"), data.draft_waiting && t("business.brief.none_draft"), t("business.brief.none_then")].filter(Boolean).join(" ")
    : t("business.brief.none_no_kickoff");
  return (
    <Hint title={t("business.brief.none")}
      action={change ? <Link className="btn-primary" to={change}>{t("business.brief.to_kickoff")}</Link> : <Link className="btn-ghost" to="/businesses/new">{t("business.start")}</Link>}>
      {body}
    </Hint>
  );
}

/** A link to the Ideas tab with a change to this section already started; the CEO turns it into a plan change to approve. */
type Ask = (section: string) => string;

/** What the company works from. Required sections first, then the optional ones that hold something, then the old plan's leftovers. */
export function BriefTab({ data, kickoffId, businessId, ask }: { data: BriefView; kickoffId: number | null; businessId: number; ask: Ask }) {
  const brief = data.brief;
  const change = kickoffId ? `/kickoff/${kickoffId}` : null;
  if (!brief) return <div className="biz-measure"><NoBrief data={data} change={change} /></div>;
  const defaults = new Map(brief.empty_defaults.map((d) => [d.key, d.default]));
  const required = brief.sections.filter((s) => s.required);
  const optional = brief.sections.filter((s) => !s.required && !s.legacy);
  const filled = optional.filter((s) => s.lines.length);
  const empty = optional.filter((s) => !s.lines.length);
  const legacy = brief.sections.filter((s) => s.legacy);
  const lines = brief.sections.flatMap((s) => s.lines);
  const count = (p: Provenance) => lines.filter((l) => l.provenance === p).length;
  const requiredFallback = (s: BriefSection) => {
    if (s.state === "settled") return undefined;
    if (s.lines.length) return t("business.brief.unsaid", { default: defaults.get(s.key) ?? "" }).trim();
    return t("business.brief.empty_default", { default: defaults.get(s.key) || s.default || t("business.brief.no_default") });
  };
  return (
    <div className="cols cols-main">
      <div>
        {data.draft_waiting && change && (
          <p className="biz-callout" role="status">{t("business.brief.changed")} <Link to={change}>{t("business.brief.confirm_in_kickoff")}</Link></p>
        )}
        <Group title={t("business.brief.required")}>
          {required.map((s) => <Sec key={s.key} s={s} fallback={requiredFallback(s)} ask={ask} />)}
        </Group>
        {!!filled.length && <Group title={t("business.brief.optional")}>{filled.map((s) => <Sec key={s.key} s={s} ask={ask} />)}</Group>}
        {!!empty.length && (
          <Section title={t("business.brief.empty_sections")} count={empty.length}>
            <Box className="biz-brief">
              <details className="biz-empty">
                <summary>{t("business.brief.empty_what")}</summary>
                <ul className="biz-bs-list">
                  {empty.map((s) => <Sec key={s.key} s={s} ask={ask} fallback={s.default ? t("business.brief.empty_default", { default: s.default }) : t("business.brief.optional_empty", { holds: s.holds })} />)}
                </ul>
              </details>
            </Box>
          </Section>
        )}
        {!!legacy.length && (
          <Group title={t("business.brief.legacy")} note={t("business.brief.legacy_note")}>
            {legacy.map((s) => <Sec key={s.key} s={s} />)}
          </Group>
        )}
      </div>
      <div>
        <Section title={t("business.brief.this")}>
          <Box pad={false}>
            <ul className="ilist">
              <li><span className="grow">{t("business.brief.version")}</span><span className="end biz-end-v">{t("business.brief.version_n", { n: brief.version })}</span></li>
              <li><span className="grow">{t("business.brief.approved")}</span><span className="end biz-end-v">{dayText(brief.approved_at)}</span></li>
              {PROV_MEANS.map(([p]) => (
                <li key={p}><span className="grow"><ProvBadge provenance={p} /></span><span className="end biz-end-v">{t("business.brief.lines", { n: count(p) })}</span></li>
              ))}
            </ul>
            <div className="box-foot">
              {change ? <Link to={change}>{t("business.brief.change")}</Link> : <span className="muted">{t("business.brief.change_no_kickoff")}</span>}
            </div>
          </Box>
        </Section>
        <BusinessFiles businessId={businessId} />
        <Section title={t("business.brief.legend")}>
          <Box pad={false}>
            <ul className="ilist biz-legend">
              {PROV_MEANS.map(([p, means]) => <li key={p}><ProvBadge provenance={p} /><span className="grow">{t(means)}</span></li>)}
            </ul>
          </Box>
        </Section>
      </div>
    </div>
  );
}

/** For the overview: the required sections, one line each, with who stands behind it. */
export function BriefSummary({ data, kickoffId, more }: { data: BriefView; kickoffId: number | null; more: ReactNode }) {
  const brief = data.brief;
  const change = kickoffId ? `/kickoff/${kickoffId}` : null;
  if (!brief) return <NoBrief data={data} change={change} />;
  const required = brief.sections.filter((s) => s.required);
  return (
    <Box pad={false}>
      <ul className="ilist biz-bsum">
        {required.map((s) => {
          const l = s.lines[0];
          return (
            <li key={s.key}>
              <div className="grow">
                <p className="t2">{s.title}</p>
                <p className="biz-bsum-t">{l ? l.text : <span className="muted">{t("business.brief.summary_empty")}</span>}</p>
              </div>
              {l && <ProvBadge provenance={l.provenance} />}
            </li>
          );
        })}
      </ul>
      <div className="box-foot">{tx("business.brief.summary_foot", { n: brief.version, date: dayText(brief.approved_at), more })}</div>
    </Box>
  );
}
