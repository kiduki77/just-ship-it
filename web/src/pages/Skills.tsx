import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, post, Row, useLive } from "../api";
import { useCompanyLive } from "../business";
import { useWorker } from "../components/frame";
import { Box, Chip, Hint, PageHead, ScopeLabel, Section, Status, Tone } from "../components/kit";
import { PanelLink } from "../components/panel";
import { ActionButton, Agent, Loading, Markdown, Time } from "../components/ui";
import { PlainKey, t } from "../i18n";
import { agentName, label } from "../labels";
import "./misc.css";
import "./people.css";

/*
 * A skill's state for the owner. The server's status alone stops at "awaiting approval" after the owner approves
 * (only the worker turns a skill on), so the decision the skill waits on decides: still the owner's, approved and
 * turning on, or left off.
 */
type Phase = "yours" | "turning_on" | "testing" | "drafting" | "on" | "turned_off" | "left_off" | "failed" | "retired";

const PHASE: Record<Phase, { label: PlainKey; tone: Tone; rank: number; note: PlainKey | null }> = {
  yours: { label: "company-people.skills.phase.yours", tone: "warn", rank: 0, note: "company-people.skills.note.yours" },
  turning_on: { label: "company-people.skills.phase.turning_on", tone: "muted", rank: 1, note: "company-people.skills.note.turning_on" },
  testing: { label: "company-people.skills.phase.testing", tone: "run", rank: 2, note: "company-people.skills.note.testing" },
  drafting: { label: "company-people.skills.phase.drafting", tone: "muted", rank: 3, note: "company-people.skills.note.drafting" },
  on: { label: "company-people.skills.phase.on", tone: "ok", rank: 4, note: "company-people.skills.note.on" },
  turned_off: { label: "company-people.skills.phase.turned_off", tone: "muted", rank: 5, note: "company-people.skills.note.turned_off" },
  left_off: { label: "company-people.skills.phase.left_off", tone: "muted", rank: 6, note: "company-people.skills.note.left_off" },
  failed: { label: "company-people.skills.phase.failed", tone: "muted", rank: 7, note: "company-people.skills.note.failed" },
  retired: { label: "company-people.skills.phase.retired", tone: "muted", rank: 8, note: "company-people.skills.note.retired" },
};

const APPROVED = ["APPROVED", "AUTO_APPROVED"];

function phaseOf(s: Row, req: Row | undefined): Phase {
  switch (s.status) {
    case "AWAITING_APPROVAL":
      return !req || req.status === "PENDING" ? "yours" : APPROVED.includes(req.status) ? "turning_on" : "left_off";
    case "ACTIVE": return "on";
    case "EVALUATING": case "CANDIDATE": return "testing";
    case "PROPOSED": case "GAP_DETECTED": return "drafting";
    case "EVAL_FAILED": case "REJECTED": return "failed";
    // retired by the owner's turn-off, or by a newer version the factory turned on
    case "DEPRECATED": return s.turned_off_at ? "turned_off" : "retired";
    default: return "retired";
  }
}

