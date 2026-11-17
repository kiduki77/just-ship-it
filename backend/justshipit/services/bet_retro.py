"""The retro of a bet that ended: one CEO call, then the server's checks, then the bet settles.

The call is owner-facing (`retro`), holds no transaction, and waits instead of failing when a usage cap is hit: a
`MustWait` out of it leaves the bet in REVIEW and the job runs again later. What the model returns is a judgement.
The server supplies the hypothesis and the numbers (`metrics.observed`), and it checks everything else the model
claims before it takes effect:

- a cause that cites an id the model was not shown loses its ids and drops to low confidence;
- a quote of a user must appear, word for word, in what that person said;
- a next bet is in scope only if it cites a brief line the model was shown, and the CEO picks in-scope bets only as far as
  free bet slots go; bets beyond the brief's scope become decision cards for the owner;
- a channel is dropped only if `promotion.droppable` agrees;
- `routine` is refused for a run that failed or hit its budget.

With fewer than 30 people at first value or beyond, statistics are noise, so the retro is three lines of what people
said (`PeopleRetroOut`). The bet then ends in DONE, or STOPPED when the verdict is stop. Starting the next bet is the
owner's: it spends the subscription, so the CEO may pick but not start.

The prompt is in English and the output-language rule decides the language the CEO writes in. What the server writes
for the owner (notes, status reasons, cards, the activity line) is in `lang`, which the job handler reads once.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.agents.executor import run_agent
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import (
    AgentRequestDraft,
    CardMetric,
    CardOption,
    DecisionCard,
    HumanDecision,
    RequestType,
    RiskLevel,
)
from justshipit.contracts.retro import BetRetroOut, NextBet, PeopleRetroDraft, PeopleRetroOut, RetroDraft
from justshipit.db.models import Bet, Business, Decision, MetricPoint, OwnerTask, Project
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.services import (
    activity,
    app_settings,
    bet_loop,
    channels,
    gate,
    metrics,
    owner_tasks,
    people_link,
    promotion,
    requests,
    roadmap,
)
from justshipit.services.fence import fenced
from justshipit.services.requests import ROUTINE_CARD
from justshipit.settings import limits, settings

log = logging.getLogger(__name__)

MIN_PEOPLE = 30
SCOPE_SECTIONS = ("one_liner", "customer_problem", "goal_deadline", "scope")
PRIOR_SECTIONS = SCOPE_SECTIONS + ("path", "success_stop", "red_lines")
QUOTES_SHOWN = 30
ROUTINE_OK = ("run_done", "time_limit", "owner")


class RetroInvalid(ValueError):
    """The model's answer cannot settle the bet (no next bet). The job fails and the queue tries again."""


def mode_for(s: Session, business_id: int) -> str:
    """Numbers while 30 or more people reached first value, three lines of people's words before that."""
    reached = people_link.provider().north_star(s, business_id)["first_value"]
    return "stats" if reached >= MIN_PEOPLE else "people_quotes"


@dataclass
class Packet:
    prompt: str
    mode: str
    bet_id: int
    business_id: int
    project_id: int | None
    hypothesis: str
    observed: metrics.Observed
    evidence: set[str]
    scope_lines: set[str]
    quotes: dict[str, list[str]]
    free_slots: int


@lru_cache
def _system() -> str:
    return (settings().prompts_dir / "bet_retro.md").read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _obs_lines(observed: metrics.Observed) -> list[str]:
    lines = []
    for stage in metrics.STAGES:
        rows = observed.by_stage[stage]
        lines.append(f"### {stage}")
        lines += [f"- [{o.evidence_id}] {o.name}: {o.value:g} ({o.basis})" for o in rows] or ["- no numbers"]
    return lines + ["### Missing data"] + ([f"- {m}" for m in observed.missing] or ["- none"])


def _channel_table(s: Session, business_id: int, now: datetime, lang: Lang) -> list[str]:
    rows = []
    for key in list(channels.registry()):
        done = [p for p in promotion.posts(s, business_id) if p.channel == key and p.status == "DONE"]
        if done:
            ok, why = promotion.droppable(s, business_id, key, now, lang=lang)
            rows.append(f"- {key}: {len(done)} posts, {sum(1 for p in done if p.material_keys)} with new material, "
                        f"first post {(now - min(p.at for p in done)).days} days ago. "
                        f"Droppable: {'yes' if ok else 'no'} ({why})")
    return rows or ["- no posts"]


