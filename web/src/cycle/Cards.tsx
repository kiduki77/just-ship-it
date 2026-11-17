import { CSSProperties, ReactNode, useState } from "react";
import { Icon } from "../components/icons";
import { Chip } from "../components/kit";
import { PanelLink } from "../components/panel";
import { ActionButton, EvRefs, refHref, usd } from "../components/ui";
import { t } from "../i18n";
import { Waiting } from "../kickoff/Waiting";
import { estimateParts, routineWord, todoWord } from "./format";
import { Checks, ClaimRows, Foot, UnsureRows } from "./GateParts";
import {
  ChangeView, Decision, DecisionCard, GateCard, ProposalCard, StageCard, StoppedCard, SummaryView, WaitingItem,
} from "./types";

export type Decide = (cycleId: number, d: Decision) => Promise<unknown>;

const CHANGE_TONE = { added: "ok", edited: "run", moved: "info", removed: "bad" } as const;

function ChangeTag({ kind }: { kind: ChangeView["kind"] }) {
  return <span className={`tag t-${CHANGE_TONE[kind]}`}>{t(`cycle.change.${kind}`)}</span>;
}

function Names({ title, items }: { title: string; items: string[] }) {
  return items.length ? <p className="cy-fine"><span className="cy-k">{title}</span> {items.join(" · ")}</p> : null;
}

/** Column widths that follow how much each column holds across the stages, so a long column is not cut to a quarter. */
function columnWidths(stages: StageCard[]): CSSProperties {
  const sum = (text: (s: StageCard) => string) => Math.max(stages.reduce((n, s) => n + text(s).length, 0), 1);
  const [doing, owner, cost] = [
    sum((s) => s.doing),
    sum((s) => s.owner_does + s.decision_points.join("")),
    sum((s) => { const e = estimateParts(s.estimate); return e.lines.join("") + e.basis; }),
  ];
  return { "--cy-cols": `fit-content(220px) minmax(150px, ${doing}fr) minmax(150px, ${owner}fr) minmax(150px, ${cost}fr)` } as CSSProperties;
}

/** One stage as the owner agrees to it: what the company does, what the owner does, a guessed time and usage. */
export function StageBlock({ stage, n }: { stage: StageCard; n?: number }) {
  const est = estimateParts(stage.estimate);
  return (
    <li className="cy-stage">
      <div className="cy-stage-name">
        {n != null && <span className="cy-n num">{n}</span>}
        <div>
          <p className="strong">{stage.title}</p>
          {stage.change && <ChangeTag kind={stage.change} />}
        </div>
      </div>
      <div className="cy-stage-cell">
        <p className="cy-k">{t("cycle.proposal.company")}</p>
        <p>{stage.doing}</p>
      </div>
      <div className="cy-stage-cell">
        <p className="cy-k">{t("cycle.proposal.owner")}</p>
        <p>{stage.owner_does}</p>
        {stage.decision_points.length > 0 && (
          <p className="cy-fine"><span className="cy-k">{t("cycle.proposal.decides")}</span> {stage.decision_points.join(" · ")}</p>
        )}
      </div>
      <div className="cy-stage-cell">
        <p className="cy-k">{t("cycle.proposal.cost")}</p>
        {est.lines.map((l) => <p key={l} className="num">{l}</p>)}
        {est.basis && <p className="cy-fine">{est.basis}</p>}
      </div>
      {(stage.recurring.length > 0 || stage.owner_work.length > 0 || stage.takes_over.length > 0) && (
        <div className="cy-stage-more">
          <Names title={t("cycle.proposal.runs")} items={stage.recurring.map(routineWord)} />
          <Names title={t("cycle.proposal.todos")} items={stage.owner_work.map(todoWord)} />
          {stage.takes_over.length > 0 && <p className="cy-fine">{t("cycle.proposal.takes_over")}</p>}
        </div>
      )}
    </li>
  );
}

