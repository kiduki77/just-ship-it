import { ReactNode, useState } from "react";
import { ItemLink } from "../components/panel";
import { Link } from "react-router-dom";
import { post } from "../api";
import { BoardCard, Box, Chip, Hint, Meter, Section, Tone } from "../components/kit";
import { toneOf } from "../components/ui";
import { PlainKey, t, tx } from "../i18n";
import { Clamp, ConfirmBox, Fact, Fail, dayText, useDo } from "./parts";
import { RetroView } from "./Retro";
import { currentAgent } from "../labels";
import { Bet, budgetLink, causeText, daysLeft, Roadmap, Routine, runStatus, usd } from "./types";

/** Why a bet wants a look. The server sends the codes; the owner reads the words, and colour is never the only sign. */
export const ATTENTION: Record<string, [PlainKey, Tone]> = {
  owner_cleanup: ["business-bets.attention.owner_cleanup", "warn"], retro_stuck: ["business-bets.attention.retro_stuck", "bad"],
  retro_budget: ["business-bets.attention.retro_budget", "warn"], run_failed: ["business-bets.attention.run_failed", "bad"],
  over_budget: ["business-bets.attention.over_budget", "warn"], over_appetite: ["business-bets.attention.over_appetite", "warn"],
  run_finished: ["business-bets.attention.run_finished", "ok"],
};

const STATUS_TAG: Partial<Record<Bet["status"], [PlainKey, Tone]>> = {
  LISTENING: ["business-bets.status.LISTENING", "info"], REVIEW: ["business-bets.status.REVIEW", "run"],
  STOPPED: ["business-bets.status.STOPPED", "muted"], DROPPED: ["business-bets.status.DROPPED", "muted"],
};

const LIVE = new Set(["run"]);
const firstLine = (text: string) => text.split("\n").find((l) => l.trim())?.trim() ?? "";
const originKey = (b: Bet): PlainKey => (b.source_bet_id ? "business-bets.tag.from_review" : "business-bets.tag.ceo");

function Tags({ b }: { b: Bet }) {
  const status = STATUS_TAG[b.status];
  return (
    <>
      {status && <span className={`tag t-${status[1]}`}>{t(status[0])}</span>}
      {b.attention.filter((a) => ATTENTION[a]).map((a) => <span key={a} className={`tag t-${ATTENTION[a][1]}`}>{t(ATTENTION[a][0])}</span>)}
      {currentAgent(b.origin) === "project_manager" && <span className="tag t-info">{t(originKey(b))}</span>}
      {b.run_mode === "owner" && <span className="tag">{t("business-bets.tag.owner_runs")}</span>}
    </>
  );
}

function daysText(b: Bet): string | null {
  const left = daysLeft(b);
  if (left === null) return null;
  return left >= 0 ? t("business-bets.days_left", { n: left }) : t("business-bets.days_over", { n: -left });
}

/** A bet on the roadmap board: what it is, why it wants a look, what it spent against its budget, and how long is left. */
export function BetCard({ b, to }: { b: Bet; to: string }) {
  const spent = b.run?.spent_usd;
  const money = b.budget_usd ? `${usd(spent ?? 0)} / ${usd(b.budget_usd)}` : spent ? t("business-bets.spent", { usd: usd(spent) }) : null;
  const days = b.finished_at ? t("business-bets.ended_on", { date: dayText(b.finished_at) }) : daysText(b);
  return (
    <BoardCard to={to} tags={<Tags b={b} />} title={b.title}
      sub={b.question || firstLine(b.pitch)}
      live={!!b.run && LIVE.has(toneOf(b.run.status)) && !b.finished_at}
      foot={(money || days || b.run) ? <>
        {money && <span className="num" title={t("business-bets.money_title")}>{money}</span>}
        {b.run && <span className={runStatus(b.run).tone === "bad" ? "bad-t" : undefined}>{runStatus(b.run).text}</span>}
        {days && <span className="sp num">{days}</span>}
      </> : undefined} />
  );
}

