"""Approval Service: Agent Request Protocol, human responses, and scoped grants."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from justshipit.agents import roles
from justshipit.contracts import notify as notices
from justshipit.contracts import owner_text
from justshipit.contracts.handoff import FIX_QUESTION_KEY, SPEC_QUESTION_KEY
from justshipit.contracts.owner_text import one_line
from justshipit.contracts.requests import (
    POLICY_ORDER, AgentRequestDraft, DecisionCard, HumanDecision, HumanResponse, PolicyMode, PostEdit, RequestStatus,
    RequestType, RESOLVED_STATUSES, RiskLevel,
)
from justshipit.contracts.origins import Origin, OriginKind, RoundOrigin, ServerOrigin
from justshipit.contracts.work import TERMINAL_TASK_STATUSES, TaskStatus
from justshipit.contracts.work_path import (
    AnswerEffect, ExpiryPolicy, FollowedEffect, HoldPhase, HoldScope, LinkSource, Rel, TargetKind, wider,
)
from justshipit.db.models import (
    ActionExecution, AgentRequest, Business, Decision, Evidence, Grant, Job, Plan, Project, RequestLink, ReviewRound,
    Task,
)
from justshipit.i18n import DEFAULT, Lang, catalog, glossary, msg
from justshipit.i18n.match import responders
from justshipit.i18n.said import Refusal, Said, said, say
from justshipit.services import (activity, app_settings, holds, launch_gate, matters, owner_tasks, people, plan_screen,
                             policy, queue, request_contract, scope, strengths, tasks, tracking)
from justshipit.services.request_contract import Filing, Refused, RequestRefused
from justshipit.services.strengths import Strengths
from justshipit.services.verdicts import REJECT_LEAD, declines, infer_verdict
from justshipit.settings import company_config, tool_policy

log = logging.getLogger(__name__)

PROJECT_MANAGER_DECIDED = responders.PROJECT_MANAGER_DECIDED
_RISK_ORDER = [r.value for r in RiskLevel]


class GrantRefused(PermissionError):
    """No valid grant for a side effect. A PermissionError for the callers that catch one, but not the OS's."""


class ResponseRejected(Refusal):
    """A decision the server refused (409). The route words it in the owner's language."""


def display_id(req_id: int) -> str:
    return f"REQ-{req_id}"


def parse_id(value: str | int) -> int:
    return int(str(value).upper().removeprefix("REQ-"))


def revision(req: AgentRequest) -> int:
    """Which version of the card the request now shows: a revision rewrites it on the same id."""
    return int((req.action_params or {}).get("revision", 1))


def _hold(s: Session, req: AgentRequest, scope: HoldScope) -> None:
    """Widen the card's hold to `scope`, never narrow it. `blocking` is generated from `hold_scope`, so the flush is
    what makes `req.blocking` read the new value for whoever looks at the card next in this session."""
    if (held := wider(req.hold_scope, scope)) != req.hold_scope:
        req.hold_scope = held.value
        s.flush()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w{2,}", text.lower())}


def _whom(s: Session, business_id: int | None, title: str, to: str | None) -> frozenset[str]:
    """Who an ask is to or about: the business's people its title or its message's `to` names, or the `to` as written
    when it names none of them."""
    found = {f"PPL-{p.id}" for text in (title, to or "") if business_id is not None
             for p in people.named(s, business_id, text)}
    if to and not (business_id is not None and people.named(s, business_id, to)):
        found.add(" ".join(to.casefold().split()))
    return frozenset(found)


def find_similar_pending(s: Session, draft: AgentRequestDraft, project_id: int | None) -> AgentRequest | None:
    """Rework iterations and parallel agents tend to re-raise the same ask; keep one inbox item. Asks to or about
    different people are never the same ask, however alike their words ("Ask to Tom: one week of support emails" and
    "Ask to Lena: …")."""
    if project_id is None:
        return None
    business_id = s.scalar(select(Project.business_id).where(Project.id == project_id))
    mine = _tokens(draft.title)
    whom = None
    for r in pending(s, project_id):
        if r.type != draft.type.value or (r.action_kind or None) != (draft.action_kind or None):
            continue
        other = _tokens(r.title)
        if not (mine and other and len(mine & other) / min(len(mine), len(other)) >= 0.5):
            continue
        whom = whom if whom is not None else _whom(s, business_id, draft.title, draft.handout.to if draft.handout else None)
        if whom == _whom(s, business_id, r.title, ((r.action_params or {}).get("handout") or {}).get("to")):
            return r
    return None


def _payload(draft: AgentRequestDraft) -> dict:
    """What the action carries: the payload a connector runs, and the text the owner uses by hand (`handout`), which
    the to-do approving it makes shows whole. Only a kind that makes a to-do keeps a handout, since only that kind's
    texts are screened (`owner_tasks.screen`), and a message keeps no blank for the owner to fill."""
    handout = ({"handout": owner_tasks.stored_handout(draft.handout)}
               if draft.handout and owner_tasks.kind_for(draft.action_kind) else {})
    if not draft.action_payload_json:
        return handout
    try:
        data = json.loads(draft.action_payload_json)
    except json.JSONDecodeError:
        return {**handout, "payload_raw": draft.action_payload_json[:4000]}
    return {**handout, **({"payload": data} if isinstance(data, dict) else {"payload_raw": draft.action_payload_json[:4000]})}


def door_rules(s: Session, project_id: int | None) -> list[dict]:
    """The decision rights of the business a project belongs to, or none for a project outside any business."""
    if project_id is None:
        return []
    rules = s.scalar(select(Business.door_policy).join(Project, Project.business_id == Business.id)
                     .where(Project.id == project_id))
    return list(rules) if isinstance(rules, list) else []


def _evidence_number(text: str) -> int | None:
    m = re.fullmatch(r"EV-(\d+)", text.strip().upper())
    return int(m.group(1)) if m else None


def _typed_options(draft: AgentRequestDraft) -> None:
    """The card's options are the request's one set of choices, each carrying its decision. A question's yes is an
    answer and any other request's answer is an approval, so the verdict matches what `respond` records. A question's
    "no" ("Redis 캐시 안 함") is an answer too, unless it leads with 반려: refusing to answer is the 반려 button."""
    card = draft.card
    if card is None or not card.options:
        return
    question = draft.type.value in QUESTION_TYPES
    for o in card.options:
        if o.verdict in (HumanDecision.APPROVE, HumanDecision.ANSWER):
            o.verdict = HumanDecision.ANSWER if question else HumanDecision.APPROVE
        elif question and o.verdict == HumanDecision.REJECT and not REJECT_LEAD.search(o.label):
            o.verdict = HumanDecision.ANSWER
    draft.options = [o.label for o in card.options]
    draft.recommended_option = next((o.label for o in card.options if o.recommended), None)


def _stored_card(s: Session, card: DecisionCard, project_id: int | None) -> dict:
    """The card as stored: one recommended option at most, only the evidence ids that exist, and no approval typed on
    an option whose words decline ("지금은 보내지 않음"): its verdict is left to `choices`, which reads the words."""
    data = card.model_dump(mode="json")
    wanted = {n for opt in data["options"] for e in opt["evidence_ids"] if (n := _evidence_number(e)) is not None}
    known = set(s.scalars(select(Evidence.id).where(Evidence.id.in_(wanted), Evidence.project_id == project_id))
                ) if wanted and project_id is not None else set()
    recommended_seen = False
    for opt in data["options"]:
        if opt["verdict"] == HumanDecision.APPROVE.value and declines(opt["label"]):
            log.warning("card option %r declines but was typed approve", opt["label"])
            opt["verdict"] = None
        ids = [f"EV-{n}" for e in opt["evidence_ids"] if (n := _evidence_number(e)) in known]
        if len(ids) != len(opt["evidence_ids"]):
            log.warning("card dropped evidence ids it could not find: %s", set(opt["evidence_ids"]) - set(ids))
        opt["evidence_ids"] = ids
        opt["recommended"] = opt["recommended"] and not recommended_seen
        recommended_seen = recommended_seen or opt["recommended"]
    if data["amends"] is not None and data["amends"] not in _settled_line_ids(s, project_id):
        log.warning("card amends %r, not a said or confirmed line of the approved plan", data["amends"])
        data["amends"] = None
    if data["amends"] is None:
        for opt in data["options"]:
            opt["line"] = None
    return data


def _settled_line_ids(s: Session, project_id: int | None) -> set[str]:
    """The ids of the said and confirmed lines of the approved plan of the project's business: the lines a card can
    amend."""
    from justshipit.services import roadmap
    p = s.get(Project, project_id) if project_id is not None else None
    brief = roadmap.approved_brief(s, p.business_id) if p is not None and p.business_id is not None else None
    return {x["id"] for sec in (brief.sections if brief else []) for x in sec["lines"]
            if x["provenance"] in roadmap.SETTLED}


def _amend_plan(s: Session, req: AgentRequest, key: str) -> None:
    """An answer that picks an option with a new plan line writes that line into the plan, as the owner's. A pick the
    plan cannot take now is refused, so the card never reads as answered while the plan and goal stay as they were."""
    from justshipit.services import roadmap
    from justshipit.services.control import ControlError
    card = (req.action_params or {}).get("card") or {}
    opt = next((o for o in card.get("options") or [] if o["label"] == key), None)
    p = s.get(Project, req.project_id) if req.project_id is not None else None
    if not (card.get("amends") and opt and opt.get("line") and p is not None and p.business_id is not None):
        return
    try:
        amended = roadmap.amend_line(s, p.business_id, card["amends"], text=opt["line"]["text"],
                                     target=opt["line"]["target"], source=f"request:{req.id}", quote=opt["label"],
                                     lang=app_settings.language())
    except ControlError as exc:
        raise ResponseRejected(said("request.plan_locked", label=opt["label"], why=str(exc))) from exc
    if amended is None:
        raise ResponseRejected(said("request.plan_line_gone", label=opt["label"]))


def has_connector(action_kind: str | None) -> bool:
    """Approvals for actions without a connector are records for a human to carry out."""
    from justshipit.operations.gateway import CONNECTORS
    return bool(action_kind) and action_kind in CONNECTORS


# Approving these makes the worker act on its own (spawn a project, merge, freeze a spec, activate a
# skill), so only server code may attach them. A model-drafted request gets internal.other instead.
SYSTEM_ACTIONS = ("project.reassess", "dev.merge", "dev.spec_approve", "dev.plan_approve", "skill.activate_", "bet.")
ROUTINE_CARD = "bet.routine"
BET_CARDS = (ROUTINE_CARD, "project.direction_change")
DEV_ASK = "dev.implementation_start"
# The product plan's approval card (engineering/plans.py): approving it writes the spec from that plan.
PLAN_CARD = "dev.plan_approve"
# Approving adds `estimated_cost_usd` to the request's project budget. `budget.spend` is another thing: a payment the
# owner makes by hand, which changes no budget.
BUDGET_RAISE = "budget.raise"


def normalize_action(draft: AgentRequestDraft, *, system: bool = False) -> dict:
    """Agents sometimes invent action names. Unknown kinds collapse to *.other (strong approval by
    default policy) so they group together; the proposed name is kept for the human."""
    kind = draft.action_kind
    if draft.type == RequestType.DEVELOPMENT or kind == DEV_ASK:
        # A development ask always starts the spec, whatever type or action the agent named, so it is one matter
        # with one policy: that step's.
        draft.type, draft.action_kind = RequestType.DEVELOPMENT, DEV_ASK
        return {"proposed_action_kind": kind} if kind and kind != DEV_ASK else {}
    if kind and not system and kind.startswith(SYSTEM_ACTIONS):
        draft.action_kind = "internal.other"
        return {"proposed_action_kind": kind}
    if not kind or kind in tool_policy():
        return {}
    draft.action_kind = "external.other" if kind.startswith("external.") or "." not in kind else "internal.other"
    return {"proposed_action_kind": kind}


def _post_card(s: Session, draft: AgentRequestDraft, project_id: int | None,
               lang: Lang) -> tuple[dict, list[str], owner_tasks.CardRefused | None]:
    """The stored post card, the fields the server dropped from it, and why it was refused, if it was. Every request
    that makes an owner task is screened, and one that must carry a post card is refused without it. A card for a
    channel with a launch gate is refused until the project's business has met the gate."""
    p = s.get(Project, project_id) if project_id else None
    business = s.get(Business, p.business_id) if p and p.business_id else None
    try:
        card, stripped = owner_tasks.screen(draft, launch_gate.gate_for(s, p.business_id if p else None), lang,
                                            tracking.own_places(business))
    except owner_tasks.CardRefused as exc:
        return {}, [], exc
    if draft.post_card is not None and not card:
        log.warning("post card on %s ignored", draft.action_kind)
    return card, stripped, None


