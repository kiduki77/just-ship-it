"""One matter, one card: what makes two owner requests the same decision, and where a new ask for a known matter goes.

A matter is named by a key stored on each of its requests (`action_params["matter"]`):

- `dev:b<business>` (or `dev:p<project>` outside a business): the business's product. Every development ask, from a
  task, the CEO or the final report, is one matter, so the owner decides "build this" once, on the spec card.
- `post:p<project>:<channel>:<post key>`: one post, whatever iteration or task drafted it.
- the key of the request an agent names in `replaces`, or `req:<id>` when that request had none.

The matter's head is its newest request that was not closed as merged or superseded, except that a declined revision
leaves the approval it revised as the head. A new ask never makes a second card while the head is open or decided: it
revises the open card, adds to it, updates the owner task the approval made, or becomes an activity note. A deferred or
expired head, or a product whose last version was merged, takes a new card. So does an ask that changes what the owner
approved (other words, a higher cost): that card names the approval it revises (`action_params["revises"]`), and
approving it moves the approval's open owner task onto it, so the matter keeps one owner task.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.contracts.handoff import DEV_JOB_DELIVERED, SpecStatus
from justshipit.contracts.plan import PlanStatus
from justshipit.contracts.requests import AgentRequestDraft, RequestStatus, RequestType
from justshipit.db.models import AgentRequest, DevJob, Job, OwnerTask, Plan, Project, Spec
from justshipit.i18n import Lang, msg
from justshipit.i18n.said import Said, said
from justshipit.services import existing_product, owner_tasks
from justshipit.services.queue import DEV_ACTIVE, in_flight

_CLOSED = (RequestStatus.CANCELLED.value, RequestStatus.DENIED_BY_POLICY.value)
_DECIDED_YES = (RequestStatus.APPROVED.value, RequestStatus.AUTO_APPROVED.value, RequestStatus.ANSWERED.value)
_ASK_AGAIN = (RequestStatus.DEFERRED.value, RequestStatus.EXPIRED.value)


class Route(StrEnum):
    NEW = "new"            # no card for the matter: make one
    REVISE = "revise"      # the card is open: the new draft replaces its content, same REQ id
    APPEND = "append"      # the card is open: the new ask adds to it (development needs add up)
    REPLACE = "replace"    # the head was deferred or expired, or changed its action: a new card, the old one closed
    SUCCEEDS = "succeeds"  # the ask changes what the owner approved: a new card that revises the head, which stands
    TASK = "task"          # approved and its owner task is open: the revision goes onto the owner task
    NOTE = "note"          # decided, or a product being drafted or built: an activity note, no card
    REASK_SPEC = "reask"   # the product's spec card was deferred or expired: the same spec is asked again
    REASK_PLAN = "reask_plan"  # the product's plan card was deferred or expired: the same plan is asked again


@dataclass(frozen=True)
class Matter:
    key: str
    adds_up: bool = False     # a development need adds to the card; any other new draft replaces the card's content
    alias: str | None = None  # the draft's own post key when `replaces` tied it to another matter: a later draft of
                              # the same post that does not name the request still finds this matter by it
    loose: bool = False       # the key may be shared by a different post: it joins an open card, never a decided one


@dataclass(frozen=True)
class Placement:
    route: Route
    head: AgentRequest | None = None
    target: AgentRequest | None = None   # the open card that an APPEND adds to, the spec card to ask again, or the
                                         # request a development need waits on
    task: OwnerTask | None = None
    why: Said | None = None              # the reason the activity note gives
    waits: bool = False                  # the need waits on `target`, a spec's own request, until its card goes up


def product_scope(s: Session, project_id: int) -> tuple[str, list[int]]:
    """The product a project builds toward, as a key suffix and the projects that share it: a business has one."""
    p = s.get(Project, project_id)
    if p is not None and p.business_id is not None:
        ids = list(s.scalars(select(Project.id).where(Project.business_id == p.business_id)))
        return f"b{p.business_id}", ids
    return f"p{project_id}", [project_id]


def product_specs(s: Session, project_id: int) -> list[Spec]:
    """Every spec written for the project's product, oldest first."""
    _, ids = product_scope(s, project_id)
    return list(s.scalars(select(Spec).where(Spec.project_id.in_(ids)).order_by(Spec.id)))


def live_spec(s: Session, project_id: int) -> Spec | None:
    """The product's current spec (current_spec of its specs)."""
    return current_spec(s, product_specs(s, project_id))


