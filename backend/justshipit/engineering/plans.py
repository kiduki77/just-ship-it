"""Product plans: before any spec, the product agent writes what a build is for. The problem and who it serves, what
the first version does and leaves out, how to tell it worked, and what every line rests on. The owner approves that
plan on a card that holds up the work, and the spec is written from the approved version, which the spec records
(`Spec.plan_commit_sha`). Each version of a plan is a commit of plan.md under the plan's one key."""
from __future__ import annotations

import re
from enum import StrEnum

from sqlalchemy import Sequence, select

from justshipit.agents.executor import run_agent
from justshipit.contracts.handoff import SpecStatus
from justshipit.contracts.owner_text import one_line
from justshipit.contracts.plan import PlanDraftOut, PlanLineOut, PlanStatus, SuccessOut
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import (
    AgentRequestDraft, CardOption, DecisionCard, HumanDecision, HumanResponse, RequestStatus, RequestType, RiskLevel,
)
from justshipit.contracts.work import TaskStatus
from justshipit.db.models import AgentRequest, Business, Job, Plan, Project, Spec, Task
from justshipit.db.session import session_scope
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import said
from justshipit.orchestration import context as ctx
from justshipit.services import activity, app_settings, evidence, matters, queue, registry, requests
from justshipit.services.evidence import Grade
from justshipit.services.fence import fenced
from justshipit.services.tasks import display_id

FILE = "plan.md"
# The usage purpose of the plan's model call: development's first step, so it spends the share kept for development.
PURPOSE = "product_plan"

PLAN_ASK = (
    "Write the product plan the owner approves before any spec is written. It sets the direction: the problem this "
    "build solves and for whom, what its first version does and leaves out, and how anyone can tell it worked. Write "
    "each line as one plain sentence the owner reads, and never how it is built (frameworks, "
    "files, APIs): that is the spec's job, written later from this plan.\n"
    "Every line says what it rests on. basis verified only when a checked fact on the evidence list backs it, and put "
    "that EV id in refs; sourced when a task result or a cited source backs it, with the T and EV ids in refs; "
    "assumed when it is your inference or a guess. Mark a line assumed rather than stretch a source: the server checks "
    "each ref and shows the owner the weaker of what you said and what it found.\n"
    "Keep scope to the smallest version that tests the core hypothesis, and say in not_doing what it leaves out on "
    "purpose. Each success line is something one can count or see once the product is out, and its check says how and "
    "where. In risks, put what could make the plan wrong and the questions still open.")

_REF = re.compile(r"(?:T|EV)-\d+")
_TASK = re.compile(r"T-(\d+)")
_SAID = {"verified": Grade.FIRM, "sourced": Grade.SOURCED, "assumed": Grade.ASSUMED}
_STRONGEST_FIRST = list(Grade)
# plan.md's sections, in order: the field of PlanDraftOut and its heading.
SECTIONS = (("goal", "plan.doc.goal"), ("users", "plan.doc.users"), ("scope", "plan.doc.scope"),
            ("not_doing", "plan.doc.not_doing"), ("success", "plan.doc.success"), ("evidence", "plan.doc.evidence"),
            ("risks", "plan.doc.risks"))
OPTIONS = ("approve", "changes", "hold", "decline")
_VERDICT = {"approve": HumanDecision.APPROVE, "changes": HumanDecision.REJECT, "hold": HumanDecision.DEFER,
            "decline": HumanDecision.REJECT}


def begin(s, p: Project, need: dict) -> Plan:
    """A new plan for the build `need` asks for, queued to be written. In the caller's transaction."""
    n = s.scalar(select(Sequence("plans_id_seq").next_value()))
    key = f"PLAN-{n:04d}"
    plan = Plan(id=n, plan_key=key, project_id=p.id, title=need["title"][:300], status=PlanStatus.DRAFT.value,
                registry_path=f"projects/{p.slug}/plans/{key}", need=need)
    s.add(plan)
    s.flush()
    queue.enqueue(s, "write_plan", {"project_id": p.id, "plan_id": plan.id}, idempotency_key=f"write_plan:{plan.id}")
    return plan