def find_pending_post(s: Session, post_card: dict, kind: str | None, project_id: int | None) -> AgentRequest | None:
    """The pending request for the same post: the same action and channel, and the same title (or first fact for a
    channel that takes no title)."""
    key = owner_tasks.post_key(post_card)
    if project_id is None or not key:
        return None
    for r in pending(s, project_id):
        other = (r.action_params or {}).get("post_card")
        if r.action_kind == kind and other and other["channel"] == post_card["channel"] and owner_tasks.post_key(other) == key:
            return r
    return None


def _record_post_card(s: Session, req: AgentRequest, stripped: list[str], refusal: owner_tasks.CardRefused | None,
                      lang: Lang) -> None:
    ref = display_id(req.id)
    if refusal:
        why = refusal.words(lang)
        req.response = {**HumanResponse(decision=HumanDecision.REJECT, note=why, responded_by="system",
                                        responded_at=req.resolved_at).model_dump(mode="json"), "refused": "post_card"}
        activity.record(s, verb="post_card_refused", entity="request", entity_id=req.id, project_id=req.project_id,
                        actor="system", data={"ref": ref, "reason": why, "rule": refusal.rule},
                        summary=msg("request.note.post_refused", lang, ref=ref, title=req.title[:120], refusal=why))
    elif stripped:
        fields = "·".join(msg(f"request.field.{f}", lang) for f in stripped)
        activity.record(s, verb="post_card_stripped", entity="request", entity_id=req.id, project_id=req.project_id,
                        actor="system", data={"ref": ref, "dropped": stripped},
                        summary=msg("request.note.post_stripped", lang, ref=ref, fields=fields))


def check(s: Session, draft: AgentRequestDraft, origin: Origin, project_id: int | None, *,
          action_params: dict | None = None, system: bool = False) -> list[Refused]:
    """Why `create` would not file this draft under this origin, or nothing when it would. The draft is read as `create`
    reads it, with its action and options settled, and is left as it was."""
    d = draft.model_copy(deep=True)
    params = {**(action_params or {}), **normalize_action(d, system=system)}
    _typed_options(d)
    found = request_contract.resolve(s, d, origin, project_id, params)
    return [] if isinstance(found, Filing) else found


def create(s: Session, draft: AgentRequestDraft, *, from_agent: str, project_id: int | None, origin: Origin,
           action_params: dict | None = None, system: bool = False,
           dedupe: bool = True, owner_decides: bool = False) -> AgentRequest | Plan:
    """File what an agent or the server asks of the owner. Every job of every lane comes through here, so the
    language of the notes it keeps is read here, once per request filed. `origin` is the call site's: where the card
    came from, which the agent never says. The draft is checked against its origin's and its type's rules first
    (services/request_contract.py), and a refused one raises RequestRefused with no row and no link written. A
    development ask that would make a new card starts a product plan instead, or joins the one under way, and that plan
    is returned (`plans.planned`): no new spec starts without a plan the owner approved. `owner_decides`: neither the
    policy nor a CEO door rule settles the card (one filed from team chat, whose pasted text may have steered it)."""
    lang = app_settings.language()
    action_params = {**(action_params or {}), **normalize_action(draft, system=system)}
    _typed_options(draft)
    filing = request_contract.resolve(s, draft, origin, project_id, action_params)
    if not isinstance(filing, Filing):
        raise RequestRefused(filing)
    task_id = origin.task_id
    renamed = "proposed_action_kind" in action_params
    post_params, stripped, refusal = _post_card(s, draft, project_id, lang)
    # A draft an owner directive rules out is withdrawn before it can revise, join or replace any card.
    broken = None if system else _directive_broken(s, project_id, draft)
    develops = draft.type == RequestType.DEVELOPMENT
    routed = (dedupe or develops) and refusal is None and broken is None
    matter = (matters.matter_for(s, draft, project_id, post_params.get("post_card"), from_agent=from_agent,
                                 task_id=task_id) if routed else None)
    replaced = None
    if matter is not None:
        matters.lock(s, project_id)
        placed = matters.place(s, matter, draft, project_id, has_connector=has_connector(draft.action_kind),
                               post_card=post_params.get("post_card"))
        if develops:
            from justshipit.engineering import plans
            need = action_params.get("need") or {"title": draft.title, "problem": draft.summary,
                                                 "why_software": draft.rationale}
            if (plan := plans.planned(s, project_id, placed, need, from_agent)) is not None:
                return plan
        if placed.route not in (matters.Route.NEW, matters.Route.REPLACE, matters.Route.SUCCEEDS):
            if placed.head is not None and placed.route in (matters.Route.REVISE, matters.Route.TASK):
                placed.head.action_params = {**(placed.head.action_params or {}), **matters.keys(matter, placed.head)}
            return _apply_placement(s, placed, draft, filing, from_agent=from_agent, lang=lang,
                                    extra={**action_params, **_payload(draft), **post_params,
                                           **({"card": _stored_card(s, draft.card, project_id)} if draft.card else {})})
        replaced = placed.head if placed.route == matters.Route.REPLACE else None
        revises = {"revises": placed.head.id} if placed.route == matters.Route.SUCCEEDS else {}
        action_params = {**action_params, **matters.keys(matter, replaced), **revises}
    elif routed and (dup := (
            find_pending_post(s, post_params["post_card"], draft.action_kind, project_id) if post_params
            else find_similar_pending(s, draft, project_id))) is not None:
        if (note := msg("request.note.same_ask", lang, agent=glossary.role(from_agent, lang))) not in dup.rationale:
            dup.rationale = f"{dup.rationale}\n\n{note} {draft.rationale}"
        _join(s, dup, filing)
        _tell_owner(s, dup)
        return dup
    verdict = policy.decide(draft.type, draft.action_kind, draft.risk_level, draft.estimated_cost_usd,
                            door_rules(s, project_id), project_manager_may_decide=not renamed and not owner_decides)
    mode = PolicyMode.DENY if refusal else verdict.mode
    if owner_decides and mode == PolicyMode.AUTO:
        mode = PolicyMode.APPROVE
    card = {"card": _stored_card(s, draft.card, project_id)} if draft.card else {}
    # A card that replaces one the project is waiting on is still what it waits on.
    scope_held = wider(filing.scope, replaced.hold_scope if replaced is not None
                       and replaced.status == RequestStatus.PENDING.value else HoldScope.NONE)
    req = AgentRequest(
        project_id=project_id, task_id=task_id, from_agent=from_agent, type=draft.type.value,
        title=draft.title, summary=draft.summary, rationale=draft.rationale, options=draft.options,
        recommended_option=draft.recommended_option, action_kind=draft.action_kind,
        action_params={**(action_params or {}), **_payload(draft), **card, **post_params},
        estimated_cost_usd=draft.estimated_cost_usd,
        risk_level=draft.risk_level.value, hold_scope=scope_held.value, policy_mode=mode.value,
        status=RequestStatus.PENDING.value, expires_at=_deadline(filing.expiry),
    )
    request_contract.stamp(req, filing)
    s.add(req)
    s.flush()
    request_contract.link(s, req.id, filing.links, revision(req))
    if broken is not None:
        return _refuse_for_directive(s, req, broken, lang)
    if replaced is not None:
        supersede(replaced, display_id(req.id), req.from_agent)
    if verdict.ignored:
        _record_ignored_rules(s, req, verdict.ignored, lang)
    if mode == PolicyMode.DENY:
        req.status = RequestStatus.DENIED_BY_POLICY.value
        req.resolved_at = datetime.now(timezone.utc)
    elif mode == PolicyMode.AUTO and draft.type not in policy.ASKS_THE_OWNER:
        req.status = RequestStatus.AUTO_APPROVED.value
        req.resolved_at = datetime.now(timezone.utc)
        by = "policy"
        if verdict.rule:
            by = PROJECT_MANAGER_DECIDED
            _record_project_manager_decision(s, req, verdict.rule, lang)
        _grant(s, req, HumanResponse(decision=HumanDecision.APPROVE, responded_by=by))
        _maybe_dispatch_side_effects(s, req)
        if not has_connector(req.action_kind):
            owner_tasks.create_for_request(s, req)
    if replaced is not None:
        _maybe_resume(s, replaced)  # a replacement the policy settled leaves nothing for the waiting project to wait on
    _tell_owner(s, req)
    _record_post_card(s, req, stripped, refusal, lang)
    return req


def _deadline(rule: ExpiryPolicy) -> datetime | None:
    """When a card filed (or revised) under `rule` expires: a card that waits for the owner with no end has none. The
    clock is the server's, the rule the card's."""
    if rule == ExpiryPolicy.KEEP_WAITING:
        return None
    ttl = int(company_config()["autonomy"]["grant_ttl_hours"])
    return datetime.now(timezone.utc) + timedelta(hours=ttl * 2)


def _join(s: Session, card: AgentRequest, filing: Filing) -> None:
    """A draft that lands on an open card: the card keeps this asker beside its own, and holds what it held and what
    the draft holds."""
    request_contract.link(s, card.id, filing.joined, revision(card))
    _hold(s, card, filing.scope)


def _note(s: Session, req: AgentRequest, verb: str, summary: str, draft: AgentRequestDraft, from_agent: str) -> None:
    activity.record(s, verb=verb, entity="request", entity_id=req.id, project_id=req.project_id, actor=from_agent,
                    summary=summary[:500], data={"ref": display_id(req.id), "asked": draft.title[:300],
                                                 "by": from_agent})


def _apply_placement(s: Session, placed: matters.Placement, draft: AgentRequestDraft, filing: Filing, *,
                     from_agent: str, extra: dict, lang: Lang) -> AgentRequest:
    """A draft for a matter that already has a card: the card, its owner task or the activity log takes it. A revision
    replaces what the agent declared on the card and adds its asker; a draft folded onto a card adds its asker and its
    holds."""
    h, route = placed.head, placed.route
    asked = msg("request.note.asked", lang, agent=from_agent, title=draft.title[:80])
    why = say(placed.why, lang) if placed.why else ""
    if route == matters.Route.REVISE:
        revision = int((h.action_params or {}).get("revision", 1)) + 1
        fresh = {**(h.action_params or {}), **extra, "revision": revision}
        for new, old in (("payload", "payload_raw"), ("payload_raw", "payload")):
            if new in extra:
                fresh.pop(old, None)
        for key in ("card", "handout"):
            if key not in extra:
                fresh.pop(key, None)  # the old card's options and text would read as this revision's
        h.title, h.summary, h.rationale = draft.title, draft.summary, draft.rationale
        h.options, h.recommended_option = draft.options, draft.recommended_option
        h.action_params = fresh
        h.estimated_cost_usd = max((c for c in (h.estimated_cost_usd, draft.estimated_cost_usd) if c is not None),
                                   default=None)
        h.risk_level = max(h.risk_level, draft.risk_level.value, key=_RISK_ORDER.index)
        was_blocking = h.blocking
        request_contract.end_agent_rows(s, h.id, revision)
        request_contract.stamp(h, filing)
        if (h.expires_at is None) != (filing.expiry == ExpiryPolicy.KEEP_WAITING):
            h.expires_at = _deadline(filing.expiry)  # a revision that changes the rule changes whether a clock runs
        request_contract.link(s, h.id, filing.links, revision)
        _hold(s, h, filing.scope)
        # A revision can ask for more (a cost, a higher risk) but never for less than the card already needed.
        verdict = policy.decide(draft.type, h.action_kind, RiskLevel(h.risk_level), h.estimated_cost_usd,
                                door_rules(s, h.project_id), project_manager_may_decide=False)
        h.policy_mode = max(PolicyMode(h.policy_mode), verdict.mode, key=POLICY_ORDER.index).value
        if h.policy_mode == PolicyMode.DENY.value:
            h.status, h.resolved_at = RequestStatus.DENIED_BY_POLICY.value, datetime.now(timezone.utc)
        _note(s, h, "request_revised", msg("request.note.revised", lang, ref=display_id(h.id), n=revision, asked=asked),
              draft, from_agent)
        if h.blocking and not was_blocking:
            _tell_owner(s, h)
        return h
    if route == matters.Route.APPEND:
        card = placed.target
        _keep_need(card, draft, from_agent)
        label = msg("request.note.next_version" if card.action_kind == "dev.spec_approve" else "request.note.also", lang)
        by = glossary.role(from_agent, lang)
        card.rationale = f"{card.rationale}\n\n[{label} · {by}] {draft.title}: {draft.summary}"
        _join(s, card, filing)
        _note(s, card, "request_folded", msg("request.note.folded", lang, asked=asked, why=why), draft, from_agent)
        return h
    if route == matters.Route.TASK:
        task = placed.task
        update = {"request": f"REQ-{h.id}", "by": from_agent, "title": draft.title, "summary": draft.summary,
                  "at": datetime.now(timezone.utc).isoformat()}
        if post := extra.get("post_card"):
            update["post_card"] = post
        task.card = {**(task.card or {}), "updates": [*(task.card or {}).get("updates", []), update]}
        activity.record(s, verb="owner_task_updated", entity="owner_task", entity_id=task.id,
                        project_id=task.project_id, business_id=task.business_id, actor=from_agent,
                        summary=msg("request.note.task_updated", lang, ot=owner_tasks.display_id(task.id),
                                    asked=asked)[:500],
                        data={"ref": owner_tasks.display_id(task.id), "request": display_id(h.id)})
        return h
    if route in (matters.Route.REASK_SPEC, matters.Route.REASK_PLAN):
        from justshipit.engineering import plans, specs
        again = (specs if route == matters.Route.REASK_SPEC else plans).reask_approval(s, placed.target)
        _join(s, again, filing)
        _note(s, again, "request_folded", msg("request.note.folded", lang, asked=asked, why=why), draft, from_agent)
        return h
    if placed.target is not None and draft.type == RequestType.DEVELOPMENT:
        # The product's next version takes it up; one waiting on a spec's questions goes onto that spec's card.
        _keep_need(placed.target, draft, from_agent, key=WAITING_NEEDS if placed.waits else "extra_needs")
    # The row links to the card it went onto, so the note names no code the feed would drop out of its middle.
    _note(s, h, "request_folded", msg("request.note.folded", lang, asked=asked, why=why), draft, from_agent)
    return h