def _finished(s: Session, spec: Spec) -> bool:
    """Whether this version is over: merged, or its build dropped by the owner (worker.drop), so nothing more is built
    on it."""
    if spec.status == SpecStatus.MERGED.value:
        return True
    from justshipit.engineering import worker
    job = s.scalar(select(DevJob).where(DevJob.spec_id == spec.id)
                   .order_by(DevJob.attempt.desc(), DevJob.id.desc()).limit(1))
    return job is not None and worker.dropped(job)


def current_spec(s: Session, specs: list[Spec]) -> Spec | None:
    """Of these specs, oldest first, the one development goes on: the newest, unless that version was merged, its
    build dropped or its approval rejected. A rejection is the owner's no to that spec only: an approved version still
    being built (its job running, stopped or waiting on the merge) stays current, and only with none does the next ask
    draft a new spec. A spec declined with no card of its own (its plan, declined after a revision sent it back)
    counts as rejected too."""
    if not specs:
        return None
    last = specs[-1]
    approval = s.get(AgentRequest, last.approval_request_id) if last.approval_request_id else None
    if _finished(s, last):
        return None
    if last.status == SpecStatus.REJECTED.value or (approval is not None
                                                    and approval.status == RequestStatus.REJECTED.value):
        return next((sp for sp in reversed(specs) if sp.approved_commit_sha and not _finished(s, sp)), None)
    return last


def product_repo(s: Session, project_id: int) -> str | None:
    """The repository the business brought, or else the one the product's first spec chose. Later versions keep it,
    so one product has one repository."""
    if (product := existing_product.of_project(s, project_id)) is not None:
        return product.repo
    return next((sp.target_repository for sp in product_specs(s, project_id) if sp.target_repository), None)


def request_ref(ref: str | None) -> int | None:
    text = (ref or "").strip().upper().removeprefix("REQ-")
    return int(text) if text.isdigit() else None