def write_plan(project_id: int, plan_id: int) -> None:
    """Handle `write_plan`: the product agent writes the plan's first version and the owner gets its card. A plan
    already written is left as it is, so a run again does nothing."""
    _write(project_id, plan_id, was=None)


def _waiting(plan: Plan | None, was: str | None) -> bool:
    """Whether the plan still waits for the version this run writes: in DRAFT at the commit the run answers."""
    return plan is not None and plan.status == PlanStatus.DRAFT.value and plan.draft_commit_sha == was


def _write(project_id: int, plan_id: int, *, was: str | None, note: str = "", of: dict | None = None) -> None:
    """Write the plan's next version: the first one (`was` None), or one from the owner's `note` on the version at
    `was`, whose card `of` names. The card goes up on the new commit."""
    lang = app_settings.language()
    with session_scope() as s:
        plan = s.get(Plan, plan_id)
        if not _waiting(plan, was):
            return
        prompt = _prompt(s, plan, note)
    out = run_agent("product", PURPOSE, prompt, PlanDraftOut, project_id=project_id)
    with session_scope() as s:
        matters.lock(s, project_id)
        plan = s.get(Plan, plan_id, with_for_update=True, populate_existing=True)
        if not _waiting(plan, was):
            return
        graded, title = _graded(s, plan.project_id, out), _flat(out.title)
        sha = registry.write_files({f"{plan.registry_path}/{FILE}": markdown(plan.plan_key, title, graded, lang)},
                                   f"[{plan.plan_key}] {'Redraft' if was else 'Draft'} plan: {title}")
        plan.title, plan.draft_commit_sha, plan.status = title[:300], sha, PlanStatus.IN_REVIEW.value
        _raise_card(s, plan, graded, lang, of)


def _prompt(s, plan: Plan, note: str) -> str:
    from justshipit.engineering.specs import task_results
    p = s.get(Project, plan.project_id)
    need = {k: v for k, v in plan.need.items() if k != "extra_needs"}
    asked = "\n".join([ctx.as_json(need), note, *(display_id(i) for i in need.get("task_ids") or [])])
    last = (registry.read_at(f"{plan.registry_path}/{FILE}", plan.draft_commit_sha) if plan.draft_commit_sha else "")
    return "\n\n".join(x for x in [
        ctx.project_brief(s, p, reader="product"),
        f"## The build to plan\n{ctx.as_json(need)}",
        ("## Other asks for the same product (fold in what fits the build)\n" + ctx.as_json(plan.need["extra_needs"]))
        if plan.need.get("extra_needs") else "",
        task_results(s, p, asked),
        ctx.evidence_ledger(s, p, 200),
        ("## Your last version of this plan\n" + fenced(FILE, ctx.clip(last, 12000, FILE))) if last else "",
        ("## What the owner asked to change\n" + fenced("Owner's notes", note)) if note else "",
        PLAN_ASK,
    ] if x)


def grade(s, project_id: int, line: PlanLineOut, tasks: set[int]) -> Grade:
    """What a line rests on as the owner reads it: never firmer than the writer said; firm only on checked evidence it
    cites; source only at most on unchecked evidence or the result of one of the project's finished tasks (`tasks`)."""
    found = evidence.support(s, project_id, line.refs)
    if found == Grade.ASSUMED and any(m and int(m.group(1)) in tasks for m in map(_TASK.fullmatch, line.refs)):
        found = Grade.SOURCED
    return max(_SAID[line.basis], found, key=_STRONGEST_FIRST.index)


def _graded(s, project_id: int, out: PlanDraftOut) -> list[tuple[str, list[tuple[PlanLineOut, Grade]]]]:
    tasks = set(s.scalars(select(Task.id).where(Task.project_id == project_id, Task.status == TaskStatus.DONE.value)))
    return [(key, [(x, grade(s, project_id, x, tasks)) for x in getattr(out, field)]) for field, key in SECTIONS]