def free_slots(s: Session, business_id: int, *, lock: bool = False) -> int:
    """How many more bets the CEO may pick: the cap less the bets running and the ones already picked and waiting.
    `lock` counts under the business row lock, which the retro takes before it picks."""
    picked = s.scalar(select(func.count()).select_from(Bet).where(Bet.business_id == business_id,
                                                                  Bet.status == "PICKED")) or 0
    return max(0, app_settings.effective().bets.max_running - roadmap.running_count(s, business_id, lock=lock) - picked)


def build_packet(s: Session, bet: Bet, now: datetime, *, lang: Lang) -> Packet:
    business = s.get(Business, bet.business_id)
    run = s.scalar(select(Project).where(Project.bet_id == bet.id))
    mode = mode_for(s, bet.business_id)
    metrics.snapshot_people(s, bet.business_id, now)
    window_start, window_close = bet.started_at or bet.created_at, bet.finished_at or now
    observed = metrics.observed(s, bet.business_id, window_start, window_close, lang=lang)
    evidence = {o.evidence_id for rows in observed.by_stage.values() for o in rows}
    hypothesis = (bet.question or bet.pitch).strip()

    brief = roadmap.approved_brief(s, bet.business_id)
    lines, scope_lines = [], set()
    for sec in (brief.sections if brief else []):
        if sec["key"] not in PRIOR_SECTIONS:
            continue
        for line in sec["lines"]:
            if line["provenance"] in roadmap.SETTLED:
                lines.append(f"- [{line['id']}] ({sec['title']}, {line['provenance']}) {line['text']}")
                if sec["key"] in SCOPE_SECTIONS:
                    scope_lines.add(line["id"])

    people = people_link.provider()
    counts = people.north_star(s, bet.business_id)
    quotes, quote_lines = {}, []
    if mode == "people_quotes":
        for q in people.quotes(s, bet.business_id, QUOTES_SHOWN):
            quotes.setdefault(q.ref, []).append(q.text)
            quote_lines.append(f"- [{q.ref}] ({q.stage})\n{fenced(f'{q.ref} said', q.text)}")
            evidence.add(q.ref)

    posted = []
    for t in s.scalars(select(OwnerTask).where(OwnerTask.business_id == bet.business_id, OwnerTask.kind == "post",
                                              OwnerTask.created_at >= window_start).order_by(OwnerTask.id)):
        ref = owner_tasks.display_id(t.id)
        evidence.add(ref)
        posted.append(f"- [{ref} · {t.status}]\n"
                      + fenced(f"{ref} post and result", f"{t.title[:80]}: {owner_tasks.result_text(t.result, lang=lang) or 'no result'}"))

    decision = s.scalar(select(Decision).where(Decision.project_id == run.id).order_by(Decision.id.desc())) if run else None
    cap, free = app_settings.effective().bets.max_running, free_slots(s, bet.business_id)
    record = "\n".join([
        f"Title: {bet.title}", f"Business: B-{business.id} {business.name}", f"Pitch: {bet.pitch}",
        f"Hypothesis: {hypothesis}", f"Audience: {bet.audience or 'not set'}",
        f"Success signal: {bet.exit_signal or 'not set'}", f"Stop condition: {bet.circuit_breaker or 'not set'}"])
    head = [
        f"## Experiment BET-{bet.id}", fenced("experiment record", record),
        f"Limits: {bet.appetite_days or 'no'} days, {bet.budget_usd or 'no'} USD equivalent of usage",
        f"Ended because: {bet.end_cause or 'unknown'}",
        (f"Reactions collected: {window_start:%Y-%m-%d} to {window_close:%Y-%m-%d} "
         f"({(window_close - window_start).days} days)"),
        f"Run: {run.status + f', spent {run.spent_usd:.2f} USD equivalent' if run else 'none (the owner did it)'}",
        fenced("run error", run.error[:200]) if run and run.error else "",
        fenced("run's last decision", f"{decision.decision} · {decision.rationale[:400]}") if decision else "",
    ]
    body = [
        "## Observations (numbers the server counted)", *_obs_lines(observed),
        "## People", f"People list: contacted {counts['contacted']}, talked {counts['talked']}, "
                     f"first_value {counts['first_value']}, repeat {counts['repeat']}"
                     + ("" if people.available else " (no people list is connected)"),
        *(["### What people said", *(quote_lines or ["- nothing yet"])] if mode == "people_quotes" else []),
        "## Posts the owner made", *(posted or ["- none"]),
        "## Posts by channel", *_channel_table(s, bet.business_id, now, lang),
        "## Plan lines", *(lines or ["- no settled lines"]),
        f"## Next experiment slots: the Project Manager may pick {free} now (at most {cap} at once)",
        f"Mode: {mode}",
    ]
    prompt = "\n".join([x for x in head if x] + [""] + body)
    return Packet(prompt, mode, bet.id, bet.business_id, run.id if run else None, hypothesis, observed, evidence,
                  scope_lines, quotes, free)