/** "Say what to change": a text box that goes to the CEO, which answers with a new version. */
function Revise({ send, disabled }: { send: (note: string) => Promise<unknown>; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!open) {
    return <button type="button" className="btn-ghost" disabled={disabled} onClick={() => setOpen(true)}>{t("cycle.revise.open")}</button>;
  }
  const note = text.trim();
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await send(note);
      setText("");
      setOpen(false);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="cy-revise" onSubmit={(e) => { e.preventDefault(); if (note && !busy) go(); }}
      onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.preventDefault(); setOpen(false); } }}>
      <label className="cy-k" htmlFor="cy-revise-note">{t("cycle.revise.label")}</label>
      <textarea id="cy-revise-note" rows={3} maxLength={4000} value={text} placeholder={t("cycle.revise.ph")} autoFocus
        readOnly={busy} onChange={(e) => setText(e.target.value)} />
      <p className="small muted">{t("cycle.revise.hint")} {t("cycle.revise.hold")}</p>
      {error && <p className="bad-t small" role="alert">{error}</p>}
      <div className="row wrap">
        <button type="submit" className="btn-primary" disabled={!note || busy}>{busy ? t("common.working") : t("cycle.revise.send")}</button>
        <button type="button" className="btn-ghost" disabled={busy} onClick={() => setOpen(false)}>{t("common.cancel")}</button>
      </div>
    </form>
  );
}

/** A card that waits on the owner. It opens the dashboard's content right under the page title, so its heading is an h2. */
function Frame({ title, tag, children, id }: { title: ReactNode; tag?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <article className="box cy-card" id={id}>
      <header className="cy-card-h">
        <Chip tone="warn">{t("cycle.waiting.title")}</Chip>
        <h2>{title}</h2>
        {tag && <span className="cy-card-tag small muted">{tag}</span>}
      </header>
      {children}
    </article>
  );
}