def markdown(key: str, title: str, graded: list[tuple[str, list[tuple[PlanLineOut, Grade]]]], lang: Lang) -> str:
    """plan.md: every line ends with what it rests on, in the words the evidence list uses, and the ids it cites. The
    writer's text is put on one line without bold, so it cannot add a line or a label that reads as the server's."""
    def line(x: PlanLineOut, g: Grade) -> str:
        refs = ", ".join(r.strip() for r in x.refs if _REF.fullmatch(r.strip()))
        out = f"- {_flat(x.text)} · **{evidence.grade_label(g, lang)}**" + (f" ({refs})" if refs else "")
        if isinstance(x, SuccessOut) and _flat(x.check):
            out += "\n  " + msg("plan.doc.check", lang, how=_flat(x.check))
        return out
    legend = msg("plan.doc.legend", lang, **{g.value: evidence.grade_label(g, lang) for g in Grade})
    parts = [f"# {key} {_flat(title)}", legend, *(
        f"## {msg(heading, lang)}\n" + ("\n".join(line(x, g) for x, g in lines) or msg("plan.doc.none", lang))
        for heading, lines in graded)]
    return "\n\n".join(parts) + "\n"


def _flat(text: str) -> str:
    return " ".join(text.replace("**", "").split())


def _raise_card(s, plan: Plan, graded: list[tuple[str, list[tuple[PlanLineOut, Grade]]]], lang: Lang,
                of: dict | None) -> AgentRequest:
    """The plan's approval card, on the version just written. It holds up the work, since what to build is the
    owner's call, and only server code files it. The card of a rewrite names the card the owner sent back, and that
    card says it was replaced."""
    labels = {k: msg(f"plan.option.{k}", lang) for k in OPTIONS}
    counts = {g: sum(1 for _, lines in graded for _, x in lines if x == g) for g in Grade}
    mix = ", ".join(msg("plan.card.part", lang, grade=evidence.grade_label(g, lang), count=counts[g]) for g in Grade)
    need = {k: v for k, v in plan.need.items() if k != "extra_needs"}
    card = DecisionCard(
        headline=msg("plan.card.headline", lang), owner_does=msg("plan.card.owner_does", lang),
        why=one_line(need.get("problem") or plan.title, 120), metrics=[],
        options=[CardOption(label=labels[k], verdict=_VERDICT[k], summary=msg(f"plan.option.{k}.says", lang),
                            evidence_ids=[], risks=[], recommended=False) for k in OPTIONS],
        recommendation=msg("plan.card.recommendation", lang), reversible=True)
    req = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title=msg("plan.card.title", lang, title=plan.title),
        summary=msg("plan.card.summary", lang, n=sum(counts.values()), mix=mix),
        rationale=need.get("why_software") or need.get("problem") or "", options=[], recommended_option=None,
        action_kind=requests.PLAN_CARD, estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM, card=card,
    ), from_agent="product", project_id=plan.project_id, origin=ServerOrigin(OriginKind.PLAN_APPROVAL, plan.id),
        system=True, dedupe=False, action_params={
        "plan_id": plan.id, "commit_sha": plan.draft_commit_sha, "changes_option": labels["changes"], "need": need,
        # The product's development matter: a later build ask lands on this card (matters.place).
        "matter": f"dev:{matters.product_scope(s, plan.project_id)[0]}",
        **({"extra_needs": plan.need["extra_needs"]} if plan.need.get("extra_needs") else {}),
        **({"redraft_of": of} if of else {})})
    plan.approval_request_id = req.id
    follow(s, req)  # a card the policy settled as it was filed is decided already
    if of and (old := s.get(AgentRequest, of["request"])) is not None:
        old.response = {**(old.response or {}), "superseded_by": requests.display_id(req.id)}
        # Asks that joined the card the owner sent back, or waited on it while the plan was rewritten, stay with it.
        kept = [n for key in ("extra_needs", requests.WAITING_NEEDS) for n in (old.action_params or {}).get(key, [])]
        if kept:
            have = req.action_params.get("extra_needs", [])
            req.action_params = {**req.action_params, "extra_needs": [*have, *(n for n in kept if n not in have)]}
    return req