# ------------------------------------------------------------------ checking the model's answer

def _check_causes(out: RetroDraft, evidence: set[str], notes: list[str], lang: Lang) -> None:
    for c in out.causes:
        kept = [e for e in c.evidence_ids if e in evidence]
        if len(kept) != len(c.evidence_ids):
            notes.append(msg("retro.note.cause_ids", lang, cause=c.cause[:40]))
        c.evidence_ids = kept
        if not kept:
            c.confidence = "low"


def _check_quotes(out: PeopleRetroDraft, known: dict[str, list[str]], notes: list[str], lang: Lang) -> None:
    kept = []
    for q in out.users_said:
        if any(_norm(q.quote) and _norm(q.quote) in _norm(t) for t in known.get(q.person_ref, [])):
            kept.append(q)
        else:
            notes.append(msg("retro.note.quote_not_found", lang, person=q.person_ref))
    out.users_said = kept
    if not kept:
        notes.append(msg("retro.note.no_quotes", lang))


def _first_value_seen(s: Session, business_id: int) -> bool:
    """Whether anyone reached first value or repeat use: a person on the list at that stage, or a metric point there. The
    owner's tally of people not on the list does not count toward the goal."""
    counts = people_link.provider().north_star(s, business_id)
    if counts["first_value"] >= 1 or counts["repeat"] >= 1:
        return True
    return s.scalar(select(MetricPoint.id).where(MetricPoint.business_id == business_id,
                                                MetricPoint.stage.in_(("first_value", "repeat")),
                                                MetricPoint.metric != "heard_tally", MetricPoint.value > 0).limit(1)) is not None


def _verdict(s: Session, bet: Bet, out: RetroDraft | PeopleRetroDraft, notes: list[str], lang: Lang) -> str:
    """The verdict that takes effect. `routine` spends money on a schedule, so the server allows it only for a run that
    ended well, with someone at first value or repeat use, and while the business keeps fewer routines than
    `bets.max_routines`. Otherwise it becomes `continue` and the notes say why. Allowed or not, a routine is only
    proposed: the owner decides on a card."""
    if out.verdict != "routine":
        return out.verdict
    if bet.end_cause not in ROUTINE_OK:
        notes.append(msg("retro.note.routine_after_failure", lang))
        out.routine = None
        return "change"
    cap = app_settings.effective().bets.max_routines
    if not _first_value_seen(s, bet.business_id):
        notes.append(msg("retro.note.routine_no_value", lang))
    elif len(bet_loop.active_routines(s, bet.business_id)) + len(bet_loop.pending_routine_cards(s, bet.business_id)) >= cap:
        notes.append(msg("retro.note.routine_cap", lang, n=cap))
    else:
        if out.routine is None:
            notes.append(msg("retro.note.routine_default_every", lang))
        return "routine"
    out.routine = None
    return "continue"


