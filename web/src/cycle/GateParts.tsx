import { EvRefs, usd } from "../components/ui";
import { t } from "../i18n";
import { SummaryView } from "./types";

type Claim = SummaryView["claims"][number];
const TONE = { high: "ok", mid: "run", low: "warn" } as const;
const PIPS = { high: 3, mid: 2, low: 1 } as const;

const evidenceCount = (s: SummaryView) => new Set(s.claims.flatMap((c) => c.evidence_ids.map(String))).size;

/** Three steps of confidence, filled by how sure the evidence is. The label beside it always says the same in words. */
function Pips({ level }: { level: Claim["confidence"] }) {
  return (
    <span className={`gv-pips t-${TONE[level]}`} role="img" aria-label={t(`cycle.confidence.${level}`)}>
      {[0, 1, 2].map((k) => <i key={k} className={k < PIPS[level] ? "on" : ""} />)}
    </span>
  );
}

/** The claims the evidence only half holds up, weakest first: what the owner should doubt before choosing. */
export function UnsureRows({ claims }: { claims: Claim[] }) {
  const rows = claims.filter((c) => c.confidence !== "high").sort((a, b) => PIPS[a.confidence] - PIPS[b.confidence]);
  if (!rows.length) return null;
  return (
    <ul className="gv-rows">
      {rows.map((c, i) => (
        <li key={i}>
          <p>{c.claim}</p>
          <p className="gv-meta small"><Pips level={c.confidence} /><span>{t(`cycle.confidence.${c.confidence}`)}</span>
            {c.capped && <span className="muted">{t("cycle.confidence.capped")}</span>}
            {c.evidence_ids.length > 0 && <EvRefs ids={c.evidence_ids} marks />}</p>
        </li>
      ))}
    </ul>
  );
}

/** Every claim, one line each with its confidence in words and its evidence: the record, folded away by default. */
export function ClaimRows({ claims }: { claims: Claim[] }) {
  if (!claims.length) return null;
  return (
    <ul className="gv-rows">
      {claims.map((c, i) => (
        <li key={i}>
          <p>{c.claim}</p>
          <p className="gv-meta small"><Pips level={c.confidence} /><span>{t(`cycle.confidence.${c.confidence}`)}</span>
            {c.capped && <span className="muted">{t("cycle.confidence.capped")}</span>}
            {c.evidence_ids.length > 0 && <EvRefs ids={c.evidence_ids} marks />}</p>
        </li>
      ))}
    </ul>
  );
}

export function Checks({ title, items, done }: { title: string; items: string[]; done: boolean }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="cy-k">{title}</p>
      <ul className={`gv-checks ${done ? "is-done" : "is-open"}`}>{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  );
}

/** The counts, three plain lines at the bottom: bookkeeping, not the finding. */
export function Foot({ s }: { s: SummaryView }) {
  return (
    <ul className="gv-foot small muted num">
      <li>{t("cycle.gate.tasks", { done: s.tasks.done, total: s.tasks.total })}{s.tasks.failed > 0 && <span className="bad-t"> · {t("cycle.gate.failed", { n: s.tasks.failed })}</span>}</li>
      <li>{t("cycle.gate.spent", { usd: usd(s.cost_usd) })}</li>
      <li>{t("cycle.viz.evidence_total", { n: evidenceCount(s) })}</li>
    </ul>
  );
}