def card_plan(s, req: AgentRequest) -> dict | None:
    """The plan a plan card asks the owner to approve, as the card's commit holds it, so the owner reads exactly what
    approving pins. None for any other card, or when the registry lacks that commit."""
    params = req.action_params or {}
    plan = s.get(Plan, params.get("plan_id") or 0) if req.action_kind == requests.PLAN_CARD else None
    if plan is None or not params.get("commit_sha"):
        return None
    try:
        text = registry.read_at(f"{plan.registry_path}/{FILE}", params["commit_sha"])
    except RuntimeError:
        return None
    from justshipit.engineering.specs import for_owner
    return {"text": for_owner(text, plan.plan_key)}


def spec_plan(s, req: AgentRequest) -> dict | None:
    """The plan behind a spec card's spec, as the card shows it: the approved plan card to open, the version the spec
    followed, whether the plan has a newer version since (`stale`), or that no plan is behind it (`legacy`: a spec of a
    build ask filed before plans). None for any other card."""
    params = req.action_params or {}
    spec = s.get(Spec, params.get("spec_id") or 0) if req.action_kind == "dev.spec_approve" else None
    if spec is None:
        return None
    plan = s.get(Plan, spec.plan_id) if spec.plan_id else None
    if plan is None:
        return {"request_id": None, "plan_key": None, "commit_sha": None, "title": None, "stale": False,
                "legacy": True}
    commit = params.get("plan_commit_sha") or spec.plan_commit_sha
    approved = s.scalar(select(AgentRequest.id).where(
        AgentRequest.action_kind == requests.PLAN_CARD, AgentRequest.action_params["plan_id"].as_integer() == plan.id,
        AgentRequest.action_params["commit_sha"].astext == commit,
        AgentRequest.status.in_([RequestStatus.APPROVED.value, RequestStatus.AUTO_APPROVED.value]))
        .order_by(AgentRequest.id.desc()).limit(1))
    return {"request_id": approved or plan.approval_request_id, "plan_key": plan.plan_key, "commit_sha": commit,
            "title": plan.title, "stale": plan.draft_commit_sha != commit, "legacy": False}


def redraft_plan(project_id: int, plan_id: int, request_id: int, commit: str, note: str) -> None:
    """Handle `redraft_plan`: the plan written again from the owner's note on the version at `commit`, under the same
    key as a new commit, and a new card that names the card `request_id`. A plan written again already is left as it
    is."""
    _write(project_id, plan_id, was=commit, note=note, of={"request": request_id, "commit": commit})


def planned(s, project_id: int, placed: matters.Placement, need: dict, by: str) -> Plan | None:
    """The plan a new development ask for the build `need` names goes to, in `requests.create`'s transaction under the
    matter lock: a plan already being written, or approved with its spec being written, takes it; where the product's
    matter would make a new card, a new plan starts instead, and a held card it replaces closes. None leaves the ask
    to the matter (`placed`): an open plan or spec card takes it, a held one is asked again, a build under way notes
    it."""
    if (plan := live(s, project_id)) is not None and plan.status != PlanStatus.IN_REVIEW.value:
        take(plan, {"title": need["title"], "problem": need.get("problem", ""), "by": by})
        _write_again(s, plan)
        return plan
    if placed.route not in (matters.Route.NEW, matters.Route.REPLACE):
        return None
    plan = begin(s, s.get(Project, project_id), need)
    if placed.route is matters.Route.REPLACE:
        requests.supersede(placed.head, plan.plan_key, by)
    return plan


_WRITES = ("write_plan", "redraft_plan")


def _write_again(s, plan: Plan) -> None:
    """A plan in DRAFT whose writing gave up is written again when the next ask joins it: with no job in flight it
    would never get a card. The copy has the key an owner's retry of that job would, so a retry and an ask queue one."""
    if plan.status != PlanStatus.DRAFT.value:
        return
    jobs = select(Job).where(Job.kind.in_(_WRITES), Job.payload["plan_id"].as_integer() == plan.id)
    if s.scalar(jobs.where(queue.in_flight()).limit(1)) is not None:
        return
    if (last := s.scalar(jobs.order_by(Job.id.desc()).limit(1))) is not None:
        queue.enqueue(s, last.kind, dict(last.payload), idempotency_key=f"job_retry:{last.id}",
                      max_attempts=last.max_attempts)