def _routine_card(s: Session, bet: Bet, out: RetroDraft | PeopleRetroDraft, notes: list[str], lang: Lang) -> int:
    """The decision card that asks the owner whether the bet becomes a routine. Returns the request id."""
    every = out.routine.every_days if out.routine else 7
    what = out.routine.what if out.routine else bet.pitch
    per_run = bet.budget_usd or float(limits()["project_budget_usd"])
    rounds = math.ceil(30 / every)
    counts = people_link.provider().north_star(s, bet.business_id)
    seen = msg("retro.routine.seen", lang, got=counts["first_value"], back=counts["repeat"])
    yes, no = msg("retro.verdict.routine", lang), msg("retro.routine.no", lang)
    from_retro = msg("retro.from_retro", lang)
    draft = AgentRequestDraft(
        type=RequestType.PROPOSAL, title=msg("retro.routine.title", lang, title=bet.title)[:300],
        summary=msg("retro.routine.summary", lang, bet=bet.title[:100], what=what, n=every, seen=seen),
        rationale=out.verdict_reason, options=[yes, no], recommended_option=yes,
        action_kind=ROUTINE_CARD, action_payload_json=None, estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM,
        card=DecisionCard(
            headline=msg("retro.routine.headline", lang),
            owner_does=msg("retro.routine.owner_does", lang),
            why=msg("retro.routine.why", lang, seen=seen),
            metrics=[CardMetric(label=msg("retro.routine.evidence", lang), value=seen,
                                basis=msg("retro.routine.people_list", lang)),
                     CardMetric(label=msg("retro.routine.every", lang),
                                value=msg("retro.routine.every_value", lang, n=every), basis=from_retro),
                     CardMetric(label=msg("retro.routine.per_run", lang), value=msg("retro.usd", lang, usd=f"{per_run:g}"),
                                basis=msg("retro.routine.per_run_bet" if bet.budget_usd else "retro.routine.per_run_default",
                                          lang)),
                     CardMetric(label=msg("retro.routine.month", lang),
                                value=msg("retro.usd", lang, usd=f"{per_run * rounds:g}"),
                                basis=msg("retro.routine.month_basis", lang, usd=f"{per_run:g}", n=rounds))],
            options=[CardOption(label=yes, verdict=HumanDecision.APPROVE,
                                summary=msg("retro.routine.yes_summary", lang, n=every),
                                evidence_ids=[], risks=[msg("retro.routine.yes_risk", lang)], recommended=True),
                     CardOption(label=no, verdict=HumanDecision.REJECT,
                                summary=msg("retro.routine.no_summary", lang),
                                evidence_ids=[], risks=[], recommended=False)],
            recommendation=out.verdict_reason,
            reversible=True))
    req = requests.create(s, draft, from_agent="project_manager", project_id=None, origin=ServerOrigin(OriginKind.BET_ROUTINE, bet.id),
                          system=True, dedupe=False,
                          action_params={"bet_id": bet.id, "every_days": every, "what": what[:1000]})
    notes.append(msg("retro.note.routine_card", lang))
    return req.id


def _decision_card(bet: Bet, nb: NextBet, bet_row: Bet, lang: Lang) -> AgentRequestDraft:
    yes, no, from_retro = msg("retro.scope.yes", lang), msg("retro.scope.no", lang), msg("retro.from_retro", lang)
    return AgentRequestDraft(
        type=RequestType.PROPOSAL, title=msg("retro.scope.title", lang, title=nb.title)[:300],
        summary=msg("retro.scope.summary", lang, bet=bet.title[:100], pitch=nb.pitch),
        rationale=nb.reason, options=[yes, no], recommended_option=yes,
        action_kind="project.direction_change", action_payload_json=None, estimated_cost_usd=None,
        risk_level=RiskLevel.MEDIUM,
        card=DecisionCard(
            headline=msg("retro.scope.headline", lang, title=nb.title),
            owner_does=msg("retro.scope.owner_does", lang),
            why=msg("retro.scope.why", lang),
            metrics=[CardMetric(label=msg("retro.scope.days", lang),
                                value=msg("retro.scope.days_value", lang, n=nb.appetite_days), basis=from_retro),
                     CardMetric(label=msg("retro.scope.budget", lang),
                                value=msg("retro.usd", lang, usd=f"{nb.budget_usd:g}") if nb.budget_usd
                                else msg("retro.scope.no_budget", lang),
                                basis=from_retro)],
            options=[CardOption(label=yes, verdict=HumanDecision.APPROVE,
                                summary=msg("retro.scope.yes_summary", lang),
                                evidence_ids=[], risks=[msg("retro.scope.yes_risk", lang)], recommended=True),
                     CardOption(label=no, verdict=HumanDecision.REJECT, summary=msg("retro.scope.no_summary", lang),
                                evidence_ids=[], risks=[], recommended=False)],
            recommendation=nb.reason, reversible=True),
    )