# Needs kept on a spec's own request while the spec waits on its questions: apart from `extra_needs`, which that
# request's spec already read, so only these go onto the spec card when it goes up.
WAITING_NEEDS = "waiting_needs"


def _keep_need(card: AgentRequest, draft: AgentRequestDraft, from_agent: str, key: str = "extra_needs") -> None:
    need = {"title": draft.title, "problem": draft.summary, "by": from_agent}
    card.action_params = {**(card.action_params or {}), key: [*(card.action_params or {}).get(key, []), need]}


def supersede(old: AgentRequest, into: str, by: str) -> None:
    """Close the card `into` replaces (a card's REQ id, or a plan's key): a deferred one keeps the owner's answer and
    says what replaced it."""
    if old.status == RequestStatus.PENDING.value:
        old.response = {"decision": "superseded", "into": into, "by": by}
        old.resolved_at = datetime.now(timezone.utc)
    else:
        old.response = {**(old.response or {}), "superseded_by": into}
    old.status = RequestStatus.CANCELLED.value


def close_duplicates(s: Session, found: list[matters.Duplicate], *, by: str) -> list[int]:
    """Close each duplicate as superseded into the card that decides it, as `supersede` does: an open card says what
    took it over, a deferred or expired one keeps the owner's answer. The card it points at is not rewritten, except
    that it blocks when a closed card did, so a project waiting on the owner keeps waiting. A card answered meanwhile
    is left alone. Running it again closes nothing more."""
    closed = []
    for d in found:
        r = s.scalar(select(AgentRequest).where(AgentRequest.id == d.card.id).with_for_update(key_share=True)
                     .execution_options(populate_existing=True))
        if r.status not in (RequestStatus.PENDING.value, RequestStatus.DEFERRED.value, RequestStatus.EXPIRED.value):
            continue
        if r.status == RequestStatus.PENDING.value:
            r.response = {"decision": "superseded", "into": display_id(d.into.id), "by": by, "responded_by": by,
                          "note": d.why}
            r.resolved_at = datetime.now(timezone.utc)
            if d.into.status == RequestStatus.PENDING.value:
                _hold(s, d.into, HoldScope(r.hold_scope))
        else:
            r.response = {**(r.response or {}), "superseded_by": display_id(d.into.id), "closed_by": by,
                          "closed_note": d.why}
        r.status = RequestStatus.CANCELLED.value
        closed.append(r.id)
        _maybe_resume(s, r)
    return closed


def stored_drafts(raw: list[dict]) -> tuple[list[AgentRequestDraft], list[dict | None]]:
    """The drafts a task result stored, read again when its review settles, with the card each keeps as stored. A card
    from before the report fields and typed options does not parse any more, so its draft is read without it and the
    card rides along as stored, which the read model still reads (as `re_request` does)."""
    drafts, cards = [], []
    for d in raw:
        try:
            drafts.append(AgentRequestDraft.model_validate(d))
            cards.append(None)
        except ValidationError:
            drafts.append(AgentRequestDraft.model_validate({**d, "card": None}))
            cards.append(d.get("card") if isinstance(d.get("card"), dict) else None)
            log.warning("stored request draft %r has a card from before typed options", str(d.get("title"))[:80])
    return drafts, cards


def from_agent_drafts(s: Session, drafts: list[AgentRequestDraft], *, agent: str, project_id: int | None,
                      origin: Origin, cards: list[dict | None] | None = None) -> list[int]:
    """The requests an agent's output raises. A draft that needs nothing from the owner is an activity note, not a
    card; the ids returned are the cards that carry the rest, and a development ask a plan took has none yet. A draft
    the contract refuses is filed as nothing but a line saying why (`not_filed`). `cards` are stored cards, by
    position, for drafts read without theirs (`stored_drafts`)."""
    ids = []
    task_id = origin.task_id
    for d, stored in zip(drafts, cards or [None] * len(drafts)):
        if not d.owner_action:
            activity.record(s, verb="agent_notice", entity="task" if task_id else "project",
                            entity_id=task_id or project_id, project_id=project_id, actor=agent,
                            summary=f"{d.title[:120]}: {d.summary[:300]}",
                            data={"title": d.title[:300], "type": d.type.value, "action_kind": d.action_kind})
            continue
        try:
            filed = create(s, d, from_agent=agent, project_id=project_id, origin=origin,
                           action_params={"card": stored} if stored else None)
        except RequestRefused as exc:
            not_filed(s, d, exc.refusals, agent=agent, project_id=project_id, origin=origin)
            continue
        if isinstance(filed, AgentRequest):
            ids.append(filed.id)
    return list(dict.fromkeys(ids))


def not_filed(s: Session, draft: AgentRequestDraft, refusals: list[Refused], *, agent: str, project_id: int | None,
              origin: Origin) -> dict:
    """A request the contract refused: a line in the activity log, which the next CEO step reads under "Requests the
    server did not file", and on a CEO round its `refusals`. Returns what it recorded."""
    lang = app_settings.language()
    why = " ".join(say(r.said(), lang) for r in refusals)
    entry = {"title": draft.title[:300], "refused": [r.key.value for r in refusals], "why_en": " ".join(
        say(r.said(), "en") for r in refusals)}
    activity.record(s, verb="request_not_filed", entity="task" if origin.task_id else "project",
                    entity_id=origin.task_id or project_id, project_id=project_id, actor=agent,
                    summary=msg("path.not_filed", lang, title=owner_text.title(draft.title, 120), why=why)[:500],
                    data={**entry, "origin": origin.kind.value})
    if isinstance(origin, RoundOrigin) and (row := s.get(ReviewRound, origin.round_id)) is not None:
        row.refusals = [*(row.refusals or []), entry]
    return entry


def supersede_iteration(s: Session, task_id: int, earlier: list[int], now: list[int]) -> list[int]:
    """A task's rework re-raises what its earlier pass asked, often under new titles that match nothing (동네산책
    T-26: five posts on ten cards). When the new pass raised a fresh card of an action for each of the earlier pass's
    open cards of that action, the earlier ones close as superseded. A pass that raised fewer leaves them open: it
    may only have added one, so nothing the owner still has to decide disappears."""
    def mine(ids: list[int]) -> list[AgentRequest]:
        # Row-locked and read again, so a card the owner answered meanwhile is seen answered and left alone.
        return list(s.scalars(select(AgentRequest).where(
            AgentRequest.id.in_(ids), AgentRequest.task_id == task_id, AgentRequest.action_kind.is_not(None),
            AgentRequest.status == RequestStatus.PENDING.value).order_by(AgentRequest.id)
            .with_for_update(key_share=True).execution_options(populate_existing=True)))
    lang = app_settings.language()
    fresh = mine([i for i in now if i not in earlier])
    stale = mine([i for i in earlier if i not in now])
    closed = []
    for kind in {r.action_kind for r in stale}:
        old = [r for r in stale if r.action_kind == kind]
        new = [r for r in fresh if r.action_kind == kind]
        if len(new) < len(old):
            continue
        for r in old:
            r.status = RequestStatus.CANCELLED.value
            r.resolved_at = datetime.now(timezone.utc)
            r.response = {"decision": "superseded", "by": "system", "into": ", ".join(display_id(n.id) for n in new),
                          "note": msg("request.note.rework", lang, task=task_id)}
            closed.append(r.id)
    return sorted(closed)


def current(s: Session, req: AgentRequest | None) -> AgentRequest | None:
    """The request that now carries a closed card's decision: one merged or superseded into another is followed to it."""
    seen = set()
    while req is not None and req.status == RequestStatus.CANCELLED.value and req.id not in seen:
        seen.add(req.id)
        ref = (req.response or {}).get("superseded_by") or (req.response or {}).get("into")
        if (rid := matters.request_ref(ref if isinstance(ref, str) else None)) is None:
            return req
        req = s.get(AgentRequest, rid)
    return req


def carry_holds(s: Session, old_task_id: int, new_task_id: int) -> None:
    """A task that redoes a failed one (TaskDraft.redoes) waits on what that one waited on: each of its holds whose
    card is still open is written again on the new task, under the same card, revision and source, so a revision of
    the card ends both."""
    for x in s.scalars(select(RequestLink).where(
            RequestLink.rel == Rel.HOLDS.value, RequestLink.target_kind == TargetKind.TASK.value,
            RequestLink.target_id == old_task_id, RequestLink.phase.is_not(None), RequestLink.ended_at.is_(None))).all():
        card = current(s, s.get(AgentRequest, x.request_id))
        if card is not None and card.status == RequestStatus.PENDING.value:
            request_contract.link(s, x.request_id, (request_contract.Link(
                Rel.HOLDS, TargetKind.TASK, new_task_id, LinkSource(x.source), phase=HoldPhase(x.phase)),), x.revision)


def _directive_broken(s: Session, project_id: int | None, draft: AgentRequestDraft) -> plan_screen.Rule | None:
    p = s.get(Project, project_id) if project_id else None
    return plan_screen.broken(plan_screen.rules(s, p), draft.title) if p else None


def _refuse_for_directive(s: Session, req: AgentRequest, rule: plan_screen.Rule, lang: Lang) -> AgentRequest:
    """A request for work an owner directive ruled out never reaches the inbox (REQ-4 asked for the KOSIS files after
    '시장규모 집계 필요 없음'). It is kept, withdrawn, with the directive it broke, and the activity log says so."""
    why = msg("request.note.directive", lang, source=rule.ref, rule=rule.label)
    req.status, req.resolved_at = RequestStatus.CANCELLED.value, datetime.now(timezone.utc)
    req.response = {**HumanResponse(decision=HumanDecision.REJECT, note=why, responded_by="system",
                                    responded_at=req.resolved_at).model_dump(mode="json"), "refused": "directive"}
    activity.record(s, verb="directive_conflict", entity="request", entity_id=req.id, project_id=req.project_id,
                    actor="system", summary=f"{display_id(req.id)} {req.title[:120]}: {why}",
                    data={"ref": display_id(req.id), "directive": rule.ref, "rule": rule.label})
    return req


_OWNER_ASKS = {t.value for t in policy.ASKS_THE_OWNER}
_DEV_DECISIONS = (DEV_ASK, "dev.spec_approve")


def _tell_owner(s: Session, req: AgentRequest) -> None:
    """A request the owner has to answer, and that is blocking, a question, an escalation, a development decision or
    a strong approval, goes to Slack once, and so does a card the bet loop files, which only the owner can answer. A
    routine approval waits in the inbox and is counted in the briefing."""
    if req.status != RequestStatus.PENDING.value:
        return
    if (req.blocking or req.type in _OWNER_ASKS or req.policy_mode == PolicyMode.STRONG_APPROVE.value
            or req.action_kind in (*BET_CARDS, *_DEV_DECISIONS)):
        queue.notify(s, notices.decision_needed(req.id, headline(req), why(req) or req.summary))