def live(s, project_id: int) -> Plan | None:
    """The product's newest plan while it is still on its way to a spec: being written, waiting for the owner, or
    approved while its spec is written. None once a spec is written from it, or after the owner declined it."""
    _, ids = matters.product_scope(s, project_id)
    plan = s.scalar(select(Plan).where(Plan.project_id.in_(ids)).order_by(Plan.id.desc()).limit(1))
    if plan is None or plan.status == PlanStatus.REJECTED.value:
        return None
    if plan.status != PlanStatus.APPROVED.value:
        return plan
    written = s.scalar(select(Spec.id).where(Spec.plan_id == plan.id).limit(1)) is not None
    writing = s.scalar(select(Job.id).where(Job.idempotency_key == f"write_spec:{plan.approval_request_id}",
                                            queue.in_flight()).limit(1)) is not None
    return plan if writing and not written else None


def take(plan: Plan, need: dict) -> None:
    """Another ask for the same product joins the plan: its writer and the spec's writer read it."""
    plan.need = {**plan.need, "extra_needs": [*plan.need.get("extra_needs", []), need]}


def extra_needs(s, req: AgentRequest) -> list[dict]:
    """The asks that joined a plan card's plan after the card went up, which the spec's writer reads beside the card's."""
    pin = pinned(s, req)
    have = (req.action_params or {}).get("extra_needs", [])
    return [n for n in pin[0].need.get("extra_needs", []) if n not in have] if pin else []


def took(s, req: AgentRequest, lang: Lang) -> Plan | None:
    """An ask that is not a plan card's, approved while a plan for its product is under way, joins that plan instead of
    a spec being written beside it."""
    if req.action_kind == requests.PLAN_CARD or req.project_id is None or (plan := live(s, req.project_id)) is None:
        return None
    take(plan, {"title": req.title, "problem": req.summary, "by": req.from_agent})
    activity.record(s, verb="request_folded", entity="request", entity_id=req.id, project_id=req.project_id,
                    actor="product", summary=msg("matter.waits_on_plan", lang),
                    data={"ref": requests.display_id(req.id), "plan": plan.plan_key})
    return plan


# ------------------------------------------------------------------ the owner's decision on the card

# The plan's status as its own card stands. A card the plan moved on from (sent back, asked again) changes nothing.
FOLLOWS: dict[str, PlanStatus] = {
    RequestStatus.PENDING.value: PlanStatus.IN_REVIEW, RequestStatus.DEFERRED.value: PlanStatus.IN_REVIEW,
    RequestStatus.EXPIRED.value: PlanStatus.IN_REVIEW, RequestStatus.APPROVED.value: PlanStatus.APPROVED,
    RequestStatus.AUTO_APPROVED.value: PlanStatus.APPROVED, RequestStatus.REJECTED.value: PlanStatus.REJECTED,
}


def follow(s, req: AgentRequest) -> None:
    """Set the plan as its card now stands, in the decision's transaction. Approving pins the card's commit. Declining
    it declines the spec a revision of the plan sent back, which would otherwise stay the product's live spec."""
    plan = s.get(Plan, (req.action_params or {}).get("plan_id") or 0)
    if plan is None or plan.approval_request_id != req.id or (to := FOLLOWS.get(req.status)) is None:
        return
    plan.status = to.value
    if to is PlanStatus.APPROVED:
        plan.approved_commit_sha = req.action_params["commit_sha"]
    spec = newest_spec(s, plan)
    if to is PlanStatus.REJECTED and spec is not None and spec.status == SpecStatus.DRAFT.value \
            and spec.approval_request_id is None:
        spec.status = SpecStatus.REJECTED.value


