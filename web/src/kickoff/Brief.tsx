import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { citeText } from "../components/attach";
import { Icon } from "../components/icons";
import { Badge } from "../components/ui";
import { t } from "../i18n";
import { provenanceLabel } from "../labels";
import { BriefLine, BriefSection, fill, KickoffView, Provenance, sourceTurn } from "./types";

const FLASH_MS = 2400;

function Mark({ provenance }: { provenance: string }) {
  const common = { width: 10, height: 10, viewBox: "0 0 10 10", "aria-hidden": true } as const;
  const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  switch (provenance) {
    case "said":
      return <svg {...common}><rect x="1" y="1" width="8" height="8" rx="2" fill="currentColor" /></svg>;
    case "confirmed":
      return <svg {...common}><rect x=".75" y=".75" width="8.5" height="8.5" rx="2" {...stroke} /><path d="M2.8 5.2l1.5 1.5 3-3.2" {...stroke} strokeWidth="1.3" /></svg>;
    case "guess":
      return <svg {...common}><path d="M5 .9 9.1 5 5 9.1.9 5z" {...stroke} strokeDasharray="2 1.5" /></svg>;
    default:
      return <svg {...common}><path d="M3.5 3.8a1.5 1.5 0 1 1 2.2 1.3C5.2 5.4 5 5.7 5 6.3M5 8.4v.1" {...stroke} /></svg>;
  }
}

/** Who stands behind a line. Shape and border differ as well as colour, and the word is always written. */
export function ProvBadge({ provenance }: { provenance: Provenance | string }) {
  return (
    <span className={`pv pv-${provenance}`}>
      <Mark provenance={provenance} />
      {provenanceLabel(provenance)}
    </span>
  );
}

function LineRow({ line, onJump }: { line: BriefLine; onJump: (n: number) => void }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  const n = sourceTurn(line.source);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOver(el.scrollHeight > el.clientHeight + 1);
  }, [line.text, open]);
  const text = <span ref={ref} className={`bl-t ${open ? "" : "clamp2"}`}>{line.text}</span>;
  return (
    <li className="bl">
      <ProvBadge provenance={line.provenance} />
      <div className="bl-body">
        {n === null ? text : (
          <button type="button" className="bl-jump" onClick={() => onJump(n)}>
            {text}
            <span className="sr-only">{t("kickoff.brief.jump")}</span>
          </button>
        )}
        {(over || open) && (
          <button type="button" className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? t("kickoff.brief.less") : t("kickoff.brief.more")}
          </button>
        )}
        {line.quote && <span className="bl-quote small muted">{t("kickoff.brief.quote", { quote: line.quote })}</span>}
        {line.doc && <span className="bl-cite small muted"><Icon name="file" size={12} />{citeText(line.doc)}</span>}
      </div>
    </li>
  );
}

function SectionRow({ section, flash, needed, onJump }: { section: BriefSection; flash: boolean; needed: boolean; onJump: (n: number) => void }) {
  return (
    <li className={`bs ${flash ? "flash" : ""}`} data-section={section.key}>
      <h3 className="bs-h" title={section.drives}>
        <span className="strong">{section.title}</span>
        {section.drives && !section.lines.length && <span className="muted bs-meta"> · {section.drives}</span>}
      </h3>
      {section.lines.length ? (
        <ul className="bs-lines">{section.lines.map((l) => <LineRow key={l.id} line={l} onJump={onJump} />)}</ul>
      ) : (
        !needed && <p className="bs-empty muted">{t("kickoff.brief.empty_section")}</p>
      )}
      {needed && <p className="bs-need small"><Badge value={t("kickoff.brief.needed")} tone="warn" raw /> {t("kickoff.brief.needed_why")}</p>}
    </li>
  );
}

const sig = (s: BriefSection) => s.lines.map((l) => `${l.id}:${l.provenance}:${l.text}`).join("|");

export interface Flash {
  keys: Set<string>;
  titles: string[];
}

const NO_FLASH: Flash = { keys: new Set(), titles: [] };

/**
 * Section keys whose lines changed since the last brief the owner saw, for a moment. The change is read from the
 * lines' content, so a refetch that returns the same brief neither flashes nor restarts the timer.
 */
export function useFlash(sections: BriefSection[] | undefined): Flash {
  const prev = useRef<Map<string, string> | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [state, setState] = useState<Flash>(NO_FLASH);
  const content = sections ? sections.map((s) => `${s.key}${sig(s)}`).join("") : null;
  useEffect(() => {
    if (!sections) {
      prev.current = null;
      return;
    }
    const now = new Map(sections.map((s) => [s.key, sig(s)]));
    const before = prev.current;
    prev.current = now;
    if (!before) return;
    const changed = sections.filter((s) => before.get(s.key) !== now.get(s.key));
    if (!changed.length) return;
    setState({ keys: new Set(changed.map((s) => s.key)), titles: changed.map((s) => s.title) });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState(NO_FLASH), FLASH_MS);
  }, [content]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return state;
}