def _rule_cite(rule: object) -> dict:
    if not isinstance(rule, dict):
        return {"raw": str(rule)[:200]}
    return {k: rule.get(k) for k in ("id", "action", "decider", "note", "source", "origin")}


def _record_project_manager_decision(s: Session, req: AgentRequest, rule: dict, lang: Lang) -> None:
    """A request a door rule left to the CEO is approved without the owner, so the owner can read who decided and
    which rule allowed it, in the request itself and in the activity log."""
    why = msg("request.note.ceo_decided", lang, action=rule["action"]) + (f" {rule['note']}" if rule.get("note") else "")
    req.response = {**HumanResponse(decision=HumanDecision.APPROVE, note=why, responded_by=PROJECT_MANAGER_DECIDED,
                                    responded_at=req.resolved_at).model_dump(mode="json"), "door_rule": _rule_cite(rule)}
    activity.record(s, verb="ceo_decided", entity="request", entity_id=req.id, project_id=req.project_id, actor="project_manager",
                    summary=msg("request.note.ceo_decided_event", lang, ref=display_id(req.id), title=req.title[:120],
                                action=rule["action"]),
                    data={"ref": display_id(req.id), "action_kind": req.action_kind, "door_rule": _rule_cite(rule)})


def _record_ignored_rules(s: Session, req: AgentRequest, ignored: tuple[tuple[object, Said], ...],
                          lang: Lang) -> None:
    """Rules that had no effect on this request, kept in the activity log so the owner can see a rule that does not
    do what it says."""
    whys = [say(why, lang) for _, why in ignored]
    log.error("%s: door rules ignored: %s", display_id(req.id), whys)
    activity.record(s, verb="door_rule_ignored", entity="request", entity_id=req.id, project_id=req.project_id,
                    actor="system", summary=msg("request.note.rules_ignored", lang, ref=display_id(req.id),
                                                n=len(ignored), why=whys[0]),
                    data={"ref": display_id(req.id), "action_kind": req.action_kind,
                          "door_rules": [{**_rule_cite(rule), "why": why} for (rule, _), why in zip(ignored, whys)]})


def re_request(s: Session, request_id: int) -> AgentRequest:
    """The owner's path after an external action failed: a fresh request for the same action and payload, decided
    under the normal policy. It never merges into another pending request, and the grant the old request spent
    (or, if it spent none, its own) is revoked so nothing of the old approval carries over. Asking twice returns
    the same new request."""
    old = s.scalar(select(AgentRequest).where(AgentRequest.id == request_id).with_for_update(key_share=True))
    if old is None:
        raise ResponseRejected(said("request.not_found", ref=display_id(request_id)))
    payload = (old.action_params or {}).get("payload")
    if not has_connector(old.action_kind) or not payload:
        raise ResponseRejected(said("request.not_external"))
    if again := s.scalar(select(AgentRequest).where(AgentRequest.action_params["re_request_of"].as_integer() == old.id)):
        return again
    if done := s.scalar(select(ActionExecution).where(ActionExecution.request_id == old.id,
                                                      ActionExecution.status == "done")):
        raise ResponseRejected(said("request.already_ran", id=done.id))
    lang = app_settings.language()
    if why := queue.why_busy(s, f"action:{old.id}", ("execute_action",), lang=lang):
        raise ResponseRejected(said("request.still_running", why=why))
    # Grants match by action kind and project, so the failed run may have spent a sibling's grant. Revoke the one
    # it spent; only a request that spent none gives up its own, or a sibling would lose the one left for it.
    spent = s.scalar(select(ActionExecution.grant_id).where(ActionExecution.request_id == old.id))
    for g in s.scalars(select(Grant).where(Grant.id == spent if spent else Grant.request_id == old.id,
                                           Grant.revoked.is_(False))):
        g.revoked = True
    draft = AgentRequestDraft(
        type=RequestType(old.type), title=msg("request.note.again_title", lang, title=old.title)[:300],
        summary=old.summary, rationale=f"{msg('request.note.again_why', lang, ref=display_id(old.id))}\n\n{old.rationale}",
        options=old.options or [], recommended_option=old.recommended_option, action_kind=old.action_kind,
        action_payload_json=json.dumps(payload, ensure_ascii=False), estimated_cost_usd=old.estimated_cost_usd,
        risk_level=RiskLevel(old.risk_level))
    # The card passed its checks when it was first stored, and one from before typed options would not parse again.
    card = {"card": c} if (c := (old.action_params or {}).get("card")) else {}
    return create(s, draft, from_agent=old.from_agent, project_id=old.project_id,
                  origin=ServerOrigin(OriginKind.RE_REQUEST, old.id, task_id=old.task_id),
                  action_params={"re_request_of": old.id, **card}, dedupe=False)


# ------------------------------------------------------------------ reopening a held or declined decision

_REOPENABLE = (RequestStatus.DEFERRED.value, RequestStatus.REJECTED.value)
# A card bound to a build: its flow acts on a hold or a no at once (a merge put off moves the job, a spec question
# settles without the answer) and the build has its own way to ask again on the Engineering screen.
_BUILD_KEYS = ("dev_job_id", "use_of_job", SPEC_QUESTION_KEY, FIX_QUESTION_KEY)


def reopen_barrier(req: AgentRequest, status: str) -> Said | None:
    """Why a decision of this kind can never be reopened once it is `status` (DEFERRED or REJECTED), or None when its
    kind allows it. A build's card never: its own flow already acted. A declined card a server flow owns (a spec, a
    bet, a meeting decision, a skill, an idea) neither: the flow already took the no (a dropped bet, a rejected spec
    the next ask redrafts, a decision recorded as rejected). Put on hold, those flows change nothing."""
    params = req.action_params or {}
    if req.action_kind == "dev.merge" or any(k in params for k in _BUILD_KEYS):
        return said("request.reopen.build")
    # A declined plan reopens like the build ask it replaced: nothing was written from it.
    if status == RequestStatus.REJECTED.value and matters.server_bound(req) and req.action_kind != PLAN_CARD:
        return said("request.reopen.flow")
    return None


def reopen_refusal(s: Session, req: AgentRequest) -> Said | None:
    """Why the owner can't reopen this decision now, or None when they can: only one they put on hold or declined
    (`respond` is the only way to either), whose kind allows it, which has not expired, and, when declined, which
    nothing has followed from yet: a note the CEO re-planned around (`realign`) or a project that carried on without
    it (`resume`). A hold sets either going too, but a hold was never a no, so the work done since stands with it.
    Nor one whose project or business is archived: approving it would set work going that the archive stopped."""
    if req.status not in _REOPENABLE:
        return said("request.reopen.not_closed", ref=display_id(req.id))
    if barrier := reopen_barrier(req, req.status):
        return barrier
    pid, bid = scope.owner_of(s, {**(req.action_params or {}), "project_id": req.project_id})
    if pid is not None and s.scalar(select(Project.status).where(Project.id == pid)) == "ARCHIVED":
        return said("request.reopen.archived_project", ref=f"P-{pid}")
    if bid is not None and s.scalar(select(Business.status).where(Business.id == bid)) == "ARCHIVED":
        return said("request.reopen.archived_business", ref=f"B-{bid}")
    if req.expires_at and req.expires_at < datetime.now(UTC):
        return said("request.reopen.expired", ref=display_id(req.id))
    if req.status == RequestStatus.REJECTED.value and s.scalar(select(Job.id).where(Job.idempotency_key.in_(
            [_round_key(req, f"realign:{req.id}"), _round_key(req, f"resume:{req.project_id}:{req.id}"),
             f"redraft_spec:{req.id}", f"redraft_plan:{req.id}"])
    ).limit(1)) is not None:
        return said("request.reopen.followed")
    return None


def _round_key(req: AgentRequest, key: str) -> str:
    """The idempotency key of work the owner's decision on `req` queues, one per decision: each reopen starts a new
    round, so the next decision's work is queued (enqueue drops a key it has seen) and is told from the last one's."""
    n = len((req.action_params or {}).get("reopened", []))
    return f"{key}:round{n}" if n else key


def reopen(s: Session, request_id: int) -> AgentRequest:
    """Put a held or declined decision back in front of the owner, the same card and revision, waiting again. What it
    was decided as is kept under `reopened`; the activity log records the status going back to PENDING, which only a
    reopen does. It no longer holds up work: its project already went on without it, and a blocking card left open on a
    running project would say otherwise."""
    req = s.scalar(select(AgentRequest).where(AgentRequest.id == request_id).with_for_update(key_share=True))
    if req is None:
        raise ResponseRejected(said("request.not_found", ref=display_id(request_id)))
    if why := reopen_refusal(s, req):
        raise ResponseRejected(why)
    req.action_params = {**(req.action_params or {}), "reopened": [
        *(req.action_params or {}).get("reopened", []),
        {"status": req.status, "response": req.response, "at": datetime.now(UTC).isoformat()}]}
    req.status, req.response, req.resolved_at, req.hold_scope = (RequestStatus.PENDING.value, None, None,
                                                                 HoldScope.NONE.value)
    s.flush()  # `blocking` is generated: reads false only once this is written
    if req.action_kind == PLAN_CARD:
        from justshipit.engineering import plans
        plans.follow(s, req)
    return req


def _grant(s: Session, req: AgentRequest, resp: HumanResponse) -> None:
    if not req.action_kind:
        return
    ttl = int(company_config()["autonomy"]["grant_ttl_hours"])
    s.add(Grant(
        request_id=req.id, project_id=req.project_id, action_kind=req.action_kind,
        max_amount_usd=resp.max_amount_usd if resp.max_amount_usd is not None else req.estimated_cost_usd,
        max_uses=resp.max_uses or 1,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl),
        connector_mode=app_settings.connector_mode_for(req.action_kind),
    ))


