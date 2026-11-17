import { Fragment, ReactNode, useId, useRef, useState } from "react";
import { Row } from "../api";
import { locale, PlainKey, t, tx } from "../i18n";
import { agentName } from "../labels";
import { CopyButton, safeUrl } from "./panel";
import {
  Doubt, DoubtKind, doubtsOf, FileKind, fileGroups, filesChanged, noteOf, Review, Severity, syncNote, UseSteps,
} from "./reviewModel";
import { Badge } from "./ui";
import "./decision.css";

const SEVERITY_TONE: Record<Severity, string> = { high: "bad", medium: "warn", low: "muted" };
const SEVERITY: Record<Severity, PlainKey> = { high: "decide.mr.sev.high", medium: "decide.mr.sev.medium", low: "decide.mr.sev.low" };
const WHY: Record<DoubtKind, PlainKey> = {
  unmet: "decide.mr.why.unmet", beyond: "decide.mr.why.beyond",
  built_partly: "decide.mr.why.built_partly", built_no: "decide.mr.why.built_no", unchecked: "decide.mr.why.unchecked",
  finding: "decide.mr.why.finding", unseen: "decide.mr.why.unseen", sample: "decide.mr.why.sample",
  needs: "decide.mr.why.needs_unnamed", no_docker: "decide.mr.why.no_docker", scenario: "decide.mr.why.scenario",
};
/** Why a doubt is one: its kind's sentence and the record's own words after it, except a step that lacked a named need. */
const reasonOf = (d: Doubt) => (d.kind === "needs" && d.detail ? t("decide.mr.why.needs", { need: d.detail })
  : d.detail ? `${t(WHY[d.kind])} ${d.detail}` : t(WHY[d.kind]));
// The line a checked doubt writes in the decline note.
const LINE = {
  unmet: "decide.mr.line.unmet", beyond: "decide.mr.line.beyond",
  built_partly: "decide.mr.line.built_partly", built_no: "decide.mr.line.built_no", unchecked: "decide.mr.line.unchecked",
  finding: "decide.mr.line.finding", unseen: "decide.mr.line.unseen", sample: "decide.mr.line.sample",
  needs: "decide.mr.line.unrun", no_docker: "decide.mr.line.unrun", scenario: "decide.mr.line.scenario",
} as const satisfies Record<DoubtKind, string>;
const KIND: Record<FileKind, PlainKey> = {
  source: "decide.mr.kind.source", tests: "decide.mr.kind.tests", docs: "decide.mr.kind.docs",
  config: "decide.mr.kind.config", other: "decide.mr.kind.other",
};

/** The way to use a build with no preview: where it is, the commands in one block to copy, and what it needs. */
export function UseIt({ use }: { use: UseSteps }) {
  return (
    <>
      <p className="small">{t("decide.mr.where", { folder: use.folder, branch: use.branch })}</p>
      <div className="dp-draft-h">
        <p className="dp-sub">{t("decide.mr.run")}</p>
        <CopyButton text={use.steps.join("\n")} what={t("decide.mr.run")} />
      </div>
      <pre className="dp-draft dp-changes mr-cmds">{use.steps.join("\n")}</pre>
      <p className="muted small">{t("decide.mr.from_files")}</p>
      {use.needs.length > 0 && (
        <ul className="dp-list">
          {use.needs.map((n) => (
            <li key={n.name}>
              {t(n.required ? "decide.mr.need_required" : "decide.mr.need_optional", { name: n.name, what: n.what })}
              {n.without && <span className="muted"> {t("decide.mr.need_without", { without: n.without })}</span>}
            </li>
          ))}
        </ul>
      )}
      {use.why && <p className="muted small">{t("decide.mr.note", { why: use.why })}</p>}
    </>
  );
}

export interface TeamMember { agent: string; model: string | null }
/** Who built, reviewed and checked a build, from the model calls the ledger recorded for it. */
export interface Team { built: TeamMember[]; reviewed: TeamMember[]; checked: TeamMember[] }

/** "Engineer (opus)", or "Code reviewer (sonnet) and Product (sonnet)", the list worded by the owner's language. */
function Members({ list }: { list: TeamMember[] }) {
  const parts = new Intl.ListFormat(locale(), { style: "long", type: "conjunction" }).formatToParts(list.map((_, i) => String(i)));
  return (
    <>
      {parts.map((p, i): ReactNode => {
        const m = p.type === "element" ? list[Number(p.value)] : null;
        return <Fragment key={i}>{m ? <>{agentName(m.agent)}{m.model && <span className="muted"> ({m.model})</span>}</> : p.value}</Fragment>;
      })}
    </>
  );
}