function limitsText(b: Bet): string {
  const left = daysLeft(b);
  const n = b.appetite_days;
  const parts = [
    n ? (left === null ? t("business-bets.limit.days", { n }) : left >= 0 ? t("business-bets.limit.days_left", { n, left })
      : t("business-bets.limit.days_over", { n, over: -left })) : "",
    b.budget_usd ? (b.run ? t("business-bets.limit.usage_spent", { budget: usd(b.budget_usd), spent: usd(b.run.spent_usd) })
      : t("business-bets.limit.usage", { budget: usd(b.budget_usd) })) : "",
  ].filter(Boolean);
  return parts.join(" · ") || t("business-bets.limit.none");
}

function Details({ b, pitch }: { b: Bet; pitch?: boolean }) {
  const ask = b.question || (pitch ? "" : b.pitch);
  return (
    <dl className="biz-dl">
      {pitch && b.pitch && <Fact k={t("business-bets.fact.pitch")}><Clamp text={b.pitch} /></Fact>}
      {ask && <Fact k={t("business-bets.fact.question")}><Clamp text={ask} /></Fact>}
      <Fact k={t("business-bets.fact.limits")}>{limitsText(b)}</Fact>
      {b.exit_signal && <Fact k={t("business-bets.fact.exit_signal")}>{b.exit_signal}</Fact>}
      {b.circuit_breaker && <Fact k={t("business-bets.fact.circuit_breaker")}>{b.circuit_breaker}</Fact>}
      {b.run && (
        <Fact k={t("business-bets.fact.run")}>
          <Link to={`/projects/${b.run.project_id}`}>{b.run.name}</Link> · {runStatus(b.run).text}
        </Fact>
      )}
    </dl>
  );
}

function Row({ id, title, chips, action, children, wide }: {
  id?: string; title: ReactNode; chips?: ReactNode; action?: ReactNode; children?: ReactNode; wide?: ReactNode;
}) {
  return (
    <li className="biz-r" id={id}>
      <div className="biz-r-h">
        <p className="biz-r-t">
          {chips}
          <span className="biz-r-title">{title}</span>
        </p>
        {action && <div className="biz-r-a">{action}</div>}
      </div>
      {children}
      {wide}
    </li>
  );
}

const betId = (b: Bet) => b.ref.toLowerCase();

function RunningRow({ b }: { b: Bet }) {
  const flags = b.attention.filter((a) => ATTENTION[a]);
  return (
    <Row id={betId(b)} title={b.title}
      chips={<>
        {b.run_mode === "ai" && b.run
          ? <Chip tone={runStatus(b.run).tone as Tone}>{runStatus(b.run).text}</Chip>
          : <Chip>{t(b.run_mode === "owner" ? "business-bets.tag.owner_runs" : "business-bets.tag.preparing")}</Chip>}
        {flags.map((a) => <Chip key={a} tone={ATTENTION[a][1]}>{t(ATTENTION[a][0])}</Chip>)}
      </>}>
      <Details b={b} />
    </Row>
  );
}

function soFarText(w: NonNullable<Bet["window"]>): string {
  const { posts, owner_tasks, numbers, contacted } = w.so_far;
  return [t("business-bets.so_far.posts", { n: posts }), t("business-bets.so_far.todos", { n: owner_tasks }),
    t("business-bets.so_far.numbers", { n: numbers }),
    contacted === null ? "" : t("business-bets.so_far.contacted", { n: contacted })].filter(Boolean).join(" · ");
}