export function ProposalView({ card, n, decide }: { card: ProposalCard; n: number; decide: Decide }) {
  const adopt = card.mode === "adopt";
  const first = card.stages[0];
  const names = new Map(card.stages.map((s) => [s.key, s.title]));
  const can = (a: ProposalCard["actions"][number]) => card.actions.includes(a);
  return (
    <Frame title={t("cycle.proposal.title", { n })} id="cycle-proposal"
      tag={<>{adopt && <>{t("cycle.proposal.adopt")} · </>}{t("cycle.proposal.version", { v: card.version })}</>}>
      {card.intro && <p className="cy-intro">{card.intro}</p>}
      {card.reply && (
        <div className="cy-reply">
          <p className="cy-k">{t("cycle.ceo_says")}</p>
          <p>{card.reply}</p>
        </div>
      )}
      {card.changes.length > 0 && (
        <div className="cy-changes">
          <p className="cy-k">{t("cycle.change.what")}</p>
          <ul>
            {card.changes.map((c) => (
              <li key={`${c.stage_key}-${c.kind}`}>
                <ChangeTag kind={c.kind} /> <span className="strong">{names.get(c.stage_key) ?? c.stage_key}</span>
                {c.why && <span className="muted"> · {c.why}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <ol className="cy-stages cy-stages-fit" style={columnWidths(card.stages)}>
        {card.stages.map((s, i) => <StageBlock key={s.key} stage={s} n={i + 1} />)}
      </ol>
      {card.revision_failed != null && (
        <p className="bad-t small" role="alert">{t("cycle.revise.failed")} <span className="muted">{card.revision_failed}</span></p>
      )}
      {card.revising ? (
        <Waiting title={t("cycle.revise_wait.title")} downTitle={t("cycle.revise_wait.title_down")} expect={t("cycle.draft.expect")} since={card.since} slowAfter={5 * 60}
          slow={t("cycle.draft.slow")} />
      ) : (
        <div className="cy-actions">
          {can("start_cycle") && (
            <ActionButton className="btn-primary" run={() => decide(card.cycle_id, { kind: "start_cycle", version: card.version })}>
              {adopt || !first ? t("cycle.start.adopt") : t("cycle.start.new", { title: first.title })}
            </ActionButton>
          )}
          {can("retry_proposal") && (
            <ActionButton className="btn-primary" run={() => decide(card.cycle_id, { kind: "retry_proposal", version: card.version })}>
              {t("cycle.retry")}
            </ActionButton>
          )}
          {can("revise") && (
            <Revise send={(note) => decide(card.cycle_id, { kind: "revise", note, base_version: card.version })} />
          )}
        </div>
      )}
      {!card.revising && can("start_cycle") && <p className="small muted cy-foot">{t(adopt ? "cycle.start.adopt_hint" : "cycle.start.new_hint")}</p>}
    </Frame>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  return items.length ? (
    <div>
      <p className="cy-k">{title}</p>
      <ul className="cy-list">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>
    </div>
  ) : null;
}

const CONFIDENCE_TONE = { high: "ok", mid: "run", low: "warn" } as const;

/** The summary a stage ends with. Every number in it is the server's own count. */
export function Summary({ summary }: { summary: SummaryView }) {
  const facts = [
    t("cycle.gate.tasks", { done: summary.tasks.done, total: summary.tasks.total }),
    summary.tasks.failed ? t("cycle.gate.failed", { n: summary.tasks.failed }) : null,
    t("cycle.gate.spent", { usd: usd(summary.cost_usd) }),
  ].filter(Boolean);
  return (
    <div className="cy-summary">
      <p className="cy-headline">{summary.headline}</p>
      <p className="small muted num">{facts.join(" · ")}</p>
      {summary.by === "server" && summary.note && <p className="small muted">{summary.note}</p>}
      <List title={t("cycle.gate.done")} items={summary.done} />
      <List title={t("cycle.gate.not_done")} items={summary.not_done} />
      {summary.claims.length > 0 && (
        <div>
          <p className="cy-k">{t("cycle.gate.claims")}</p>
          <ul className="cy-claims">
            {summary.claims.map((c, i) => (
              <li key={i}>
                <p>{c.claim}</p>
                <p className="cy-fine">
                  <span className={`tag t-${CONFIDENCE_TONE[c.confidence]}`}>{t(`cycle.confidence.${c.confidence}`)}</span>
                  {c.capped && <span className="muted"> {t("cycle.confidence.capped")}</span>}
                  {c.evidence_ids.length > 0 && <span className="muted"> {t("cycle.gate.evidence_n", { n: c.evidence_ids.length })} <EvRefs ids={c.evidence_ids} marks /></span>}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      <List title={t("cycle.gate.needs")} items={summary.needs_owner} />
    </div>
  );
}

function Parked({ items }: { items: WaitingItem[] }) {
  return (
    <ul className="cy-list">
      {items.map((w) => (
        <li key={`${w.kind}-${w.id}`}><span className="muted">{t(`cycle.waiting.${w.kind}`)} · </span>
          {w.kind === "decision"
            ? <PanelLink to={`/inbox?req=${w.id}`} target={{ key: "req", id: w.id }}>{w.title}</PanelLink> : w.title}
        </li>
      ))}
    </ul>
  );
}

export function GateView({ card, decide }: { card: GateCard; decide: Decide }) {
  const can = (a: GateCard["actions"][number]) => card.actions.includes(a);
  const s = card.summary;
  const next = card.next_stage;
  const nextEst = next ? estimateParts(next.estimate) : null;
  const openDecisions = card.waiting.filter((w) => w.kind === "decision").length;
  const showNext = can("start_next") && !!next && !!nextEst;
  const showFinish = can("finish_cycle");
  const showRevise = can("revise") && card.proposal_version > 0;
  const unsure = (s?.claims ?? []).some((c) => c.confidence !== "high");
  const watch = unsure || (s?.change_suggestions.length ?? 0) > 0 || (s?.needs_owner.length ?? 0) > 0 || card.waiting.length > 0
    || (showNext && openDecisions > 0);
  return (
    <Frame title={t("cycle.gate.title", { stage: card.stage_title })} id="cycle-gate"
      tag={card.round > 1 ? t("cycle.gate.round", { n: card.round }) : undefined}>
      <div className="gv">
        {card.reply && (
          <div className="cy-reply">
            <p className="cy-k">{t("cycle.ceo_says")}</p>
            <p>{card.reply}</p>
          </div>
        )}
        {s ? (
          <div className="gv-lead">
            <p className="cy-headline">{s.headline}</p>
            {s.by === "server" && s.note && <p className="small muted">{s.note}</p>}
          </div>
        ) : <p className="muted">{t("cycle.gate.lead")}</p>}

        <div className="gv-options">
          {showNext && next && nextEst && (
            <section className="gv-opt">
              <p className="gv-opt-k">{t("cycle.gate.next", { title: next.title })}</p>
              <p>{next.doing}</p>
              <p className="small muted num">{nextEst.text}{nextEst.basis && ` · ${nextEst.basis}`}</p>
              {s?.next_reason && <p className="small muted">{s.next_reason}</p>}
              <div className="gv-opt-act">
                <ActionButton className="btn-primary" run={() => decide(card.cycle_id, { kind: "start_next", after_stage_id: card.stage_id, version: card.proposal_version })}>
                  {t("cycle.gate.start_next", { title: next.title })}
                </ActionButton>
              </div>
            </section>
          )}
          {showFinish && (
            <section className="gv-opt">
              <p className="gv-opt-k">{t("cycle.gate.finish")}</p>
              <p className="small muted">{t("cycle.gate.finish_hint")}</p>
              <div className="gv-opt-act">
                <ActionButton className="btn-primary" run={() => decide(card.cycle_id, { kind: "finish_cycle", after_stage_id: card.stage_id })}>
                  {t("cycle.gate.finish")}
                </ActionButton>
              </div>
            </section>
          )}
          {showRevise && (
            <section className="gv-opt">
              <p className="gv-opt-k">{t("cycle.gate.revise")}</p>
              <div className="gv-opt-act">
                <Revise send={(note) => decide(card.cycle_id, { kind: "revise", note, base_version: card.proposal_version })} />
              </div>
            </section>
          )}
        </div>

        {watch && (
          <div className="gv-watch">
            <p className="gv-h">{t("cycle.viz.watch")}</p>
            {s && <List title={t("cycle.gate.needs")} items={s.needs_owner} />}
            {s && unsure && <UnsureRows claims={s.claims} />}
            {s && <List title={t("cycle.gate.suggest")} items={s.change_suggestions} />}
            {card.waiting.length > 0 && (
              <div>
                <p className="cy-k">{t("cycle.gate.waiting")}</p>
                <Parked items={card.waiting} />
              </div>
            )}
            {showNext && openDecisions > 0 && <p className="small warn-t">{t("cycle.gate.open_stay", { n: openDecisions })}</p>}
          </div>
        )}

        {s && (s.claims.length > 0 || s.done.length > 0 || s.not_done.length > 0) && (
          <details className="gv-more">
            <summary>{t("cycle.gate.claims")}</summary>
            <ClaimRows claims={s.claims} />
            <div className="gv-two">
              <Checks title={t("cycle.gate.done")} items={s.done} done />
              <Checks title={t("cycle.gate.not_done")} items={s.not_done} done={false} />
            </div>
          </details>
        )}
        {s && <Foot s={s} />}

        {(can("retry_stage") || can("stop_here")) && (
          <div className="gv-end">
            {can("retry_stage") && (
              <ActionButton className="btn-ghost" run={() => decide(card.cycle_id, { kind: "retry_stage", stage_id: card.stage_id })}>
                {t("cycle.gate.retry")}
              </ActionButton>
            )}
            {can("stop_here") && (
              <ActionButton className="danger" confirm={t("cycle.gate.stop_yes")}
                run={() => decide(card.cycle_id, { kind: "stop_here", after_stage_id: card.stage_id })}>
                {t("cycle.gate.stop")}
              </ActionButton>
            )}
            {can("stop_here") && <p className="small muted">{t("cycle.gate.stop_confirm")}</p>}
          </div>
        )}
      </div>
    </Frame>
  );
}

export function StoppedView({ card, decide }: { card: StoppedCard; decide: Decide }) {
  return (
    <Frame title={t("cycle.stopped.title", { stage: card.stage_title })}>
      <p className="muted">{t("cycle.stopped.lead")}</p>
      {card.waiting.length > 0 && <Parked items={card.waiting} />}
      <div className="cy-actions">
        <ActionButton className="btn-primary" run={() => decide(card.cycle_id, { kind: "resume_stage", stage_id: card.stage_id })}>
          {t("cycle.stopped.resume")}
        </ActionButton>
        <ActionButton className="danger" confirm={t("cycle.gate.stop_yes")}
          run={() => decide(card.cycle_id, { kind: "stop_here", after_stage_id: card.stage_id })}>
          {t("cycle.gate.stop")}
        </ActionButton>
      </div>
    </Frame>
  );
}

/** Decisions of the running stage's work. They are items of the owner's list, so each opens in the decisions screen. */
export function DecisionList({ cards }: { cards: DecisionCard[] }) {
  return (
    <article className="box cy-card">
      <header className="cy-card-h"><Chip tone="warn">{t("cycle.waiting.title")}</Chip><h3>{t("cycle.decision.title")}</h3></header>
      <ul className="cy-decisions">
        {cards.map((d) => {
          const to = refHref("REQ", d.request_id, d.project_id) ?? "/inbox";
          return (
            <li key={d.request_id}>
              <PanelLink className="grow" to={to} target={{ key: "req", id: d.request_id }}>{d.title}</PanelLink>
              {d.blocking && <span className="tag t-warn">{t("cycle.decision.blocking")}</span>}
              <PanelLink className="small nowrap" to={to} target={{ key: "req", id: d.request_id }} label={`${t("cycle.decision.open")}: ${d.title}`}>
                {t("cycle.decision.open")} <Icon name="arrow" /></PanelLink>
            </li>
          );
        })}
      </ul>
    </article>
  );
}