export function FlashStatus({ flash }: { flash: Flash }) {
  return <div className="sr-only" role="status">{flash.titles.length ? t("kickoff.brief.flash", { titles: flash.titles.join(", ") }) : ""}</div>;
}

type BriefStatus = "approved" | "awaiting" | "draft";

const briefStatus = (v: KickoffView): BriefStatus =>
  v.brief?.approved_at ? "approved" : v.session.status === "DONE" ? "awaiting" : "draft";

function StatusWord({ view }: { view: KickoffView }) {
  const status = briefStatus(view);
  if (status === "awaiting") return <Badge value={t("kickoff.brief.awaiting")} tone="warn" raw />;
  return <>{status === "approved" ? t("kickoff.brief.approved", { version: view.brief?.version ?? "" }) : t("kickoff.brief.draft")}</>;
}

/**
 * The plan's progress the way approval reads it: how many sections still block approval (the gate sections the owner
 * must say or confirm), then the other required ones, which the company fills with its default when left empty.
 */
export function BriefHead({ view, as: Tag = "h2" }: { view: KickoffView; as?: "h2" | "span" }) {
  const required = (view.brief?.sections ?? []).filter((s) => s.required);
  const gates = required.filter((s) => s.gate);
  const left = view.brief?.approved_at ? 0 : view.approval.blockers.length;
  const others = required.filter((s) => !s.gate);
  const othersSettled = others.filter((s) => s.state === "settled").length;
  const done = Math.max(0, gates.length - left);
  return (
    <div className="bh">
      <Tag className="bh-t">
        <span className="strong">{t("kickoff.brief.title")}</span>
        <span className="muted"> · <StatusWord view={view} /></span>
      </Tag>
      <p className={`bh-gate ${left ? "warn-t" : "ok-t"}`}>{left ? t("kickoff.brief.gate_left", { n: left }) : t("kickoff.brief.gate_none")}</p>
      {others.length > 0 && <p className="bh-rest small muted">{t("kickoff.brief.others", { settled: othersSettled, total: others.length })}</p>}
      {gates.length > 0 && (
        <div className="meter sm" role="progressbar" aria-label={t("kickoff.brief.meter")} aria-valuemin={0} aria-valuemax={gates.length} aria-valuenow={done}>
          <span style={{ width: `${(done / gates.length) * 100}%` }} />
        </div>
      )}
    </div>
  );
}

export function BriefPanel({ view, flash, onJump, head = true }: {
  view: KickoffView; flash: Flash; onJump: (n: number) => void; head?: boolean;
}) {
  const sections = view.brief?.sections;
  const { keys } = flash;
  if (!sections) return <p className="muted small">{t("kickoff.brief.none")}</p>;
  const needed = new Set(view.brief?.approved_at ? [] : view.approval.blockers);
  const required = sections.filter((s) => s.required);
  // a quiet section counts only once it holds something, so the count matches the list it opens to
  const optional = sections.filter((s) => !s.required && !s.legacy && (!s.quiet || s.lines.length > 0));
  const legacy = sections.filter((s) => s.legacy);
  const filled = optional.filter((s) => s.lines.length);
  const empty = view.coverage.empty_optional;
  const row = (s: BriefSection) => <SectionRow key={s.key} section={s} flash={keys.has(s.key)} needed={needed.has(s.key)} onJump={onJump} />;
  return (
    <div className="brief">
      {head && <BriefHead view={view} />}
      <ol className="bs-list">{required.map(row)}</ol>
      <details className="bs-more">
        <summary>
          {t("kickoff.brief.optional", { n: optional.length, filled: filled.length })}
          {filled.some((s) => keys.has(s.key)) && <span className="bs-new">{t("kickoff.brief.just_changed")}</span>}
        </summary>
        <ol className="bs-list">{filled.map(row)}</ol>
        {empty.length > 0 && <p className="bs-empty-opt small muted">{t("kickoff.brief.empty_ok", { titles: empty.join(", ") })}</p>}
      </details>
      {legacy.length > 0 && (
        <details className="bs-more">
          <summary>{t("kickoff.brief.legacy", { n: legacy.length })}</summary>
          <p className="small muted bs-legacy-note">{t("kickoff.brief.legacy_note")}</p>
          <ol className="bs-list">{legacy.map(row)}</ol>
        </details>
      )}
    </div>
  );
}