def respond(s: Session, request_id: int, resp: HumanResponse) -> AgentRequest:
    # Read as locked, not as this session last saw it: an expiry or a withdrawal that took the row first is refused below.
    # Card rows are locked FOR NO KEY UPDATE everywhere: writers of a card still exclude each other, and a link written
    # under a project lock (its foreign key takes KEY SHARE on the card) never waits on an answer, so a realign that
    # holds the project and copies a hold cannot deadlock with an answer that holds the card and waits for the project.
    req = s.scalar(select(AgentRequest).where(AgentRequest.id == request_id).with_for_update(key_share=True)
                   .execution_options(populate_existing=True))
    if req is None:
        raise ResponseRejected(said("request.not_found", ref=display_id(request_id)))
    if req.status in {st.value for st in RESOLVED_STATUSES}:
        raise ResponseRejected(said("request.already_decided", ref=display_id(request_id), status=req.status))
    # The card can be revised on the same id while the owner reads it; a decision on an earlier revision is refused.
    if resp.revision is not None and resp.revision != revision(req):
        raise ResponseRejected(said("request.stale"))
    # An expired request can no longer grant anything, but the owner must still be able to close it: otherwise a
    # blocking one would hold its project in WAITING_HUMAN forever.
    if (req.expires_at and req.expires_at < datetime.now(timezone.utc)
            and resp.decision in (HumanDecision.APPROVE, HumanDecision.ANSWER)):
        raise ResponseRejected(said("request.expired", ref=display_id(request_id)))
    if (resp.decision == HumanDecision.APPROVE and (why := typed_confirm_why(req))
            and (resp.confirm_text or "").strip().upper() != display_id(req.id)):
        raise ResponseRejected(said(_TYPED_REFUSAL[why], ref=display_id(req.id),
                                    cost=f"${req.estimated_cost_usd or 0:g}"))
    if req.type == RequestType.QUESTION.value and resp.decision == HumanDecision.APPROVE and not (
            resp.answer or resp.chosen_option):
        raise ResponseRejected(said("request.needs_answer"))
    picked = _picked(req, resp, app_settings.language())
    if picked:
        resp.chosen_option = picked["label"]
    if picked and picked["verdict"] and not same_decision(picked["verdict"], resp.decision.value):
        raise ResponseRejected(said("request.verdict_mismatch", label=picked["label"],
                               verdict=said(f"request.verdict.{picked['verdict']}")))
    # Approving a question records its answer; an approved escalation or clarification is an approval and acts.
    if (resp.decision == HumanDecision.APPROVE and picked and req.type != RequestType.QUESTION.value
            and (declines(picked["key"]) or declines(picked["label"]))
            and (has_connector(req.action_kind) or _effect(req, RequestStatus.APPROVED) is not None)):
        raise ResponseRejected(said("request.declining_option", label=picked["label"]))
    if resp.decision in (HumanDecision.REJECT, HumanDecision.DEFER) and not (picked and picked["verdict"]):
        resp.chosen_option = None  # an option that does not say it is a no is not the owner's answer to a no
    resp.post_edit = _post_edit(req, resp)
    sent_back = _sent_back(s, req, resp)
    plan_back = _plan_sent_back(s, req, resp)
    if picked and resp.decision in (HumanDecision.APPROVE, HumanDecision.ANSWER):
        _amend_plan(s, req, picked["key"])

    resp.responded_at = datetime.now(timezone.utc)
    req.response = resp.model_dump(mode="json")
    req.resolved_at = resp.responded_at
    req.status = {
        HumanDecision.APPROVE: RequestStatus.APPROVED,
        HumanDecision.ANSWER: RequestStatus.ANSWERED,
        HumanDecision.REJECT: RequestStatus.REJECTED,
        HumanDecision.DEFER: RequestStatus.DEFERRED,
    }[resp.decision].value
    if req.type == RequestType.QUESTION.value and resp.decision == HumanDecision.APPROVE:
        req.status = RequestStatus.ANSWERED.value
    if req.status == RequestStatus.APPROVED.value:
        _grant(s, req, resp)
        if not has_connector(req.action_kind):
            owner_tasks.create_for_request(s, req, by_owner=True)
    elif req.status == RequestStatus.ANSWERED.value and answer_makes_task(req):
        task = owner_tasks.create_for_request(s, req, by_owner=True)
        steps = ((req.action_params or {}).get("use_steps") or {}).get(resp.chosen_option or "")
        local = resp.chosen_option == (req.action_params or {}).get("local_option")
        if task is not None and steps:
            # The way the owner picked, every step of it; run on their own computer, it publishes nothing.
            task.card = {**task.card, "what": steps, **({"label": "run"} if local else {})}
    follow_decision(s, req)
    # A hold decides nothing, so it runs none of what the card said answering does.
    uses = ([] if resp.decision == HumanDecision.DEFER
            else _answer_uses(s, req, picked["key"] if picked and resp.chosen_option else None))

    # Record answers into the project context so every later agent call sees them. Build a new list:
    # appending to the loaded one mutates the "old" value too, SQLAlchemy then sees no change and the
    # answer is silently dropped (only the first answer of a project was ever saved).
    if req.project_id and (resp.answer or resp.chosen_option or resp.note or sent_back or uses):
        p = s.scalar(select(Project).where(Project.id == req.project_id).with_for_update())
        ctx = p.context or {}
        p.context = {**ctx, "owner_answers": [*ctx.get("owner_answers", []), {
            "request": display_id(req.id), "title": req.title, "decision": req.status,
            "answer": resp.answer or resp.chosen_option, "note": resp.note,
            # Each note on a planned screen is an owner source of its own, held against the draft it was written on.
            **({"screens": sent_back, "commit": req.action_params["commit_sha"]} if sent_back else {}),
        }]}
    # Free-text guidance can change direction, whatever the project is doing: the CEO re-plans what has not started.
    _follow_answer(s, req, uses, replan=bool(resp.note or resp.answer), by_owner=True)

    if sent_back:
        p, key = req.action_params, f"redraft_spec:{req.id}"
        _queued(s, req, queue.enqueue(s, "redraft_spec", {"project_id": req.project_id, "spec_id": p["spec_id"],
                                                          "request_id": req.id}, idempotency_key=key, by_owner=True), key)
    if plan_back:
        p, key = req.action_params, f"redraft_plan:{req.id}"
        _queued(s, req, queue.enqueue(s, "redraft_plan", {"project_id": req.project_id, "plan_id": p["plan_id"],
                                                          "request_id": req.id, "commit": p["commit_sha"],
                                                          "note": resp.note.strip()},
                                      idempotency_key=key, by_owner=True), key)
    if req.project_id:
        from justshipit.engineering import owner_trace
        owner_trace.restate(s, req.project_id, app_settings.language())
    _maybe_resume(s, req, by_owner=True)
    _maybe_dispatch_side_effects(s, req, by_owner=True)
    return req


def _answer_uses(s: Session, req: AgentRequest, option: str | None) -> list[RequestLink]:
    """What the card declared answering does that this answer runs: each use for any answer, and each for the option
    the owner picked (`option`, the option as stored)."""
    return [x for x in s.scalars(select(RequestLink).where(
        RequestLink.request_id == req.id, RequestLink.rel == Rel.ANSWER_USE.value, RequestLink.ended_at.is_(None))
        .order_by(RequestLink.id)) if x.option_key is None or x.option_key == option]


_TERMINAL_TASKS = {st.value for st in TERMINAL_TASK_STATUSES}


def _follow_answer(s: Session, req: AgentRequest, uses: list[RequestLink], *, replan: bool, by_owner: bool) -> None:
    """Run the answer uses, each recorded as followed. `informs` is the owner_answers entry the caller wrote, which every
    agent of the project reads. `feeds` puts the answer in each named task's own prompt (`context.fed_answers` reads
    these links), unless the task ended meanwhile. `replans`, or the owner's own words (`replan`), queue the CEO's
    re-plan, after which a run the answer freed resumes (`_replan`)."""
    for x in uses:
        if x.effect == AnswerEffect.REPLANS.value or (
                x.target_kind == TargetKind.TASK.value and s.get(Task, x.target_id).status in _TERMINAL_TASKS):
            continue
        _followed(s, req, FollowedEffect(x.effect), TargetKind(x.target_kind), x.target_id, x.option_key)
    replans = [x for x in uses if x.effect == AnswerEffect.REPLANS.value]
    if (replan or replans) and req.project_id and (job := _replan(s, req, by_owner=by_owner)) is not None:
        _followed(s, req, FollowedEffect.REPLANS, TargetKind.JOB, job.id, replans[0].option_key if replans else None)


def _sent_back(s: Session, req: AgentRequest, resp: HumanResponse) -> list[dict]:
    """The owner's notes on single screens of a spec card, which send the spec back to be written again. They go with
    a decline only: approving builds the draft as it is, and a hold decides nothing, so either would drop them."""
    if not resp.screen_notes:
        return []
    if req.action_kind != "dev.spec_approve" or resp.decision != HumanDecision.REJECT:
        raise ResponseRejected(said("request.screen_notes_decline"))
    from justshipit.engineering import specs
    return specs.send_back(s, req, resp)


def _plan_sent_back(s: Session, req: AgentRequest, resp: HumanResponse) -> bool:
    """Whether the owner asked for changes on a plan card: the plan goes back to be written again from the note."""
    if req.action_kind != PLAN_CARD:
        return False
    from justshipit.engineering import plans
    return plans.send_back(s, req, resp)


# How people will use a merged build (engineering/usable.py): a question the server asks under the deploy action,
# which has no connector, so the company deploys nothing.
USE_CARD_KIND = "dev.deploy_production"


def answer_makes_task(req: AgentRequest) -> bool:
    """A question the server asks whose answer the owner then carries out: the way to host or run a merged build
    becomes the owner's to-do with its steps."""
    return (req.type == RequestType.QUESTION.value and req.action_kind == USE_CARD_KIND
            and "use_of_job" in (req.action_params or {}))


# The status a CEO decision takes from its approval card; a card still open, deferred or withdrawn leaves it pending.
_DECISION_FOLLOWS = {RequestStatus.APPROVED.value: "EFFECTIVE", RequestStatus.AUTO_APPROVED.value: "EFFECTIVE",
                     RequestStatus.REJECTED.value: "REJECTED"}


def follow_decision(s: Session, req: AgentRequest) -> None:
    """A decision that needs the owner is in force once its card is approved (Daniel's DEC-4 stayed PENDING_APPROVAL
    after REQ-64 was approved)."""
    dec = s.get(Decision, (req.action_params or {}).get("decision_id") or 0)
    if dec is not None and (to := _DECISION_FOLLOWS.get(req.status)):
        dec.status = to


def _post_edit(req: AgentRequest, resp: HumanResponse) -> PostEdit | None:
    """The owner's rewrite of a draft post, kept only when approving one and only when it changed something; it becomes
    the owner task's copy text. A rewrite sent for a request with no draft post is refused rather than dropped."""
    edit = resp.post_edit
    if edit is None:
        return None
    card = (req.action_params or {}).get("post_card") or {}
    what = card.get("what") or {}
    if card.get("mode") != "draft":
        raise ResponseRejected(said("request.no_draft"))
    if resp.decision != HumanDecision.APPROVE:
        return None
    title = (edit.title or "").strip() or what.get("title")
    body = edit.body.strip()
    if not body:
        raise ResponseRejected(said("request.empty_body"))
    if title == what.get("title") and body == (what.get("body") or "").strip():
        return None
    return PostEdit(title=title, body=body)


def _by_key(s: Session, key: str) -> Job | None:
    """The job queued under this key, when enqueue found it there already."""
    return s.scalar(select(Job).where(Job.idempotency_key == key))


def _followed(s: Session, req: AgentRequest, effect: FollowedEffect, kind: TargetKind, target_id: int,
              option: str | None = None) -> None:
    """Record what deciding the card set going, at the card's revision: the card then says what happened from this,
    not from a prediction (design 2.3)."""
    request_contract.link(s, req.id, (request_contract.Link(Rel.FOLLOWED, kind, target_id, LinkSource.SERVER,
                                                            option=option, effect=effect),), revision(req))


def _replan(s: Session, req: AgentRequest, *, by_owner: bool) -> Job | None:
    """Queue the CEO's re-plan of the work `req`'s answer or expiry changed, on a project with a plan to change. A run
    that waits for the owner resumes only once the re-plan is done (`resume_after`, design 7 Order on resume), since
    the re-plan has its own lock key and would otherwise race the resumed run."""
    p = s.get(Project, req.project_id)
    if p.status != "RUNNING" and p.status not in holds.PARKED:
        return None
    key = _round_key(req, f"realign:{req.id}")
    waits = {"resume_after": True} if p.status in holds.PARKED else {}
    return (queue.enqueue(s, "realign_project", {"project_id": p.id, "request_id": req.id, **waits},
                          idempotency_key=key, max_attempts=2, by_owner=by_owner)
            or _by_key(s, key))


def _maybe_resume(s: Session, req: AgentRequest, *, by_owner: bool = False, replanned: bool = False) -> None:
    """When a waiting project has nothing left to wait for once `req` is resolved, enqueue its resume, which the card
    records as followed. Not while a re-plan that resumes the run itself is still to finish (`holds.replanning`), so
    the run goes on with the plan that re-plan leaves. `replanned`: the caller is that re-plan, done, and only one queued
    behind it still holds the run. `by_owner`: the owner's own answer resolved it, so the resume runs whatever stage is
    open."""
    if not req.project_id:
        return
    p = s.get(Project, req.project_id)
    if p.status != "WAITING_HUMAN" or holds.park_reason(s, p.id) is not None:
        return
    if holds.replanning(s, p.id, besides_running=replanned):
        return
    key = _round_key(req, f"resume:{p.id}:{req.id}")
    if job := queue.enqueue(s, "resume_project", {"project_id": p.id}, idempotency_key=key,
                            by_owner=by_owner) or _by_key(s, key):
        _followed(s, req, FollowedEffect.RESUMES, TargetKind.JOB, job.id)


def resume_replanned(s: Session, req: AgentRequest) -> None:
    """The re-plan `req` queued with `resume_after` is done: the run it kept waiting goes on, when nothing else parks it.
    The owner's answer resumes it whatever stage is open; an expiry's waits for a stage like the company's own work."""
    _maybe_resume(s, req, by_owner=req.status != RequestStatus.EXPIRED.value, replanned=True)


# ------------------------------------------------------------------ when nobody answers (design 7, Expiry)

EXPIRY = "expiry"  # `by` on what the expiry rule wrote: the card's response and its owner_answers entry
EXPIRY_NOTICE = timedelta(days=1)


def defaulted(req: AgentRequest) -> bool:
    """Whether nobody answered the card and it went on with its default: the yes that default stands for, never the
    owner's answer."""
    resp = req.response or {}
    return (req.status == RequestStatus.EXPIRED.value and resp.get("by") == EXPIRY
            and resp.get("decision") == HumanDecision.ANSWER.value)


def _goes_on_with(req: AgentRequest) -> str | None:
    """The option a card past its deadline goes on with: its recorded default, only for a question that spends and sends
    nothing, only its recommended option, and only while the card still offers it as an answer or a yes. Any other card
    only closes, whatever its row says, so a bad row never approves (the contract refuses one when it is filed)."""
    option = req.default_option
    if (req.expiry_policy != ExpiryPolicy.DEFAULT.value or req.type not in QUESTION_TYPES or option is None
            or option != req.recommended_option or request_contract.sends(req.action_kind, req.estimated_cost_usd)):
        return None
    offered = any(c["key"] == option and request_contract.goes_ahead(c["verdict"]) for c in choices(req))
    return option if offered else None