/** A bet whose run finished and that waits out its window for market response, with the owner's early retro. */
function ListeningRow({ b, reload }: { b: Bet; reload: () => void }) {
  const [armed, setArmed] = useState(false);
  const d = useDo();
  const w = b.window;
  const end = async () => {
    if (await d.run(() => post(`/bets/${b.id}/move`, { to: "REVIEW", reason: t("business-bets.reason.review_early") }))) reload();
  };
  if (!w) return null;
  return (
    <Row id={betId(b)} title={b.title}
      chips={<Chip tone="info">{t("business-bets.listening.chip", { n: w.days_left })}</Chip>}
      action={!armed && <button className="btn-ghost" onClick={() => setArmed(true)}>{t("business-bets.listening.review_now")}</button>}
      wide={armed && (
        <ConfirmBox confirmLabel={t("business-bets.listening.review_now")} busy={d.busy} error={d.error} onConfirm={end} onCancel={() => setArmed(false)}>
          <p>{t("business-bets.listening.confirm", { n: w.days_left })}</p>
          <p className="muted">{t("business-bets.listening.confirm_note")}</p>
        </ConfirmBox>
      )}>
      <p className="biz-lead">{t("business-bets.listening.lead", { date: dayText(w.ends_at) })}</p>
      <p className="biz-sub num">{t("business-bets.so_far", { facts: soFarText(w) })}</p>
      <Details b={b} />
    </Row>
  );
}

function LegacyRow({ b, reload }: { b: Bet; reload: () => void }) {
  const [armed, setArmed] = useState(false);
  const d = useDo();
  const end = async () => {
    if (await d.run(() => post(`/bets/${b.id}/move`, { to: "STOPPED", reason: t("business-bets.reason.legacy_closed") }))) reload();
  };
  return (
    <Row id={betId(b)} title={b.title} chips={<Chip tone="warn">{t("business-bets.attention.owner_cleanup")}</Chip>}
      action={!armed && <button className="btn-ghost" onClick={() => setArmed(true)}>{t("business-bets.legacy.close")}</button>}
      wide={armed && (
        <ConfirmBox confirmLabel={t("business-bets.legacy.close")} busy={d.busy} error={d.error} tone="danger" onConfirm={end} onCancel={() => setArmed(false)}>
          <p>{t("business-bets.legacy.confirm")}</p>
          <p className="muted">{t("business-bets.legacy.confirm_note")}</p>
        </ConfirmBox>
      )}>
      <p className="biz-lead">{t("business-bets.legacy.lead")}</p>
      <Details b={b} />
    </Row>
  );
}

function ReviewRow({ b, reload }: { b: Bet; reload: () => void }) {
  const stuck = b.attention.includes("retro_stuck");
  // A budget refused the last round and still has no room: another round would be refused too, so the way on is the budget.
  const refused = !b.retro && b.budget_stop ? b.budget_stop : null;
  const d = useDo();
  const redo = async () => {
    if (await d.run(() => post(`/bets/${b.id}/retro/redo`, {}))) reload();
  };
  const chip: [PlainKey, Tone] = b.retro ? ["business-bets.review.ready", "ok"] : refused ? ["business-bets.attention.retro_budget", "warn"]
    : stuck ? ["business-bets.attention.retro_stuck", "bad"] : ["business-bets.review.writing", "run"];
  const rest = b.retro ? "" : refused ? refused.says : t(stuck ? "business-bets.review.stuck" : "business-bets.review.ceo_writing");
  return (
    <Row id={betId(b)} title={b.title}
      chips={<Chip tone={chip[1]}>{t(chip[0])}</Chip>}
      action={refused ? <Link className="btn-primary" to={budgetLink(refused)}>{t("business-bets.raise_budget")}</Link>
        : !b.retro && stuck && <button className="btn-ghost" disabled={d.busy} onClick={redo}>{t(d.busy ? "business-bets.review.retrying" : "business-bets.review.retry")}</button>}>
      <p className="biz-lead">{causeText(b.end_cause) ?? t("business-bets.cause.unknown")}. {rest}</p>
      {b.retro && <RetroView r={b.retro} />}
      <Fail text={d.error} />
    </Row>
  );
}

const FINISHED_CHIP: Partial<Record<Bet["status"], PlainKey>> = {
  DONE: "business-bets.status.DONE", DROPPED: "business-bets.status.DROPPED",
};