def _same_scope(s: Session, a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return a == b
    return a == b or product_scope(s, a)[0] == product_scope(s, b)[0]


# A request tied to one of these is the server's (a spec, a merge, a bet, a meeting decision, a skill, an idea): its
# card closes only through its own flow, so no agent's `replaces` can rewrite or cancel it.
_BOUND = ("spec_id", "plan_id", "dev_job_id", "use_of_job", "bet_id", "decision_id", "skill_id", "idea_id")


def server_bound(req: AgentRequest) -> bool:
    """Whether the card belongs to a server flow, which alone may close it: no agent, CEO cleanup or directive does."""
    return any(k in (req.action_params or {}) for k in _BOUND)


def _replaceable(old: AgentRequest, from_agent: str, task_id: int | None) -> bool:
    """Whether a draft may name `old` in `replaces`: the same author's or task's own card, and not a server one."""
    return (old.from_agent == from_agent or (task_id is not None and old.task_id == task_id)) and not server_bound(old)


def matter_for(s: Session, draft: AgentRequestDraft, project_id: int | None, post_card: dict | None, *,
               from_agent: str, task_id: int | None) -> Matter | None:
    """The matter a draft belongs to, or None when nothing structural ties it to an earlier request."""
    if project_id is None:
        return None
    if draft.type == RequestType.DEVELOPMENT:
        return Matter(f"dev:{product_scope(s, project_id)[0]}", adds_up=True)
    post = _post_matter(project_id, post_card)
    if (rid := request_ref(draft.replaces)) is not None:
        old = s.get(AgentRequest, rid)
        if old is not None and _same_scope(s, old.project_id, project_id) and _replaceable(old, from_agent, task_id):
            key = (old.action_params or {}).get("matter")
            # The draft lands on the matter's head, which can be a later card than the one it named: it must be one
            # the draft may rewrite too.
            lands = _head(s, key) if key else None
            if lands is None or _replaceable(lands, from_agent, task_id):
                if not key:
                    key = f"req:{old.id}"
                    old.action_params = {**(old.action_params or {}), "matter": key}
                return Matter(key, alias=post[0] if post and post[0] != key else None)
    return Matter(post[0], loose=post[1]) if post else None


def _post_matter(project_id: int, post_card: dict | None) -> tuple[str, bool] | None:
    """The post's key, and whether it is loose: an outline card is keyed by its first fact, which another post can
    share, so only a title names a post firmly."""
    if post_card and (post_key := owner_tasks.post_key(post_card)):
        return f"post:p{project_id}:{post_card['channel']}:{post_key}", not (post_card.get("what") or {}).get("title")
    return None


def keys(matter: Matter, req: AgentRequest | None = None) -> dict:
    """The action params that file a request under the matter: its key, and the aliases it is also found by."""
    params = (req.action_params or {}) if req is not None else {}
    aliases = list(params.get("matter_aliases", []))
    if matter.alias and matter.alias not in aliases:
        aliases.append(matter.alias)
    revises = {"revises": params["revises"]} if "revises" in params else {}
    return {"matter": params.get("matter", matter.key), **({"matter_aliases": aliases} if aliases else {}), **revises}


def lock(s: Session, project_id: int) -> None:
    """Serialize filing per business, so two parallel tasks asking the same thing still make one card. One lock per
    transaction whatever the matters, so drafts filed in any order cannot deadlock."""
    key = f"matters:{product_scope(s, project_id)[0]}"
    s.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


def _head_query(key: str):
    named = ((AgentRequest.action_params["matter"].astext == key)
             | AgentRequest.action_params["matter_aliases"].contains([key]))
    return select(AgentRequest).where(named, AgentRequest.status.notin_(_CLOSED)).order_by(AgentRequest.id.desc()).limit(1)


def _head(s: Session, key: str, *, lock: bool = False) -> AgentRequest | None:
    def one(q):
        return s.scalar(q.with_for_update() if lock else q)
    h = one(_head_query(key))
    # The owner's no to a revision is a no to the change only: the approval it revised still stands.
    if h is not None and h.status == RequestStatus.REJECTED.value and (rid := (h.action_params or {}).get("revises")):
        h = one(select(AgentRequest).where(AgentRequest.id == rid, AgentRequest.status.in_(_DECIDED_YES))) or h
    return h


def _declined(s: Session, key: str, h: AgentRequest) -> AgentRequest | None:
    """The newest revision of the approved `h` the owner turned down, if the matter's newest request is one."""
    last = s.scalar(_head_query(key))
    declined = last is not None and last.status == RequestStatus.REJECTED.value
    return last if declined and (last.action_params or {}).get("revises") == h.id else None


def head(s: Session, matter: Matter) -> AgentRequest | None:
    """The matter's newest open or decided request, row-locked, so an owner's answer that lands meanwhile is read
    before the new draft is placed against it."""
    return _head(s, matter.key, lock=True)


def _product_placement(s: Session, h: AgentRequest, project_id: int) -> Placement:
    """A development ask once the product's development was decided: the spec card takes it while open, and a new
    version starts only after the last one was merged."""
    # A merged version is done, and a rejected one is the owner's no to that spec, not to every later version: with no
    # approved version still being built, the next ask starts a new spec the owner can approve.
    if (live := live_spec(s, project_id)) is None:
        if (s.scalar(select(Job.id).where(Job.idempotency_key == f"write_spec:{h.id}", in_flight())) is None
                or s.scalar(select(Spec.id).where(Spec.request_id == h.id)) is not None):
            return Placement(Route.NEW, h)
        # The next spec is being written from this request, so its card goes up on it: the ask waits there for it.
        return Placement(Route.NOTE, h, target=h, waits=True, why=said("matter.waits_on_draft"))
    card = s.get(AgentRequest, live.approval_request_id) if live.approval_request_id else None
    if card is None:
        # No spec card is up (the spec waits on the owner's answers to its questions, or on its redraft from the
        # owner's notes): the ask waits on the spec's request, and the card that goes up carries it, as an open spec
        # card would.
        why = {SpecStatus.CLARIFICATION_REQUIRED.value: "matter.waits_on_questions",
               SpecStatus.DRAFT.value: "matter.waits_on_redraft"}.get(live.status)
        if why and live.request_id:
            return Placement(Route.NOTE, h, target=s.get(AgentRequest, live.request_id), waits=True, why=said(why))
        return Placement(Route.NOTE, h, why=said("matter.spec_in_progress"))
    if card.status == RequestStatus.PENDING.value:
        return Placement(Route.APPEND, h, target=card, why=said("matter.next_version"))
    if card.status in _ASK_AGAIN:
        return Placement(Route.REASK_SPEC, h, target=card, why=said("matter.reask_deferred"))
    if card.status == RequestStatus.CANCELLED.value:
        # Withdrawn before the owner decided (a cleanup or a directive, from before those spared spec cards): the
        # spec is still undecided, so it is asked again rather than left live with no card.
        return Placement(Route.REASK_SPEC, h, target=card, why=said("matter.reask_withdrawn"))
    return Placement(Route.NOTE, h, target=card, why=_building(s, live))


def _building(s: Session, spec: Spec) -> Said:
    """What the product's approved spec is doing now, as the note says it: development is underway only while a dev
    job is."""
    jobs = list(s.scalars(select(DevJob.status).where(DevJob.spec_id == spec.id)))
    if any(st in DEV_ACTIVE for st in jobs):
        return said("matter.building")
    if any(st in {d.value for d in DEV_JOB_DELIVERED} for st in jobs):
        return said("matter.built")
    return said("matter.approved_idle")


def place(s: Session, matter: Matter, draft: AgentRequestDraft, project_id: int, *, has_connector: bool,
          post_card: dict | None = None) -> Placement:
    """Where a new draft for this matter goes, from the head's state. Call under `lock`. `post_card` is the draft's
    post card as the server stored it."""
    h = head(s, matter)
    if h is None:
        return Placement(Route.NEW)
    if h.status == RequestStatus.PENDING.value:
        if matter.adds_up:
            return Placement(Route.APPEND, h, target=h, why=said("matter.added_to", title=h.title[:80]))
        # A card that sends something on approval is never rewritten under the owner: the owner could approve a
        # payload they never read. It is replaced, so an approval of the old card is refused.
        if h.type != draft.type.value or h.action_kind != draft.action_kind or has_connector:
            return Placement(Route.REPLACE, h)
        return Placement(Route.REVISE, h)
    plan = s.get(Plan, pid) if (pid := (h.action_params or {}).get("plan_id")) else None
    if h.status in _ASK_AGAIN:
        if plan is not None:
            return Placement(Route.REASK_PLAN, h, target=h, why=said("matter.reask_plan"))
        return Placement(Route.REPLACE, h)
    if matter.loose:
        return Placement(Route.NEW, h)
    if h.status == RequestStatus.REJECTED.value:
        if plan is not None and plan.status == PlanStatus.DRAFT.value and plan.approval_request_id is None:
            # The owner asked for changes: the ask waits on this card, and the plan's next card carries it.
            return Placement(Route.NOTE, h, target=h, waits=True, why=said("matter.waits_on_plan"))
        return Placement(Route.NOTE, h, why=said("matter.rejected"))
    if matter.key.startswith("dev:"):
        return _product_placement(s, h, project_id)
    if h.status in _DECIDED_YES:
        if (_asks_more(h, draft) or (has_connector and draft.replaces) or _other_post(h, post_card)
                or _other_handout(h, draft)):
            # The owner already said no to this very change: asking it again is a note, not a card.
            no = _declined(s, matter.key, h)
            if no is not None and not (_asks_more(no, draft) or _other_post(no, post_card)
                                       or _other_handout(no, draft)):
                return Placement(Route.NOTE, h, why=said("matter.rejected"))
            return Placement(Route.SUCCEEDS, h)   # what the owner approved does not cover this: a decision of its own
        task = s.scalar(select(OwnerTask).where(OwnerTask.request_id == h.id))
        if task is not None and task.status == "OPEN":
            return Placement(Route.TASK, h, task=task, why=said("matter.to_task", id=task.id))
        return Placement(Route.NOTE, h, why=said("matter.approved"))
    return Placement(Route.NEW, h)


def _other_post(h: AgentRequest, post_card: dict | None) -> bool:
    """Whether the draft's post is not the one the owner approved: another post, the same one with other words, or the
    same words for another place. The owner approved what they read and where it goes, so a change to either is a new
    decision, not an update on the approved task."""
    if post_card is None:
        return False
    approved = (h.action_params or {}).get("post_card")
    return not approved or _posted(post_card) != _posted(approved)


def _other_handout(h: AgentRequest, draft: AgentRequestDraft) -> bool:
    """Whether the draft's message or steps are not the ones the owner approved: the owner copies them from the to-do
    as approved, so other words, or another person to send them to, are a new decision, as for a post."""
    if draft.handout is None:
        return False
    return (h.action_params or {}).get("handout") != owner_tasks.stored_handout(draft.handout)


def _posted(post_card: dict) -> tuple:
    """What the owner publishes and where: the channel and address, and the title, body and facts of the text."""
    what = post_card.get("what") or {}
    return (post_card.get("channel"), (post_card.get("where_url") or "").strip(), (what.get("title") or "").strip(),
            (what.get("body") or "").strip(), [f.strip() for f in what.get("facts") or []])


@dataclass(frozen=True)
class Duplicate:
    """An open card another card already decides: it closes, pointing at `into`, with `why` in the owner's language."""
    card: AgentRequest
    into: AgentRequest
    why: str


_CLOSABLE = (RequestStatus.PENDING.value, *_ASK_AGAIN)


def duplicates(s: Session, business_id: int, *, lang: Lang) -> list[Duplicate]:
    """The business's open product cards that `place` would never have filed: one spec card decides the product, the
    newest. A development ask goes to that card, or to the approved spec still being built, or else to the newest ask.
    Cards from before the matter keys, so the matter is the product, not a stored key."""
    pids = list(s.scalars(select(Project.id).where(Project.business_id == business_id)))
    open_ = list(s.scalars(select(AgentRequest).where(AgentRequest.project_id.in_(pids),
                                                      AgentRequest.status == RequestStatus.PENDING.value)
                           .order_by(AgentRequest.id)))
    spec_cards = [r for r in open_ if r.action_kind == "dev.spec_approve" and "spec_id" in (r.action_params or {})]
    asks = [r for r in open_ if r.type == RequestType.DEVELOPMENT.value and not server_bound(r)]
    if spec_cards:
        keep = spec_cards[-1]
        key = s.get(Spec, keep.action_params["spec_id"]).spec_key
        return ([Duplicate(r, keep, msg("cleanup.why.newest_spec", lang, key=key)) for r in spec_cards[:-1]]
                + [Duplicate(r, keep, msg("cleanup.why.on_spec", lang, key=key)) for r in asks])
    built = s.scalar(select(Spec).where(Spec.project_id.in_(pids), Spec.approved_commit_sha.is_not(None),
                                        Spec.status != SpecStatus.MERGED.value, Spec.approval_request_id.is_not(None))
                     .order_by(Spec.id.desc()).limit(1))
    if built is not None:
        card = s.get(AgentRequest, built.approval_request_id)
        return [Duplicate(r, card, msg("cleanup.why.built", lang, key=built.spec_key)) for r in asks]
    return [Duplicate(r, asks[-1], msg("cleanup.why.newest_ask", lang)) for r in asks[:-1]]


def replaced(s: Session, business_id: int, pairs: list[tuple[int, int]], *, lang: Lang) -> list[Duplicate]:
    """Cards a later card replaced with nothing structural to say so (an agent wrote "REQ-26 대체" in the title):
    the operator names each pair. Refused unless both are the business's, the newer card is still live and the older
    one is open or asked again (deferred, expired). A server flow's card closes only through its own flow."""
    pids = set(s.scalars(select(Project.id).where(Project.business_id == business_id)))
    out = []
    for old_id, new_id in pairs:
        old, new = s.get(AgentRequest, old_id), s.get(AgentRequest, new_id)
        if old is None or new is None or old.project_id not in pids or new.project_id not in pids:
            raise ValueError(msg("cleanup.refuse.other_business", lang, old=f"REQ-{old_id}", new=f"REQ-{new_id}",
                                 business=f"B-{business_id}"))
        resp = old.response or {}
        if old.status in _CLOSED and f"REQ-{new.id}" in (resp.get("into"), resp.get("superseded_by")):
            continue   # closed into it already: a second run changes nothing
        if new.id <= old.id or new.status in _CLOSED:
            raise ValueError(msg("cleanup.refuse.not_later", lang, new=f"REQ-{new_id}", old=f"REQ-{old_id}"))
        if old.status not in _CLOSABLE or server_bound(old):
            raise ValueError(msg("cleanup.refuse.not_closable", lang, old=f"REQ-{old_id}"))
        out.append(Duplicate(old, new, msg("cleanup.why.replaced", lang, new=f"REQ-{new.id}")))
    return out


_RISK = ["low", "medium", "high", "critical"]


def _asks_more(h: AgentRequest, draft: AgentRequestDraft) -> bool:
    """A revision that costs more, or is riskier, than what the owner approved."""
    cost = (draft.estimated_cost_usd or 0) > (h.estimated_cost_usd or 0)
    return cost or _RISK.index(draft.risk_level.value) > _RISK.index(h.risk_level or "low")