def _option_label(req: AgentRequest, key: str) -> str:
    return next((c["label"] for c in choices(req) if c["key"] == key), key)


def _expiring(project_id: int | None, *when):
    """The project's cards (every project's, with None) that an expiry rule may close: filed under the work path, still
    open, and not waiting with no end."""
    q = select(AgentRequest).where(AgentRequest.status == RequestStatus.PENDING.value, AgentRequest.context_version > 0,
                                   AgentRequest.expiry_policy != ExpiryPolicy.KEEP_WAITING.value, *when)
    return q if project_id is None else q.where(AgentRequest.project_id == project_id)


def expire_due(s: Session, project_id: int | None, now: datetime) -> list[int]:
    """Apply "if you don't answer" to each card past its deadline, of one project or of every one. The worker runs it
    every tick and the run gate before it decides whether the run waits, so a default is recorded before the work it
    frees goes on. Each card is locked, and skipped while another writer holds it: an answer that took the row first
    wins, and the card is no longer PENDING when this could lock it again. Cards from before the work path and cards
    that keep waiting are left alone. It also tells the owner, once, about each card due within a day. Returns the
    cards it expired."""
    lang = app_settings.language()
    due = list(s.scalars(_expiring(project_id, AgentRequest.expires_at <= now).order_by(AgentRequest.id)
                         .with_for_update(skip_locked=True, key_share=True)
                         .execution_options(populate_existing=True)))
    for req in due:
        _expire(s, req, now, lang)
    _warn_expiring(s, project_id, now)
    return [r.id for r in due]


def _expire(s: Session, req: AgentRequest, now: datetime, lang: Lang) -> None:
    """One card past its deadline, under its row lock and then its project's, the order `respond` takes them in: its
    rule applied, and what it did recorded as followed."""
    p = (s.scalar(select(Project).where(Project.id == req.project_id).with_for_update()
                  .execution_options(populate_existing=True)) if req.project_id else None)
    held = [h for h in holds.live_holds(s, p.id) if h.request_id == req.id] if p is not None else []
    option = _goes_on_with(req)
    req.status, req.resolved_at = RequestStatus.EXPIRED.value, now
    if option is not None:
        label = _option_label(req, option)
        req.response = {"decision": HumanDecision.ANSWER.value, "chosen_option": label, "by": EXPIRY}
        _followed(s, req, FollowedEffect.DEFAULTED, TargetKind.REQUEST, req.id, option=option)
        if p is not None:
            ctx = p.context or {}
            p.context = {**ctx, "owner_answers": [*ctx.get("owner_answers", []), {
                "request": display_id(req.id), "title": req.title, "decision": req.status, "answer": label,
                "note": None, "by": EXPIRY}]}
        # The team goes on as the card said answering with this option would.
        _follow_answer(s, req, _answer_uses(s, req, option), replan=False, by_owner=False)
    else:
        req.response = {"by": EXPIRY}
        _followed(s, req, FollowedEffect.CLOSED, TargetKind.REQUEST, req.id)
        _close_held(s, req, held, lang)
        if p is not None and (job := _replan(s, req, by_owner=False)) is not None:
            _followed(s, req, FollowedEffect.REPLANS, TargetKind.JOB, job.id)
    _maybe_resume(s, req)


def _close_held(s: Session, req: AgentRequest, held: list[holds.Hold], lang: Lang) -> None:
    """What a card that closes unanswered does to the tasks it held: one it kept from starting is cancelled, and a
    result that waited on it is rejected. A task that has moved on since (it started, it ended) is left as it is."""
    for h in held:
        t = s.scalar(select(Task).where(Task.id == h.task_id).with_for_update()
                     .execution_options(populate_existing=True))
        if h.phase == HoldPhase.START and t.status in (TaskStatus.PROPOSED.value, TaskStatus.READY.value):
            tasks.transition(t, TaskStatus.CANCELLED,
                             error=msg("work.task.cancelled_by_expiry", lang, ref=display_id(req.id)))
            _followed(s, req, FollowedEffect.CANCELLED, TargetKind.TASK, t.id)
        elif h.phase == HoldPhase.FINISH and t.status == TaskStatus.APPROVAL_REQUIRED.value:
            tasks.transition(t, TaskStatus.REJECTED)
            _followed(s, req, FollowedEffect.REJECTED, TargetKind.TASK, t.id)


def _warn_expiring(s: Session, project_id: int | None, now: datetime) -> None:
    """A Slack notice for each open card due within a day, saying what its rule does at the deadline (the owner's answer
    to open question 2). The queue drops a notice key it has seen, so asking every tick tells the owner once."""
    soon = list(s.scalars(_expiring(project_id, AgentRequest.expires_at > now,
                                    AgentRequest.expires_at <= now + EXPIRY_NOTICE).order_by(AgentRequest.id)))
    if not soon:
        return
    tz = app_settings.effective().usage.timezone
    for req in soon:
        option = _goes_on_with(req)
        queue.notify(s, notices.decision_expiring(req.id, headline(req), req.expires_at, tz,
                                                  _option_label(req, option) if option is not None else None))


class Undo(StrEnum):
    """How hard approving is to take back, in the owner's words."""
    EASY = "easy"
    HARD = "hard"
    NONE = "none"


@dataclass(frozen=True)
class Effect:
    """The work an owner's decision puts on the queue, the sentence that tells the owner what it does, and how hard it
    is to take back (with how, when `says` does not already tell). The sentences are catalog messages."""
    job: str
    payload: dict
    key: str
    says: Said
    undo: Undo
    undo_how: Said | None = None
    max_attempts: int | None = None


_SENDS = {"live": Undo.NONE, "outbox": Undo.EASY, "off": Undo.EASY}


def _effect(req: AgentRequest, status: RequestStatus) -> Effect | None:
    """One table for what a decision sets going: the dispatch below queues it and the read model describes it. An
    automatic approval sets the same work going as the owner's, so neither can skip a step the other takes."""
    p = req.action_params or {}
    if status == RequestStatus.AUTO_APPROVED:
        status = RequestStatus.APPROVED
    if status == RequestStatus.ANSWERED:
        if p.get(SPEC_QUESTION_KEY) and p.get("spec_id"):
            return _spec_question(req)
        if p.get(FIX_QUESTION_KEY):
            return Effect("dev_fix_answered", {"dev_job_id": p[FIX_QUESTION_KEY], "request_id": req.id},
                          f"dev_fix_answered:{req.id}", said("request.effect.dev_fix"), Undo.EASY, max_attempts=1)
        if p.get("dev_job_id"):
            return Effect("dev_resume", {"dev_job_id": p["dev_job_id"]}, f"dev_resume:{req.id}",
                          said("request.effect.dev_resume"), Undo.EASY, max_attempts=1)
        return None
    if status != RequestStatus.APPROVED:
        return None
    kind = req.action_kind
    if kind == "dev.merge" and p.get("dev_job_id"):
        return Effect("dev_merge", {"dev_job_id": p["dev_job_id"], **({"head": p["head"]} if p.get("head") else {})},
                      f"dev_merge:{req.id}",
                      said("request.effect.dev_merge"), Undo.HARD, said("request.effect.dev_merge.undo"), max_attempts=1)
    if kind == PLAN_CARD and p.get("plan_id") and req.project_id:
        return Effect("write_spec", {"project_id": req.project_id, "request_id": req.id}, f"write_spec:{req.id}",
                      said("request.effect.plan_approved"), Undo.EASY)
    if kind == "dev.spec_approve" and p.get("spec_id"):
        return Effect("spec_approved", {"spec_id": p["spec_id"], "request_id": req.id}, f"spec_approved:{req.id}",
                      said("request.effect.spec_approved"), Undo.HARD, said("request.effect.spec_approved.undo"))
    # A development request from before every one was filed as DEV_ASK still starts the spec when it is approved.
    if (kind == DEV_ASK or req.type == RequestType.DEVELOPMENT.value) and req.project_id:
        return Effect("write_spec", {"project_id": req.project_id, "request_id": req.id}, f"write_spec:{req.id}",
                      said("request.effect.write_spec"), Undo.EASY)
    if has_connector(kind):
        mode = app_settings.connector_mode_for(kind) or ""
        sends = said(f"request.effect.sends.{mode if mode in _SENDS else 'none'}")
        return Effect("execute_action", {"request_id": req.id}, f"execute_action:{req.id}",
                      said("request.effect.execute", sends=sends), _SENDS.get(mode, Undo.EASY), max_attempts=1)
    if kind == BUDGET_RAISE and req.project_id and (req.estimated_cost_usd or 0) > 0:
        return Effect("raise_budget", {"request_id": req.id}, f"raise_budget:{req.id}",
                      said("request.effect.budget_raise", amount=f"{req.estimated_cost_usd:g}"), Undo.EASY,
                      said("request.effect.budget_raise.undo"), max_attempts=1)
    if kind == "project.reassess" and req.project_id:
        return Effect("reassess_project", {"project_id": req.project_id, "request_id": req.id}, f"reassess:{req.id}",
                      said("request.effect.reassess"), Undo.EASY, max_attempts=1)
    if kind and kind.startswith("skill.activate") and p.get("skill_id"):
        return Effect("skill_activate", {"skill_id": p["skill_id"]}, f"skill_activate:{req.id}",
                      said("request.effect.skill"), Undo.HARD)
    return None


def _spec_question(req: AgentRequest) -> Effect:
    return Effect("spec_questions", {"spec_id": req.action_params["spec_id"]}, f"spec_questions:{req.id}",
                  said("request.effect.spec_question"), Undo.EASY)


def _bound_to_bet(req: AgentRequest) -> bool:
    return req.action_kind in BET_CARDS and isinstance((req.action_params or {}).get("bet_id"), int)


def _queued(s: Session, req: AgentRequest, job: Job | None, key: str) -> None:
    """Record the server's work for the decision as followed: the job just queued, or the one already queued under its
    key. Work the gate skipped is not recorded, since it does not run."""
    if (job := job or _by_key(s, key)) is not None:
        _followed(s, req, FollowedEffect.QUEUED, TargetKind.JOB, job.id)


def _maybe_dispatch_side_effects(s: Session, req: AgentRequest, *, by_owner: bool = False) -> None:
    """Decisions that carry follow-up work are routed to their handlers via the queue, and the card records each job
    as followed. A card the server bound to a bet changes that bet in this transaction. `by_owner`: the owner decided
    it, so its work runs whatever stage is open; an automatic approval's work waits for a stage like anything else the
    company starts."""
    if _bound_to_bet(req):
        from justshipit.services import bet_loop
        bet_loop.on_card_decided(s, req, lang=app_settings.language(), by_owner=by_owner)
        return
    if req.action_kind == PLAN_CARD:
        from justshipit.engineering import plans
        plans.follow(s, req)
    # A spec question settles however the owner closed it: the spec does not wait on a card put off (questions.py).
    if (req.status in (RequestStatus.REJECTED.value, RequestStatus.DEFERRED.value)
            and (req.action_params or {}).get(SPEC_QUESTION_KEY)):
        effect = _spec_question(req)
        _queued(s, req, queue.enqueue(s, effect.job, effect.payload, idempotency_key=effect.key, by_owner=by_owner),
                effect.key)
        return
    if (req.status in (RequestStatus.REJECTED.value, RequestStatus.DEFERRED.value) and req.action_kind == "dev.merge"
            and req.action_params.get("dev_job_id")):
        key = f"dev_merge_declined:{req.id}"
        _queued(s, req, queue.enqueue(s, "dev_merge_declined", {"dev_job_id": req.action_params["dev_job_id"],
                                                                "request_id": req.id},
                                      idempotency_key=key, by_owner=by_owner), key)
        return
    if (effect := _effect(req, RequestStatus(req.status))) is not None:
        extra = {} if effect.max_attempts is None else {"max_attempts": effect.max_attempts}
        _queued(s, req, queue.enqueue(s, effect.job, effect.payload, idempotency_key=effect.key, by_owner=by_owner,
                                      **extra), effect.key)


# Cards whose approval builds on what the owner's own checks find: while one is open, the card says so.
BUILDS_ON_CHECKS = frozenset({"dev.spec_approve"})

BET_CARD_SAYS = {
    ROUTINE_CARD: ("request.effect.routine", "request.effect.routine.undo"),
    "project.direction_change": ("request.effect.direction_change", None),
}


@dataclass(frozen=True)
class Outcome:
    """What approving does at once, from what the server will queue and not from what an agent claims, and how hard
    that is to take back. `undo` is None when the server cannot tell and the card does not say; `undo_by` is "agent"
    when only the card's claim speaks for it."""
    says: str
    undo: Undo | None
    undo_how: str | None
    undo_by: str | None