def send_back(s, req: AgentRequest, resp: HumanResponse) -> bool:
    """Whether the owner's no is a request for changes: the plan then goes back to DRAFT with no card, and the caller
    queues its rewrite from the note. One without a note is refused, since nothing would say what to change."""
    params = req.action_params or {}
    changes = params.get("changes_option") or ""
    if resp.decision != HumanDecision.REJECT or not changes or resp.chosen_option not in (changes, one_line(changes, 40)):
        return False
    if not (resp.note or "").strip():
        raise requests.ResponseRejected(said("plan.changes.no_note"))
    plan = s.get(Plan, params["plan_id"], with_for_update=True)
    if plan is None or plan.approval_request_id != req.id:
        return False
    plan.status, plan.approval_request_id = PlanStatus.DRAFT.value, None
    return True


def reask_approval(s, old: AgentRequest) -> AgentRequest:
    """Ask again about a plan whose card the owner put on hold or let expire: the same plan and commit on a new card,
    and the old card closed as replaced."""
    keep = ("plan_id", "commit_sha", "changes_option", "need", "matter", "extra_needs", "redraft_of", "card")
    again = requests.create(s, AgentRequestDraft(
        type=RequestType.APPROVAL, title=old.title, summary=old.summary, rationale=old.rationale,
        options=old.options or [], recommended_option=None, action_kind=requests.PLAN_CARD, estimated_cost_usd=None,
        risk_level=RiskLevel(old.risk_level)), from_agent="product", project_id=old.project_id,
        origin=ServerOrigin(OriginKind.PLAN_REASK, old.action_params["plan_id"]), system=True, dedupe=False,
        action_params={k: v for k, v in (old.action_params or {}).items() if k in keep})
    plan = s.get(Plan, old.action_params["plan_id"])
    if plan is not None and plan.approval_request_id == old.id:
        plan.approval_request_id, plan.status = again.id, PlanStatus.IN_REVIEW.value
        follow(s, again)
    old.response = {**(old.response or {}), "superseded_by": requests.display_id(again.id)}
    old.status = RequestStatus.CANCELLED.value
    return again


# ------------------------------------------------------------------ the spec written from a plan

def pinned(s, req: AgentRequest) -> tuple[Plan, str] | None:
    """The plan version a spec written for `req` follows: a plan card's plan at the card's commit. None for any other
    request (a build ask filed before plans): its spec has no plan."""
    params = req.action_params or {}
    plan = s.get(Plan, params.get("plan_id") or 0) if req.action_kind == requests.PLAN_CARD else None
    return (plan, params["commit_sha"]) if plan is not None else None


def moved_on(s, req: AgentRequest) -> bool:
    """Whether the plan a plan card approved has moved on since (the owner revised it), so its spec waits for the
    next approval."""
    pin = pinned(s, req)
    return pin is not None and (pin[0].status != PlanStatus.APPROVED.value or pin[0].approved_commit_sha != pin[1])


def prompt_section(s, req: AgentRequest) -> str:
    """The approved plan as the spec's writer reads it. Empty for a request without a plan."""
    if (pin := pinned(s, req)) is None:
        return ""
    plan, commit = pin
    text = registry.read_at(f"{plan.registry_path}/{FILE}", commit)
    return (f"## The product plan the owner approved ({plan.plan_key})\nWrite the spec inside this plan: its scope and "
            "what it will not do bind the spec, and every requirement serves its goal and success criteria. A line "
            "marked assumed is a guess to keep visible, not a fact.\n" + fenced(FILE, ctx.clip(text, 12000, FILE)))


class Revision(StrEnum):
    FREE = "free"            # nothing written from the plan yet: it is written again
    SEND_BACK = "send_back"  # its spec is not approved: the spec goes back to DRAFT and follows the next version
    FROZEN = "frozen"        # its spec is approved: a change of direction is a new build ask


# What revising an approved plan does, by the status of the newest spec written from it (None: none yet).
REVISION: dict[str | None, Revision] = {
    None: Revision.FREE,
    **{st.value: Revision.SEND_BACK for st in (SpecStatus.DRAFT, SpecStatus.CLARIFICATION_REQUIRED,
                                               SpecStatus.IN_REVIEW, SpecStatus.REJECTED)},
    **{st.value: Revision.FROZEN for st in (SpecStatus.SPEC_APPROVED, SpecStatus.READY_FOR_DEV,
                                            SpecStatus.IN_DEVELOPMENT, SpecStatus.DELIVERED, SpecStatus.ACCEPTED,
                                            SpecStatus.MERGED)},
}