/** Built by and checked by, a line under the headline. The reviewers count as checkers. */
export function BuiltBy({ team }: { team?: Team | null }) {
  if (!team) return null;
  const seen = new Set<string>();
  const checked = [...team.reviewed, ...team.checked].filter((m) => !seen.has(`${m.agent}|${m.model}`) && seen.add(`${m.agent}|${m.model}`));
  if (!team.built.length && !checked.length) return null;
  return (
    <p className="small mr-team">
      {team.built.length > 0 && <span>{tx("decide.mr.built_by", { agent: <Members list={team.built} /> })}</span>}
      {team.built.length > 0 && checked.length > 0 && " · "}
      {checked.length > 0 && <span>{tx("decide.mr.checked_by", { agents: <Members list={checked} /> })}</span>}
    </p>
  );
}

/**
 * What the owner checks before deciding a merge, below the walkthrough: what to doubt, worst first, then who built it,
 * what was built and the code, folded. Ticking a doubt writes the decline note while the note is still the one this
 * wrote (`syncNote`). Without `onNote` (a decided card) it reads, with no ticks.
 */
export function MergeReview({ r, note, onNote }: { r: Row; note: string | null; onNote: ((v: string) => void) | null }) {
  const review: Review | undefined = r.review;
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const written = useRef<string | null>(null);
  if (r.action_kind !== "dev.merge" || !review) return null;
  const unseen: string[] = r.try_it?.unseen ?? [];
  const sample: string | null = r.try_it?.sample ?? null;

  const doubts = doubtsOf(review, unseen, sample);
  const tick = (d: Doubt) => {
    const nextTicked = new Set(ticked);
    if (ticked.has(d.id)) nextTicked.delete(d.id); else nextTicked.add(d.id);
    setTicked(nextTicked);
    if (note === null || !onNote) return;
    const lines = doubts.filter((x) => nextTicked.has(x.id)).map((x) => t(LINE[x.kind], { text: x.text }));
    const next = syncNote(note, written.current, noteOf(t("decide.mr.note_lead"), lines));
    written.current = next.written;
    if (next.note !== note) onNote(next.note);
  };
  return (
    <>
      <BuiltBy team={r.team} />
      <Doubts doubts={doubts} checked={(d) => ticked.has(d.id)} onTick={onNote ? tick : null} />
      <BuiltItems review={review} />
      <CodeFiles r={r} review={review} />
    </>
  );
}

function Doubts({ doubts, checked, onTick }: { doubts: Doubt[]; checked: (d: Doubt) => boolean; onTick: ((d: Doubt) => void) | null }) {
  const uid = useId();
  return (
    <section className="dp-sec mr-sec" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("decide.mr.doubts")}</h3>
      {!doubts.length ? <p className="muted small">{t("decide.mr.doubts_none")}</p> : (
        <>
          {onTick && <p className="muted small">{t("decide.mr.doubts_hint")}</p>}
          <ul className="mr-doubts">
            {doubts.map((d) => {
              const body = (
                <>
                  <span className="mr-doubt-t">{d.text}</span>
                  <Badge value={t(SEVERITY[d.severity])} tone={SEVERITY_TONE[d.severity]} raw />
                  <span className="mr-doubt-w">{reasonOf(d)}</span>
                </>
              );
              return (
                <li key={d.id}>
                  {onTick ? (
                    <label className="mr-doubt">
                      <input type="checkbox" checked={checked(d)} onChange={() => onTick(d)} />
                      {body}
                    </label>
                  ) : <div className="mr-doubt mr-doubt-ro">{body}</div>}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

/** The build's items in plain words, as the Engineering screen lists them, one fold away. */
function BuiltItems({ review }: { review: Review }) {
  const items = review.items;
  if (!items) return null;
  return (
    <details className="dp-fold">
      <summary>{t("build.items.title")} · {t("build.items.done", { done: items.done, total: items.total })}</summary>
      <ol className="dp-list mr-items">
        {items.tasks.map((x) => (
          <li key={x.n}>{x.title}{x.detail && <span className="muted"> · {x.detail}</span>}</li>
        ))}
      </ol>
    </details>
  );
}

/** The branch and how far it is ahead, then the changed files by kind, each kind one fold away. */
function CodeFiles({ r, review }: { r: Row; review: Review }) {
  const code: { branch: string; pr_url: string | null; changes: string | null } | null = r.try_it?.code ?? null;
  if (!code) return null;
  const groups = fileGroups(code.changes ?? "");
  const pr = safeUrl(code.pr_url);
  return (
    <details className="dp-fold">
      <summary>{t("decide.mr.code", { n: filesChanged(code.changes ?? "", groups) })}</summary>
      <p className="small">
        <span className="mono">{code.branch}</span>
        {review.commits_ahead != null && <> · {t("decide.mr.ahead", { n: review.commits_ahead })}</>}
        {pr && <> · <a href={pr} target="_blank" rel="noreferrer">{t("decide.code.pr")}</a></>}
      </p>
      {groups.map((g) => (
        <details key={g.kind} className="dp-fold-in mr-group">
          <summary className="small">{t(KIND[g.kind])} <span className="muted">{g.files.length}</span></summary>
          <ul className="mr-files">
            {g.files.map((f) => <li key={f.path}><span className="mono">{f.path}</span> <span className="muted mono">{f.lines}</span></li>)}
          </ul>
        </details>
      ))}
    </details>
  );
}