def _next_bets(s: Session, bet: Bet, out: RetroDraft | PeopleRetroDraft, packet: Packet, notes: list[str],
               lang: Lang) -> list[int]:
    """Create the proposed bets. In-scope ones are picked while slots are free; the rest wait as candidates, and the
    ones outside the brief's scope also go to the owner as a decision card."""
    if not out.next_bets:
        raise RetroInvalid(msg("retro.invalid.no_next_bet", lang))
    free, made = free_slots(s, bet.business_id, lock=True), []
    for nb in out.next_bets[:3]:
        in_scope = nb.in_scope and bool(set(nb.brief_line_ids) & packet.scope_lines)
        row = roadmap.create_bet(s, bet.business_id, title=nb.title, pitch=nb.pitch, origin="project_manager", audience=nb.audience,
                                 question=nb.question, appetite_days=nb.appetite_days, budget_usd=nb.budget_usd,
                                 exit_signal=nb.exit_signal, circuit_breaker=nb.circuit_breaker, lang=lang)
        row.source_bet_id = bet.id
        made.append(row.id)
        if in_scope and free > 0:
            roadmap.move_bet(s, row.id, roadmap.BetStatus.PICKED,
                             reason=msg("retro.reason.picked", lang, bet=bet.title[:100], reason=nb.reason), lang=lang)
            free -= 1
        elif in_scope:
            row.status_reason = msg("retro.reason.no_slot", lang, bet=bet.title[:100])
        else:
            req = requests.create(s, _decision_card(bet, nb, row, lang), from_agent="project_manager", project_id=packet.project_id,
                                  origin=ServerOrigin(OriginKind.BET_SCOPE, row.id), system=True, dedupe=False,
                                  action_params={"bet_id": row.id})
            row.status_reason = msg("retro.reason.out_of_scope", lang)
            notes.append(msg("retro.note.out_of_scope", lang, title=nb.title))
    return made


def apply(s: Session, bet_id: int, out: RetroDraft | PeopleRetroDraft, packet: Packet, now: datetime | None = None,
          *, lang: Lang) -> Bet:
    """Check the answer, store the retro, create the next bets and settle the bet. A bet already settled is returned
    as it is, so a job that runs twice stores one retro."""
    bet = s.get(Bet, bet_id, with_for_update=True)
    if bet is None or bet.status != roadmap.BetStatus.REVIEW.value or bet.retro is not None:
        return bet
    notes: list[str] = []
    verdict = _verdict(s, bet, out, notes, lang)
    if packet.mode == "stats":
        _check_causes(out, packet.evidence, notes, lang)
    else:
        _check_quotes(out, packet.quotes, notes, lang)
    for key in out.drop_channels:
        ok, why = promotion.droppable(s, bet.business_id, key, now, lang=lang)
        if key in channels.registry() and ok:
            promotion.drop_channel(s, bet.business_id, key, bet.id, why)
        else:
            why = why if key in channels.registry() else msg("retro.note.channel_unknown", lang)
            notes.append(msg("retro.note.channel_kept", lang, channel=key, why=why))
    if gate.managed(s, bet.business_id):
        # In a cycle the next work is proposed one way only: as stages of the CEO's next proposal, which reads them here.
        made = []
        notes.append(msg("retro.note.to_cycle", lang))
    else:
        made = _next_bets(s, bet, out, packet, notes, lang)
        if verdict == "routine":
            _routine_card(s, bet, out, notes, lang)
    if packet.mode == "stats":
        retro = BetRetroOut(**out.model_dump(exclude={"verdict"}), verdict=verdict, hypothesis=packet.hypothesis,
                            observed=packet.observed, notes=notes)
    else:
        retro = PeopleRetroOut(**out.model_dump(exclude={"verdict"}), verdict=verdict, notes=notes)
    bet.verdict, bet.retro = verdict, retro.model_dump(mode="json")
    bet.outcome = (retro.did if isinstance(retro, PeopleRetroOut) else out.verdict_reason)[:1000]
    roadmap.move_bet(s, bet.id, roadmap.BetStatus.STOPPED if verdict == "stop" else roadmap.BetStatus.DONE,
                     reason=out.verdict_reason[:1000], lang=lang)
    activity.record(s, verb="bet_retro", entity="bet", entity_id=bet.id, project_id=packet.project_id,
                    business_id=bet.business_id, actor="project_manager",
                    summary=msg("retro.activity", lang, title=bet.title[:100],
                                verdict=msg(f"retro.verdict.{verdict}", lang), n=len(made)),
                    data={"verdict": verdict, "mode": packet.mode, "next_bets": made, "notes": notes})
    return bet


def run(bet_id: int, *, lang: Lang) -> None:
    """The `bet_retro` job. The model call holds no transaction and a `MustWait` out of it leaves the bet in REVIEW."""
    now = datetime.now(UTC)
    with session_scope() as s:
        bet = s.get(Bet, bet_id)
        if bet is None or bet.status != roadmap.BetStatus.REVIEW.value or bet.retro is not None:
            return
        packet = build_packet(s, bet, now, lang=lang)
    model = RetroDraft if packet.mode == "stats" else PeopleRetroDraft
    out = run_agent("project_manager", "retro", packet.prompt, model, project_id=packet.project_id,
                    business_id=packet.business_id, system_override=_system())
    with session_scope() as s, activity.as_actor("project_manager"):
        apply(s, bet_id, out, packet, now, lang=lang)