/** SKILL.md's front matter `description:` line, and the body without the front matter. */
function splitSkill(md: string | null | undefined): { description: string | null; body: string | null } {
  if (!md) return { description: null, body: null };
  const m = /^---\s*\n([\s\S]*?)\n---\s*\n?/.exec(md);
  if (!m) return { description: null, body: md };
  const d = /^description:\s*(.+)$/m.exec(m[1]);
  return { description: d ? d[1].trim().replace(/^["']|["']$/g, "") : null, body: md.slice(m[0].length) };
}

type Gap = { text: string; agents: string[]; count: number };

/** The same gap reported in different case or spacing is one gap, with every agent that reported it and the total. */
function mergeGaps(gaps: Row[]): Gap[] {
  const by = new Map<string, Gap>();
  for (const g of gaps) {
    const key = String(g.gap ?? "").trim().toLowerCase().replace(/\s+/g, " ");
    const cur = by.get(key) ?? { text: String(g.gap ?? "").trim(), agents: [], count: 0 };
    if (!cur.agents.includes(g.agent)) cur.agents.push(g.agent);
    cur.count += Number(g.count ?? 0);
    by.set(key, cur);
  }
  return [...by.values()].sort((a, b) => b.count - a.count);
}

const pctOf = (v: number) => `${Math.round(v * 100)}%`;

/** The test against past work as three rows, without the skill and with it. */
function EvalTable({ summary }: { summary: Row }) {
  const rows: [PlainKey, string, string][] = [
    ["company-people.skills.eval.pass", t("company-people.skills.eval.of", { k: summary.baseline_pass ?? 0, n: summary.n ?? 0 }),
      t("company-people.skills.eval.of", { k: summary.candidate_pass ?? 0, n: summary.n ?? 0 })],
    ["company-people.skills.eval.unmet", Number(summary.baseline_unmet ?? 0).toFixed(1), Number(summary.candidate_unmet ?? 0).toFixed(1)],
    ["company-people.skills.eval.supported", pctOf(Number(summary.baseline_supported ?? 0)), pctOf(Number(summary.candidate_supported ?? 0))],
  ];
  return (
    <table className="tbl pp-eval">
      <thead><tr><th scope="col" /><th scope="col" className="r">{t("company-people.skills.eval.without")}</th><th scope="col" className="r">{t("company-people.skills.eval.with")}</th></tr></thead>
      <tbody>{rows.map(([k, a, b]) => <tr key={k}><th scope="row">{t(k)}</th><td className="r">{a}</td><td className="r">{b}</td></tr>)}</tbody>
    </table>
  );
}

/** Turn off a skill that is on, said first: which agents stop reading it, and that the company won't suggest it again. */
function TurnOff({ s, onDone }: { s: Row; onDone: () => void }) {
  return (
    <p className="pp-skill-act">
      <ActionButton className="btn-ghost" confirm={t("company-people.skills.off.confirm", { name: s.name })} keep={t("company-people.skills.off.keep")}
        what={t("company-people.skills.off.what", { agents: s.target_agents.map(agentName).join(", ") })}
        done={t("company-people.skills.off.done", { name: s.name })}
        run={() => post(`/skills/${s.id}/turn-off`, {}).then(onDone)}>
        {t("company-people.skills.off.button")}
      </ActionButton>
    </p>
  );
}

function SkillDetail({ s, phase, req, onChange }: { s: Row; phase: Phase; req: Row | undefined; onChange: () => void }) {
  const worker = useWorker();
  const { description, body } = splitSkill(s.content);
  const p = PHASE[phase];
  const summary: Row | null = s.eval_results?.summary ?? null;
  const proposal = s.proposal && (s.proposal.problem || s.proposal.expected_benefit) ? s.proposal : null;
  return (
    <Box className="pp-skill">
      <header className="pp-skill-h">
        <h2>{s.name} <span className="pp-quiet">v{s.version}</span></h2>
        <span className="pp-skill-tags"><span className="tag">{label(s.kind)}</span><Chip tone={p.tone}>{t(p.label)}</Chip></span>
      </header>
      {description && <p className="pp-skill-desc">{description}</p>}
      <p className={`pp-skill-note t-${p.tone}`}>
        {s.status === "REJECTED" ? t("company-people.skills.note.unsafe") : p.note && t(p.note)}
        {phase === "turning_on" && worker.down && <> {t("company-people.skills.note.worker_off")}</>}
      </p>
      {phase === "on" && <TurnOff s={s} onDone={onChange} />}
      {phase === "yours" && s.approval_request_id && (
        <p><PanelLink className="btn-primary" target={{ key: "req", id: s.approval_request_id }} to={`/skills?id=${s.id}&req=${s.approval_request_id}`}>{t("company-people.skills.decide")}</PanelLink></p>
      )}
      <dl className="pp-skill-dl">
        <dt>{t("company.skills.targets")}</dt>
        <dd>{s.target_agents.map((a: string, i: number) => <span key={a}>{i > 0 && ", "}<Agent id={a} /></span>)}</dd>
        {s.source_gap_count > 0 && <><dt>{t("company-people.skills.source")}</dt><dd>{t("company-people.skills.from_gaps", { n: s.source_gap_count })}</dd></>}
        {req && <><dt>{t("company-people.skills.decision")}</dt><dd>{label(req.status)}{req.resolved_at ? <> · <Time v={req.resolved_at} /></> : null}</dd></>}
        {s.turned_off_at && <><dt>{t("company-people.skills.turned_off_at")}</dt><dd><Time v={s.turned_off_at} /></dd></>}
        <dt>{t("company-people.skills.updated")}</dt><dd><Time v={s.updated_at} /></dd>
      </dl>
      {proposal && (
        <section className="pp-skill-sec">
          <h3>{t("company.skills.proposal")}</h3>
          {proposal.problem && <p>{proposal.problem}</p>}
          {proposal.expected_benefit && <p className="pp-quiet">{proposal.expected_benefit}</p>}
        </section>
      )}
      {s.eval_results && (
        <section className="pp-skill-sec">
          <h3>{t("company.skills.eval")} {summary && <span className="pp-quiet pp-n">{t("company-people.skills.eval.sample", { n: summary.n ?? 0 })}{Number(summary.n ?? 0) < 5 ? ` · ${t("company-people.skills.eval.small")}` : ""}</span>}</h3>
          {summary ? <EvalTable summary={summary} /> : <p className="pp-quiet">{t("company-people.skills.eval.none")}</p>}
          <details className="pp-raw"><summary>{t("company-people.skills.eval.raw")}</summary><pre className="pre">{JSON.stringify(s.eval_results, null, 2)}</pre></details>
        </section>
      )}
      {body && (
        <section className="pp-skill-sec">
          <h3>{t("company-people.skills.content")}</h3>
          <div className="pp-skill-md"><Markdown text={body} /></div>
        </section>
      )}
    </Box>
  );
}

/** Skills in one sentence: what waits for the owner, what is being turned on or tested, what is already on. */
function skillsStatus(phases: Phase[], gaps: number): Status {
  const n = (p: Phase) => phases.filter((x) => x === p).length;
  const on = n("on") ? t("company.skills.on", { n: n("on") }) : t("company.skills.none_on");
  if (n("yours")) return { tone: "warn", text: t("company-people.skills.status.yours", { n: n("yours"), on }) };
  if (n("turning_on")) return { tone: "muted", text: t("company-people.skills.status.turning_on", { n: n("turning_on"), on }) };
  if (n("testing")) return { tone: "run", text: t("company.skills.evaluating", { n: n("testing"), on }) };
  if (n("on")) return { tone: "ok", text: t("company.skills.active", { n: n("on") }) };
  return { text: gaps ? t("company.skills.gaps", { n: gaps }) : t("company.skills.idle") };
}

export default function Skills() {
  const { data, error, reload, failure } = useLive<{ skills: Row[]; gaps: Row[] }>("/skills", ["skills"]);
  const { data: requests } = useCompanyLive<Row[]>("/inbox?include_resolved=true", ["agent_requests"]);
  const [params, setParams] = useSearchParams();
  const [queued, setQueued] = useState(false);
  const worker = useWorker();
  const byId = new Map((requests ?? []).map((r) => [r.id, r]));
  const items = (data?.skills ?? []).map((s) => ({ s, req: s.approval_request_id ? byId.get(s.approval_request_id) : undefined }))
    .map((x) => ({ ...x, phase: phaseOf(x.s, x.req) }))
    .sort((a, b) => PHASE[a.phase].rank - PHASE[b.phase].rank || b.s.id - a.s.id);
  const gaps = mergeGaps(data?.gaps ?? []);
  const yours = items.filter((x) => x.phase === "yours" && x.s.approval_request_id);
  const runFactory = () => post("/skills/run-factory", {}).catch((e) => {
    if (e instanceof ApiError && e.status === 409) return setQueued(true);
    throw e;
  });
  const factory = queued ? (
    <button type="button" className="btn-ghost" disabled>{t("company-people.skills.factory_queued")}</button>
  ) : (
    <ActionButton className="btn-ghost" disabled={!data || gaps.length === 0}
      why={data && gaps.length === 0 ? t("company-people.skills.factory_no_gaps") : undefined}
      confirm={t("company-people.skills.factory_yes")} what={t("company-people.skills.factory_what")}
      done={worker.down ? t("company-people.skills.factory_done_off") : t("company.skills.queued")}
      run={() => runFactory().then(reload)}>
      {t("company.skills.run")}
    </ActionButton>
  );
  const head = (
    <PageHead title={t("company.skills.title")}
      status={data ? skillsStatus(items.map((x) => x.phase), gaps.length) : error ? { tone: "bad", text: t("company-people.skills.load_failed") } : null}
      sub={<>{t("company-people.skills.sub")} {t("company-people.skills.scope")}</>}
      actions={<>
        {yours.length > 0 && (
          <PanelLink className="btn-primary" target={{ key: "req", id: yours[0].s.approval_request_id }} to={`/skills?req=${yours[0].s.approval_request_id}`}>
            {t("company-people.skills.decide_n", { n: yours.length })}
          </PanelLink>
        )}
        {factory}
        <ScopeLabel>{t("common.whole_company")}</ScopeLabel>
      </>} />
  );
  if (!data) return <div className="owner-page kui mx pp-skills">{head}<Loading error={error} retry={reload} failure={failure} /></div>;

  const wanted = Number(params.get("id")) || null;
  const current = items.find((x) => x.s.id === wanted) ?? items[0];
  const pick = (id: number) => {
    const next = new URLSearchParams(params);
    next.set("id", String(id));
    setParams(next, { replace: true });
  };

  return (
    <div className="owner-page kui mx pp-skills">
      {head}

      <Section title={t("company.skills.title")} count={items.length}>
        {items.length === 0 ? (
          <Hint title={t("company.skills.empty")}>{t("company.skills.empty_hint")}</Hint>
        ) : (
          <div className="mx-split">
            <Box pad={false} className="mx-side">
              <ul className="rows" aria-label={t("company.skills.list")}>
                {items.map(({ s, phase }) => (
                  <li key={s.id}>
                    <button type="button" className={`row-i mx-pick pp-pick ${current?.s.id === s.id ? "on" : ""}`} aria-current={current?.s.id === s.id} onClick={() => pick(s.id)}>
                      <span className="row-main">
                        <span className="row-t">{s.name} <span className="pp-quiet">v{s.version}</span></span>
                        <span className="row-m">{label(s.kind)} · {s.target_agents.map(agentName).join(", ")}</span>
                      </span>
                      <span className="row-end"><Chip tone={PHASE[phase].tone}>{t(PHASE[phase].label)}</Chip></span>
                    </button>
                  </li>
                ))}
              </ul>
            </Box>
            {current && <SkillDetail s={current.s} phase={current.phase} req={current.req} onChange={reload} />}
          </div>
        )}
        {current && (
          <p className="sr-only" role="status">{`${current.s.name} v${current.s.version} ${t(PHASE[current.phase].label)}`}</p>
        )}
      </Section>

      <Section title={t("company.skills.gaps_title")} count={gaps.length}>
        {gaps.length === 0 ? (
          <Hint title={t("company.skills.no_gaps")}>{t("company.skills.no_gaps_hint")}</Hint>
        ) : (
          <details className="pp-gaps">
            <summary>{t("company-people.skills.gaps_summary", { n: gaps.length, total: gaps.reduce((n, g) => n + g.count, 0) })}</summary>
            <Box pad={false}>
              <ul className="rows">
                {gaps.slice(0, 30).map((g, i) => (
                  <li key={i} className="row-i">
                    <span className="row-main">
                      <span className="row-t pp-wrap">{g.text}</span>
                      <span className="row-m">{g.agents.map(agentName).join(", ")}</span>
                    </span>
                    <span className="row-end">{t("company.skills.times", { n: g.count })}</span>
                  </li>
                ))}
              </ul>
            </Box>
            {gaps.length > 30 && <p className="pp-quiet">{t("company.skills.top_gaps")}</p>}
          </details>
        )}
      </Section>
    </div>
  );
}