def _owner_task_says(req: AgentRequest) -> Said:
    """What approving a card that becomes the owner's own to-do does, by the kind of to-do it becomes. A money card
    says the project's budget stays as it is: the only way to raise it is the project's settings."""
    kind = owner_tasks.task_kind(req)
    if kind == "spend" and req.action_kind == "budget.spend":
        return said("request.effect.owner_task.budget")
    # a channel that takes only points gets the facts and a checklist: the owner writes the post (channels.yaml mode)
    if kind == "post" and ((req.action_params or {}).get("post_card") or {}).get("mode") == "outline":
        return said("request.effect.owner_task.post_outline")
    return said(f"request.effect.owner_task.{kind}" if kind in ("post", "publish", "spend", "check")
                else "request.effect.owner_task")


def outcome(s: Session, req: AgentRequest, lang: Lang) -> Outcome:
    answers = req.type == RequestType.QUESTION.value
    claimed = ((req.action_params or {}).get("card") or {}).get("reversible")
    by_card = (Undo.EASY if claimed else Undo.NONE, None, "agent") if isinstance(claimed, bool) else (None, None, None)
    pays = False  # a to-do that is not itself the payment but costs money: the owner pays that too
    if effect := _effect(req, RequestStatus.ANSWERED if answers else RequestStatus.APPROVED):
        says, undo = effect.says, (effect.undo, effect.undo_how, "server")
    elif answer_makes_task(req):
        says = said("request.effect.owner_task")
        undo = (Undo.EASY, said("request.effect.owner_task.undo"), "server")
    elif answers:
        # The way to correct an answer is an instruction on its project; a company-wide answer has none to give.
        says, undo = said("request.effect.answered"), ((Undo.EASY, said("request.effect.answered.undo"), "server")
                                                       if req.project_id else (None, None, None))
    elif _bound_to_bet(req):
        key, how = BET_CARD_SAYS[req.action_kind]
        says, undo = said(key), (Undo.EASY, said(how) if how else None, "server")
    elif kind := owner_tasks.task_kind(req):
        pays = kind != "spend" and (req.estimated_cost_usd or 0) > 0
        if revised := owner_tasks.revised_task(s, req, lock=False):
            says = said("request.effect.owner_task.revises", title=owner_text.title(revised.title, 80))
        elif talk := owner_tasks.talk_with(s, req, lock=False):
            says = said("request.effect.owner_task.joins", title=owner_text.title(talk.title, 80))
        else:
            says = _owner_task_says(req)
        undo = (Undo.EASY, said("request.effect.owner_task.undo"), "server")
    elif _record_only(req):
        says, undo = said("request.effect.record_only"), (Undo.EASY, None, "server")
    elif req.action_kind:
        # Approving a card with options records the chosen one where every agent of the project reads it (`respond`).
        follows = bool(req.project_id and req.options)
        says, undo = said("request.effect.team_follows" if follows else "request.effect.by_hand"), by_card
    else:
        says, undo = said("request.effect.record"), by_card
    sentences = [say(says, lang)]
    if pays:
        sentences.append(msg("request.effect.owner_pays", lang, cost=f"${req.estimated_cost_usd:g}"))
    if (req.action_kind in BUILDS_ON_CHECKS and req.project_id
            and (checks := owner_tasks.open_checks(s, req.project_id))):
        titles = ", ".join(f"'{one_line(x, 60)}'" for x in checks)
        sentences.append(msg("request.effect.open_checks", lang, titles=titles))
    if (req.project_id and (p := s.get(Project, req.project_id)) and p.status == "WAITING_HUMAN"
            and req.id in {r.id for r in holds.parking(s, p.id)}):
        frees = holds.park_reason(s, p.id, besides=req.id) is None
        sentences.append(msg("request.effect.resumes" if frees else "request.effect.still_waits", lang))
    level, how, by = undo
    return Outcome(". ".join(sentences) + ".", level, say(how, lang) if how else None, by)


def on_approve(s: Session, req: AgentRequest, lang: Lang) -> str:
    return outcome(s, req, lang).says


def _record_only(req: AgentRequest) -> bool:
    """A kind the CEO may decide, under its own name, that sends nothing and queues nothing: approving only records."""
    return (req.action_kind in policy.PROJECT_MANAGER_DECIDABLE and "proposed_action_kind" not in (req.action_params or {})
            and _effect(req, RequestStatus.APPROVED) is None)


# The sentence `respond` refuses an approval with when the typed number is missing or wrong, by `typed_confirm_why`.
_TYPED_REFUSAL = {"strong": "request.typed_confirm", "unknown_spend": "request.typed_confirm.unknown_spend"}


def typed_confirm(req: AgentRequest) -> bool:
    """Whether approving takes the request id typed in. The console and `respond` both ask this."""
    return typed_confirm_why(req) is not None


def typed_confirm_why(req: AgentRequest) -> str | None:
    """Why approving takes the request id typed in: "strong" when the policy asks for a strong approval
    (`_strong_typed`), "unknown_spend" when money goes out on an action the company has no rule for
    (`policy.no_rule_spend`), which the policy may otherwise let through with a plain approve. None when it doesn't."""
    if _strong_typed(req):
        return "strong"
    if policy.no_rule_spend(req.action_kind, req.estimated_cost_usd):
        return "unknown_spend"
    return None


def _strong_typed(req: AgentRequest) -> bool:
    """Whether a strong approval takes the request id typed in. A typed id guards what the company does that cannot be
    taken back, not the owner's own to-do. So every strong approval takes it but one: something the owner does by hand
    (a post, a flyer, a message they send themselves) filed as an `external.other` request under that name, carrying a
    post card or a handout the server screened, which the owner has not made strong in Settings, which queues and sends
    nothing, and which costs nothing. That one is strong only because `external.other` is the catch-all; approving it
    makes a to-do. A cost left out is no money to spend only when the owner sends or posts the text: steps to follow
    (install an app, open an account) may cost what the agent did not say. The exception rests on what the server
    checked, never on the risk the agent rated, which can only make the id required."""
    if req.policy_mode != PolicyMode.STRONG_APPROVE.value or answer_makes_task(req):
        return False
    params = req.action_params or {}
    by_hand = (req.action_kind == "external.other" and "proposed_action_kind" not in params
               and bool(params.get("post_card") or params.get("handout"))
               and req.action_kind not in app_settings.effective().policy.overrides)
    acts = _effect(req, RequestStatus.APPROVED) is not None or has_connector(req.action_kind)
    sends_text = bool(params.get("post_card")) or (params.get("handout") or {}).get("kind") == "message"
    free = req.estimated_cost_usd == 0 or (req.estimated_cost_usd is None and sends_text)
    return not (by_hand and free) or acts or req.risk_level in (RiskLevel.HIGH.value, RiskLevel.CRITICAL.value)


# ------------------------------------------------------------------ the report the owner reads

QUESTION_TYPES = {RequestType.QUESTION.value, RequestType.ESCALATION.value, RequestType.CLARIFICATION.value}


def same_decision(verdict: str, decision: str) -> bool:
    """An approval and an answer are the same yes: a question's answer is sent either way."""
    yes = {HumanDecision.APPROVE.value, HumanDecision.ANSWER.value}
    return verdict == decision or (verdict in yes and decision in yes)


def _distinct(text: str, seen: set[str]) -> str:
    out, n = text, 2
    while out in seen:
        out, n = f"{text} ({n})", n + 1
    seen.add(out)
    return out


def choices(req: AgentRequest, lang: Lang = DEFAULT) -> list[dict]:
    """The options the owner picks from, each with the decision it is: the card's typed options when there is a card,
    the request's own words otherwise, typed by `infer_verdict` (None when they do not say). `key` is the option as
    stored, which `respond` matches; `label` is scrubbed for reading, so a label the scrub empties ("send_now") keeps
    its code name, and one that reads like an earlier one is numbered."""
    question = req.type in QUESTION_TYPES
    card_options = ((req.action_params or {}).get("card") or {}).get("options")
    options = ([(o["label"], o) for o in card_options] if card_options
               else [(o, {"recommended": o == req.recommended_option}) for o in (req.options or [])])
    keys: set[str] = set()
    labels: set[str] = set()
    out = []
    for i, (text, o) in enumerate(options):
        label = one_line(text, 40) or one_line(text, 40, names=True) or msg("request.option", lang, n=i + 1)
        out.append({"key": _distinct(text, keys), "label": _distinct(label, labels), "summary": o.get("summary") or None,
                    "risks": o.get("risks") or [], "evidence_ids": o.get("evidence_ids") or [],
                    "recommended": bool(o.get("recommended")), "plan_line": (o.get("line") or {}).get("text"),
                    "verdict": o.get("verdict") or infer_verdict(text, question)})
    return out


def _picked(req: AgentRequest, resp: HumanResponse, lang: Lang) -> dict | None:
    """The option the owner chose, by its key, or by the label a console from before keys sent, labelled in `lang`
    as the card showed it. A yes naming an option the request does not offer is refused: the owner would be approving
    something the card never showed."""
    if not resp.chosen_option:
        return None
    offered = choices(req, lang)
    chosen = resp.chosen_option
    found = (next((c for c in offered if c["key"] == chosen), None)
             or next((c for c in offered if c["label"] in (chosen, one_line(chosen, 40))), None))
    if found is None and offered and resp.decision in (HumanDecision.APPROVE, HumanDecision.ANSWER):
        raise ResponseRejected(said("request.not_an_option", label=one_line(chosen, 40)))
    return found


_FIRST_SENTENCE = re.compile(r"^.+?[.!?。](?=\s|$)", re.DOTALL)


def _first_sentence(text: str | None, limit: int) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    m = _FIRST_SENTENCE.match(text)
    return one_line(m.group(0) if m else text, limit) or None


def headline(req: AgentRequest) -> str:
    """The conclusion line: the card's headline, a card's question from before headlines, or the title."""
    card = (req.action_params or {}).get("card") or {}
    return one_line(card.get("headline") or card.get("question") or "", 70) or owner_text.title(req.title, 80)


# The owner's own part of a request whose card names none, by who carries the action out: the owner does what
# becomes an owner task; for anything else the company acts on the approval.
_OWNER_PART = ("post", "publish", "spend", "other", "check")


def owner_does(req: AgentRequest, lang: Lang) -> str:
    """What the owner does: the card's line, else one derived from the request's kind, so older cards and cards
    whose agent left it out still say it."""
    card = (req.action_params or {}).get("card") or {}
    if card.get("owner_does"):
        return one_line(card["owner_does"], 90)
    if req.type in QUESTION_TYPES:
        return msg("request.owner_does.pick" if req.options else "request.owner_does.write", lang)
    kind = owner_tasks.task_kind(req)
    if kind in _OWNER_PART:
        return msg(f"request.owner_does.{kind}", lang)
    # "The company carries it out" only where approving sets company work going; otherwise approving records the
    # decision, and saying the company acts on it would contradict what approving does.
    acts = (_effect(req, RequestStatus.APPROVED) is not None or _bound_to_bet(req)
            or bool(not kind and req.action_kind and req.project_id and req.options))  # outcome's "team_follows"
    return msg("request.owner_does.decide" if acts else "request.owner_does.decide_only", lang)


def why(req: AgentRequest) -> str | None:
    card = (req.action_params or {}).get("card") or {}
    # A spec card's summary was the PRD's path and commit until it was written for the owner; its rationale is the
    # problem the development solves.
    spec_reason = _first_sentence(req.rationale, 120) if req.action_kind == "dev.spec_approve" else None
    return ((one_line(card["why"], 120) if card.get("why") else None)
            or _first_sentence(card.get("recommendation"), 120) or spec_reason or _first_sentence(req.summary, 120))


def report(s: Session, req: AgentRequest, lang: Lang) -> dict:
    """The request as a report to the owner: the conclusion, what the owner does, one line of why, what approving does
    and how hard it is to take back, then the options and, for a post, the post itself. Cards from before these
    fields, and requests the server made without a card, read into the same shape."""
    out = outcome(s, req, lang) if req.status == RequestStatus.PENDING.value else None
    return {
        "headline": headline(req),
        "owner_does": owner_does(req, lang),
        "why": why(req),
        "on_approve": out.says if out else None,
        "undo": ({"level": out.undo.value, "says": msg(f"request.undo.{out.undo.value}", lang), "how": out.undo_how,
                  "by": out.undo_by} if out and out.undo else None),
        "typed_confirm": bool(out) and typed_confirm(req),
        "typed_confirm_why": typed_confirm_why(req) if out else None,
        "choices": choices(req, lang),
        "post": owner_tasks.readable_card((req.action_params or {}).get("post_card"), lang, req.title,
                                           req.status == RequestStatus.PENDING.value),
        "handout": (req.action_params or {}).get("handout"),
        "post_edit": (req.response or {}).get("post_edit"),
        "closed": closed_why(req, lang),
        "revises": _revises(s, req, lang),
        "reopen": _reopen_state(s, req, lang),
        "reopen_later": ({"defer": reopen_barrier(req, RequestStatus.DEFERRED.value) is None,
                          "reject": reopen_barrier(req, RequestStatus.REJECTED.value) is None} if out else None),
    }