def revision_of(s, spec: Spec | None) -> Revision:
    """What revising the plan does to its newest spec. A spec the owner approved on its card is frozen though its
    status says IN_REVIEW until the `spec_approved` job runs."""
    card = s.get(AgentRequest, spec.approval_request_id) if spec is not None and spec.approval_request_id else None
    if card is not None and card.status in (RequestStatus.APPROVED.value, RequestStatus.AUTO_APPROVED.value):
        return Revision.FROZEN
    return REVISION[spec.status if spec else None]


def newest_spec(s, plan: Plan) -> Spec | None:
    return s.scalar(select(Spec).where(Spec.plan_id == plan.id).order_by(Spec.id.desc()).limit(1))


def stale_spec(s, req: AgentRequest) -> Spec | None:
    """The spec of a plan card's plan, written from another version and not approved: the spec a revision of the plan
    sent back, which this approval writes again under its key."""
    if (pin := pinned(s, req)) is None or (spec := newest_spec(s, pin[0])) is None:
        return None
    return spec if spec.plan_commit_sha != pin[1] and revision_of(s, spec) is not Revision.FROZEN else None


def withdraw_spec_cards(s, spec: Spec, lang: Lang) -> None:
    """Close the cards still asking about a spec its plan's revision replaces: its approval and its questions, open,
    held or expired, so none can be reopened and approved for a version the plan moved on from."""
    from justshipit.engineering import questions
    note = msg("plan.revise.withdrawn", lang)
    for card_id in (spec.approval_request_id, *(c.id for c in questions.cards(s, spec).values())):
        if not card_id or requests.withdraw_pending(s, card_id, by="system", note=note):
            continue
        card = s.get(AgentRequest, card_id, with_for_update=True)
        if card is not None and card.status in (RequestStatus.DEFERRED.value, RequestStatus.EXPIRED.value):
            card.response = {**(card.response or {}), "closed_by": "system", "closed_note": note}
            card.status = RequestStatus.CANCELLED.value
    spec.approval_request_id = None


def revise(s, plan_id: int, note: str, lang: Lang) -> Plan:
    """The owner revises an approved plan with a note, in the caller's transaction. Before its spec is approved the
    plan is written again from the note, and a spec already written from it goes back to DRAFT with its cards closed,
    to be written again from the version the owner approves next. Once its spec is approved the plan is frozen."""
    plan = s.get(Plan, plan_id)
    if plan is None:
        raise requests.ResponseRejected(said("plan.not_found"))
    matters.lock(s, plan.project_id)
    s.refresh(plan, with_for_update=True)
    if not note.strip():
        raise requests.ResponseRejected(said("plan.changes.no_note"))
    p = s.get(Project, plan.project_id)
    if p.status == "ARCHIVED":
        raise requests.ResponseRejected(said("work.refuse.archived_project", ref=f"P-{p.id}"))
    if p.business_id is not None and s.scalar(select(Business.status).where(Business.id == p.business_id)) == "ARCHIVED":
        raise requests.ResponseRejected(said("work.refuse.archived_business", ref=f"B-{p.business_id}"))
    if plan.status != PlanStatus.APPROVED.value:
        raise requests.ResponseRejected(said("plan.revise.not_approved"))
    spec = newest_spec(s, plan)
    how = revision_of(s, spec)
    if how is Revision.FROZEN:
        raise requests.ResponseRejected(said("plan.revise.frozen"))
    if how is Revision.SEND_BACK:
        withdraw_spec_cards(s, spec, lang)
        spec.status = SpecStatus.DRAFT.value
    plan.status = PlanStatus.DRAFT.value
    queue.enqueue(s, "redraft_plan", {"project_id": plan.project_id, "plan_id": plan.id,
                                      "request_id": plan.approval_request_id, "commit": plan.draft_commit_sha,
                                      "note": note.strip()},
                  idempotency_key=f"redraft_plan:{plan.approval_request_id}", by_owner=True)
    return plan