function FinishedRow({ b }: { b: Bet }) {
  const [open, setOpen] = useState(false);
  const why = causeText(b.end_cause) ?? b.status_reason;
  return (
    <Row id={betId(b)} title={b.title}
      chips={<Chip tone={b.status === "DONE" ? "ok" : "muted"}>{t(FINISHED_CHIP[b.status] ?? "business-bets.status.STOPPED")}</Chip>}
      action={b.retro && <button className="btn-ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{t(open ? "common.fold" : "business-bets.finished.view_review")}</button>}>
      <p className="biz-lead">
        {b.finished_at ? t("business-bets.finished.ended_on", { date: dayText(b.finished_at) }) : t("business-bets.finished.ended")}
        {why ? ` · ${why}` : ""}
        {b.run ? ` · ${t("business-bets.spent", { usd: usd(b.run.spent_usd) })}` : ""}
        {!b.retro && ` · ${t("business-bets.finished.no_review")}`}
      </p>
      {open && b.retro && <RetroView r={b.retro} />}
    </Row>
  );
}

function CandidateRow({ b, plan, reload }: { b: Bet; plan: Roadmap["limits"]; reload: () => void }) {
  const [armed, setArmed] = useState(false);
  const d = useDo();
  const picked = b.status === "PICKED";
  const full = plan.running >= plan.max_running;
  const start = async () => {
    const ok = await d.run(async () => {
      if (!picked) await post(`/bets/${b.id}/move`, { to: "PICKED" });
      await post(`/bets/${b.id}/move`, { to: "RUNNING", reason: t("business-bets.reason.owner_started") });
    });
    if (ok) reload();
  };
  const pick = () => d.run(() => post(`/bets/${b.id}/move`, { to: "PICKED" })).then((ok) => ok && reload());
  return (
    <Row id={betId(b)} title={b.title}
      chips={<>
        <Chip tone={picked ? "run" : "muted"}>{t(picked ? "business-bets.tag.up_next" : "business-bets.tag.candidate")}</Chip>
        {currentAgent(b.origin) === "project_manager" && <Chip tone="info">{t(originKey(b))}</Chip>}
      </>}
      action={!armed && (
        <>
          {!picked && <button className="btn-ghost" disabled={d.busy} onClick={pick}>{t("business-bets.candidate.make_next")}</button>}
          <button className="btn-primary" onClick={() => setArmed(true)}>{t("business-bets.candidate.start")}</button>
        </>
      )}
      wide={armed && (
        <ConfirmBox confirmLabel={t("business-bets.candidate.start")} busy={d.busy || full} error={d.error} onConfirm={start} onCancel={() => setArmed(false)}>
          <p>{tx("business-bets.candidate.confirm", { title: <strong>{b.title}</strong> })}</p>
          <ul className="confirm-list">
            <li><span className="k">{t("business-bets.fact.limits")}</span> {t("business-bets.candidate.limits", { limits: limitsText(b) })}</li>
            <li>
              <span className="k">{t("business-bets.candidate.running_k")}</span>{" "}
              {t("business-bets.candidate.running", { running: plan.running, max: plan.max_running })}{" "}
              {full ? t("business-bets.candidate.full") : t("business-bets.candidate.after", { n: plan.running + 1 })}
            </li>
            {b.run_mode === "owner" && <li>{t("business-bets.candidate.owner_runs")}</li>}
            <li>{t("business-bets.candidate.outside_stage")} <Link to="/">{t("business-bets.candidate.see_stage")}</Link></li>
          </ul>
        </ConfirmBox>
      )}>
      <Details b={b} pitch />
      {!armed && <Fail text={d.error} />}
    </Row>
  );
}

function RoutineRow({ r, reload }: { r: Routine; reload: () => void }) {
  const [armed, setArmed] = useState(false);
  const d = useDo();
  const waiting = r.state === "proposed";
  const stopped = r.state === "stopped";
  const run = r.every_days ? t("business-bets.routine.every", { n: r.every_days }) : "";
  const cost = r.per_run_budget_usd ? t("business-bets.routine.per_run", { usd: usd(r.per_run_budget_usd) }) : "";
  const next = r.state === "active" && r.next_run_at ? t("business-bets.routine.next", { date: dayText(r.next_run_at) }) : "";
  const stop = async () => {
    if (await d.run(() => post(`/bets/${r.bet_id}/routine/stop`, {}))) reload();
  };
  return (
    <Row title={r.title}
      chips={<Chip tone={waiting ? "warn" : stopped ? "muted" : "ok"}>
        {t(waiting ? "business-bets.routine.waiting" : stopped ? "business-bets.routine.stopped" : "business-bets.routine.active")}
      </Chip>}
      action={waiting ? (r.request_id && <ItemLink className="btn-primary" to={`/inbox?req=${r.request_id}`}>{t("business-bets.routine.decide")}</ItemLink>)
        : !stopped && !armed && <button className="btn-ghost" onClick={() => setArmed(true)}>{t("business-bets.routine.stop")}</button>}
      wide={armed && (
        <ConfirmBox confirmLabel={t("business-bets.routine.stop")} busy={d.busy} error={d.error} tone="danger" onConfirm={stop} onCancel={() => setArmed(false)}>
          <p>{t("business-bets.routine.confirm")}</p>
        </ConfirmBox>
      )}>
      <p className="biz-lead">
        {t(waiting ? "business-bets.routine.lead_waiting" : stopped ? "business-bets.routine.lead_stopped" : "business-bets.routine.lead_active")}
      </p>
      {(run || cost || next) && <p className="biz-sub num">{[run, cost, next].filter(Boolean).join(" · ")}</p>}
    </Row>
  );
}

function Group({ title, count, note, children }: { title: string; count: number; note?: string; children: ReactNode }) {
  return (
    <Section title={title} count={count} actions={note ? <span className="small muted num">{note}</span> : undefined}>
      <Box pad={false}><ul className="biz-rows">{children}</ul></Box>
    </Section>
  );
}

export function betNeedsOwner(roadmap: Roadmap, routines: Routine[]): number {
  return roadmap.bets.filter((b) => b.legacy || b.attention.includes("retro_stuck") || b.attention.includes("retro_budget")).length
    + routines.filter((r) => r.state === "proposed").length;
}

/** A cycle's length the way the owner said it: whole weeks as weeks. */

function Limits({ roadmap, routines }: { roadmap: Roadmap; routines: Routine[] }) {
  const { limits, cycle } = roadmap;
  const active = routines.filter((r) => r.state === "active").length;
  return (
    <Section title={t("business-bets.limits.title")}>
      <Box pad={false}>
        <ul className="ilist">
          <li>
            <div className="grow">
              <p className="t1">{t("business-bets.limits.running")}</p>
              <Meter value={limits.running} max={limits.max_running} label={t("business-bets.limits.running")} />
            </div>
            <span className="end biz-end-v">{t("business-bets.limits.of", { n: limits.running, max: limits.max_running })}</span>
          </li>
          <li>
            <div className="grow"><p className="t1">{t("business-bets.limits.routines")}</p><p className="t2">{t("business-bets.limits.routines_note")}</p></div>
            <span className="end biz-end-v">{t("business-bets.limits.of", { n: active, max: limits.max_routines })}</span>
          </li>
          <li>
            <div className="grow">
              <p className="t1">{t("business-bets.limits.cycle")}</p>
              <p className="t2">{cycle ? t("business-bets.limits.cycle_note", { n: cycle.number, date: dayText(cycle.ends_on) })
                : t("business-bets.limits.no_cycle")}</p>
            </div>
            <span className="end biz-end-v">{cycle ? t("business-bets.days_left", { n: cycle.days_left }) : "—"}</span>
          </li>
        </ul>
      </Box>
    </Section>
  );
}

/** Bets in the order the owner meets them: what runs, what needs the owner, what could come next, what came out of it. */
export function BetsTab({ roadmap, routines, reload }: { roadmap: Roadmap; routines: Routine[]; reload: () => void }) {
  const [more, setMore] = useState(false);
  const by = (f: (b: Bet) => boolean) => roadmap.bets.filter(f);
  const running = by((b) => b.status === "RUNNING" && !b.legacy);
  const listening = by((b) => b.status === "LISTENING");
  const legacy = by((b) => b.legacy);
  const review = by((b) => b.status === "REVIEW");
  const inRoutine = new Set(routines.filter((r) => r.state !== "declined").map((r) => r.bet_id));
  const finished = by((b) => ["DONE", "STOPPED", "DROPPED"].includes(b.status) && !b.legacy && !inRoutine.has(b.id))
    .sort((a, c) => (c.finished_at ?? "").localeCompare(a.finished_at ?? ""));
  const candidates = [...by((b) => b.status === "PICKED"), ...by((b) => b.status === "PROPOSED")];
  const live = routines.filter((r) => r.state !== "declined");
  const { limits } = roadmap;

  if (!roadmap.bets.length && !live.length) {
    return (
      <div className="cols cols-main">
        <Hint title={t("business-bets.empty.title")}>{t("business-bets.empty.body")}</Hint>
        <Limits roadmap={roadmap} routines={routines} />
      </div>
    );
  }

  return (
    <div className="cols cols-main">
      <div>
        {!!legacy.length && (
          <Group title={t("business-bets.group.legacy")} count={legacy.length}>
            {legacy.map((b) => <LegacyRow key={b.id} b={b} reload={reload} />)}
          </Group>
        )}
        {(!!running.length || !listening.length) && (
          <Group title={t("business-bets.group.running")} count={running.length} note={t("business-bets.group.running_note", { n: limits.max_running })}>
            {running.length ? running.map((b) => <RunningRow key={b.id} b={b} />)
              : <li className="biz-r biz-none">{t("business-bets.group.running_none")}</li>}
          </Group>
        )}
        {!!listening.length && (
          <Group title={t("business-bets.group.listening")} count={listening.length} note={t("business-bets.group.listening_note")}>
            {listening.map((b) => <ListeningRow key={b.id} b={b} reload={reload} />)}
          </Group>
        )}
        {!!review.length && (
          <Group title={t("business-bets.group.review")} count={review.length}>
            {review.map((b) => <ReviewRow key={b.id} b={b} reload={reload} />)}
          </Group>
        )}
        <Group title={t("business-bets.group.candidates")} count={candidates.length}>
          {candidates.length ? candidates.map((b) => <CandidateRow key={b.id} b={b} plan={limits} reload={reload} />)
            : <li className="biz-r biz-none">{t("business-bets.group.candidates_none")}</li>}
        </Group>
        {!!finished.length && (
          <Group title={t("business-bets.group.finished")} count={finished.length}>
            {(more ? finished : finished.slice(0, 3)).map((b) => <FinishedRow key={b.id} b={b} />)}
            {finished.length > 3 && (
              <li className="biz-r biz-none">
                <button className="link" onClick={() => setMore(!more)}>{more ? t("common.fold") : t("business-bets.group.finished_more", { n: finished.length - 3 })}</button>
              </li>
            )}
          </Group>
        )}
      </div>
      <div>
        <Limits roadmap={roadmap} routines={routines} />
        <Section title={t("business-bets.group.routines")} count={live.length}>
          {live.length ? (
            <Box pad={false}><ul className="biz-rows">{live.map((r) => <RoutineRow key={r.bet_id} r={r} reload={reload} />)}</ul></Box>
          ) : (
            <Hint title={t("business-bets.group.routines_none")}>{t("business-bets.group.routines_none_body")}</Hint>
          )}
        </Section>
      </div>
    </div>
  );
}