def _reopen_state(s: Session, req: AgentRequest, lang: Lang) -> dict | None:
    """For a held or declined request: whether the owner can reopen it, and if not, why in one owner line."""
    if req.status not in _REOPENABLE:
        return None
    why = reopen_refusal(s, req)
    return {"allowed": why is None, "why": say(why, lang) if why else None}


def closed_why(req: AgentRequest, lang: Lang) -> str | None:
    """Why a request the owner did not decide was settled, in one owner line: who withdrew it and why ("취소 · 시스템이
    정함" told 박정숙 nothing as she was approving REQ-76), that the policy approved it ("Auto-approved" on a build
    the owner had not approved read as the system deciding for them), or that nobody answered by the deadline and what
    the card's rule did then."""
    resp = req.response or {}
    if req.status == RequestStatus.EXPIRED.value and resp.get("by") == EXPIRY:
        return (msg("request.closed.expired_default", lang, option=resp.get("chosen_option") or "") if defaulted(req)
                else msg("request.closed.expired", lang))
    if req.status == RequestStatus.CANCELLED.value and resp.get("decision") == "withdrawn":
        return msg("request.closed.withdrawn_ceo" if roles.current(resp.get("by") or "") == roles.PROJECT_MANAGER
                   else "request.closed.withdrawn", lang,
                   why=owner_text.detail(resp.get("note") or "")).strip()
    if req.status != RequestStatus.AUTO_APPROVED.value or decided_by_project_manager(req):
        return None
    effect = _effect(req, RequestStatus.APPROVED)
    key = f"request.closed.auto.{effect.job}" if effect else ""
    return msg(key if key in catalog() else "request.closed.auto", lang)


def _revises(s: Session, req: AgentRequest, lang: Lang) -> str | None:
    """The approval this card changes, as the card names it: approving the card moves that approval's to-do here."""
    rid = (req.action_params or {}).get("revises")
    old = s.get(AgentRequest, rid) if rid else None
    return msg("request.revises", lang, title=owner_text.title(old.title, 80)) if old else None


def decided_by_project_manager(req: AgentRequest) -> bool:
    """Whether the Project Manager answered under a decision rule, not the owner or the policy."""
    return (req.response or {}).get("responded_by") in responders.DECIDED_UNDER_A_RULE


def read_model(s: Session, req: AgentRequest, lang: Lang, owner_strengths: Strengths) -> dict:
    """The request as the console reads it, the server's words in `lang`. `report` is the server's reading; `card` is
    what the agent wrote, kept as its narrative. `business_id` is the business of the request's project, for a link to
    that business's own screens. `fit` says which of the card's areas the owner is strong or new in,
    so the console opens the detail of one and leads with the conclusion in the other."""
    row = {c.name: getattr(req, c.name) for c in req.__table__.columns}
    pending_now = req.status == RequestStatus.PENDING.value
    project = s.get(Project, req.project_id) if req.project_id else None
    return {**row, "business_id": project.business_id if project else None, "title": owner_text.title(req.title), "revision": revision(req), "by_project_manager": decided_by_project_manager(req),
            "card": (req.action_params or {}).get("card"), "report": report(s, req, lang),
            "fit": strengths.fit(strengths.card_areas(req.action_kind, req.from_agent), owner_strengths),
            "dev_budget": _dev_budget(s, req) if pending_now else None, **_merge_card(s, req), **email_preview(req)}


EMAIL_SEND = "external.email_send"


def email_preview(req: AgentRequest) -> dict:
    """What an email approval sends, read from the payload the gateway sends (operations/gateway.email_send): its
    recipients, subject and body, so the owner reads them before approving. `email_preview_missing` names the first
    part that cannot be read, the same parts the gateway refuses to send without, so the console can hold the approval
    and say why. Other kinds carry neither field."""
    if req.action_kind != EMAIL_SEND:
        return {}
    payload = (req.action_params or {}).get("payload")
    if not isinstance(payload, dict):
        return {"email_preview": None, "email_preview_missing": "no_payload"}
    raw = payload.get("to")
    to = [str(x).strip() for x in (raw if isinstance(raw, list) else str(raw or "").split(",")) if str(x).strip()]
    subject, body = str(payload.get("subject") or "").strip(), str(payload.get("body") or "").strip()
    missing = next((why for why, part in (("no_recipient", to), ("no_subject", subject), ("no_body", body)) if not part),
                   None)
    return {"email_preview": {"to": to, "subject": subject, "body": payload.get("body") or ""},
            "email_preview_missing": missing}


def _merge_card(s: Session, req: AgentRequest) -> dict:
    """A merge card's way to try the build before deciding, what to check in it, and what the budget lacks for the fix
    a rejection with a note starts (engineering/try_it.py, engineering/merge_review.py, engineering/revise.py)."""
    job_id = (req.action_params or {}).get("dev_job_id")
    if req.action_kind != "dev.merge" or not isinstance(job_id, int):
        return {}
    from justshipit.db.models import DevJob, Spec
    from justshipit.engineering import merge_review, revise, team, try_it
    job = s.get(DevJob, job_id)
    spec = s.get(Spec, job.spec_id) if job is not None else None
    if spec is None:
        return {}
    repository = (spec.handoff or {}).get("target", {}).get("repository") or spec.target_repository
    head = (req.action_params or {}).get("head")
    return {"try_it": try_it.card(job, repository),
            "review": merge_review.card(job, repository, head if isinstance(head, str) else None),
            "team": team.of_build(s, job.id), "fix_budget": revise.shortfall(s, spec.project_id)}


def _dev_budget(s: Session, req: AgentRequest) -> dict | None:
    """What the project's budget lacks for the development this approval starts, so the owner sees it before
    approving instead of in a dev job that fails on its first call."""
    from justshipit.services import budget
    starts_dev = req.type == RequestType.DEVELOPMENT.value or req.action_kind in (PLAN_CARD, "dev.spec_approve")
    return budget.dev_shortfall(s, req.project_id) if starts_dev and req.project_id else None


def _usable_grants(action_kind: str, project_id: int | None, request_id: int | None):
    q = select(Grant).where(
        Grant.action_kind == action_kind, Grant.revoked.is_(False), Grant.expires_at > func.clock_timestamp(),
        Grant.uses < Grant.max_uses, (Grant.project_id == project_id) | (Grant.project_id.is_(None)))
    return q if request_id is None else q.where(Grant.request_id == request_id)


def grant_left(s: Session, action_kind: str, project_id: int | None, amount_usd: float = 0.0, *,
               request_id: int | None = None) -> bool:
    """Whether consume_grant would find a grant now, so a retry the console offers can spend one."""
    return s.scalar(_usable_grants(action_kind, project_id, request_id).where(
        Grant.max_amount_usd.is_(None) | (Grant.amount_used_usd + amount_usd <= Grant.max_amount_usd)
    ).limit(1)) is not None


def consume_grant(s: Session, action_kind: str, project_id: int | None, amount_usd: float = 0.0, *,
                  request_id: int | None = None) -> Grant:
    """Tool Gateway check right before executing a side effect. Raises if no valid grant. With `request_id` only the
    grant that request's approval made is spent: a grant carries what the owner approved (the connector's mode, the
    amount, the expiry), which another request of the same kind must not borrow."""
    grants = s.scalars(_usable_grants(action_kind, project_id, request_id).order_by(Grant.id)
                       .with_for_update()).all()
    # The lock can wait on another spend, so expiry is judged by the database clock once it is held.
    now = s.scalar(select(func.clock_timestamp()))
    for g in grants:
        if g.expires_at > now and (g.max_amount_usd is None or g.amount_used_usd + amount_usd <= g.max_amount_usd):
            g.uses += 1
            g.amount_used_usd += amount_usd
            return g
    raise GrantRefused(f"no valid grant for {action_kind} (project {project_id})")


def in_business(business_id: int):
    """The requests one business shows: those of its projects, and the company-wide ones, which belong to the owner
    and so to every business."""
    mine = select(Project.id).where(Project.business_id == business_id)
    return or_(AgentRequest.project_id.is_(None), AgentRequest.project_id.in_(mine))


def pending(s: Session, project_id: int | None = None, business_id: int | None = None) -> list[AgentRequest]:
    q = select(AgentRequest).where(AgentRequest.status == RequestStatus.PENDING.value)
    if project_id:
        q = q.where(AgentRequest.project_id == project_id)
    if business_id is not None:
        q = q.where(in_business(business_id))
    return list(s.scalars(q.order_by(AgentRequest.blocking.desc(), AgentRequest.id)))


def budget_ask(r: AgentRequest) -> dict | None:
    """The open card that asks to add to a project's budget, as the console links it, or None for any other card."""
    if r.action_kind != BUDGET_RAISE or not r.project_id or (r.estimated_cost_usd or 0) <= 0:
        return None
    return {"id": r.id, "title": r.title, "amount_usd": r.estimated_cost_usd}


def withdraw_pending(s: Session, request_id: int, *, by: str, note: str) -> bool:
    """Cancel a request still waiting for an answer because the work it asked about changed. False when it is no longer
    pending, which leaves the owner's answer alone."""
    req = s.scalar(select(AgentRequest).where(AgentRequest.id == request_id).with_for_update(key_share=True))
    if req is None or req.status != RequestStatus.PENDING.value:
        return False
    req.status = RequestStatus.CANCELLED.value
    req.resolved_at = datetime.now(timezone.utc)
    req.response = {"decision": "withdrawn", "by": by, "note": note}
    return True


def consolidate(s: Session, project_id: int, merges: list, withdrawals: list, *, by: str = "project_manager") -> dict:
    """Apply an inbox cleanup: merged duplicates are cancelled into the kept request, obsolete ones
    are withdrawn. Only the project's still-pending requests can be touched, and never a card the server bound to its
    own flow (a spec, a merge, a bet): withdrawing a spec card would leave its spec waiting on a card nobody sees.
    Each card is locked and read again, and one an answer or an expiry holds is left alone: the owner's answer wins,
    and an answer that waits on this lock is refused as already decided. Skipping never waits, so a realign holding
    its project's row cannot deadlock with an answer that holds the card and waits for that row."""
    lang = app_settings.language()
    open_ids = {r.id: r for r in s.scalars(
        select(AgentRequest).where(AgentRequest.project_id == project_id,
                                   AgentRequest.status == RequestStatus.PENDING.value)
        .order_by(AgentRequest.id).with_for_update(skip_locked=True, key_share=True)
        .execution_options(populate_existing=True))}
    bound = {i for i, r in open_ids.items() if matters.server_bound(r)}
    done = {"merged": [], "withdrawn": []}
    for m in merges:
        keep = open_ids.get(m.keep_id)
        dups = [open_ids[i] for i in m.merge_ids if i in open_ids and i != m.keep_id and i not in bound]
        if keep is None or not dups:
            continue
        if keep.id not in bound:
            keep.title = m.merged_title[:300] or keep.title
        # The kept card now reads differently, so a decision on what the owner read before is refused.
        keep.action_params = {**(keep.action_params or {}), "revision": revision(keep) + 1}
        for d in dups:
            merged = msg("request.note.merged", lang, ref=display_id(d.id), agent=d.from_agent)
            keep.rationale = f"{keep.rationale}\n\n{merged} {d.title}: {d.summary}"
            _hold(s, keep, HoldScope(d.hold_scope))
            d.status = RequestStatus.CANCELLED.value
            d.resolved_at = datetime.now(timezone.utc)
            d.response = {"decision": "merged", "into": display_id(keep.id), "by": by, "note": m.reason}
            open_ids.pop(d.id, None)
            done["merged"].append(d.id)
        _tell_owner(s, keep)
    for w in withdrawals:
        r = open_ids.get(w.id)
        if r is None or r.id in bound:
            continue
        r.status = RequestStatus.CANCELLED.value
        r.resolved_at = datetime.now(timezone.utc)
        r.response = {"decision": "withdrawn", "by": by, "note": w.reason}
        done["withdrawn"].append(r.id)
        _maybe_resume(s, r)
    return done
