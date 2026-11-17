"""What `requests.create` refuses and what it records (docs/design/work-path.md 2.2-2.5): a case per row of the rules per
request type, the owner-work rule, holds on a task the filer waits on or one that ended, keys that resolve only inside
the card's own project, a refused draft that writes nothing, a joined card that keeps both askers, a revised card that
ends its old agent rows, and a REQ-6-shaped CEO question stored with every column and link."""
import pytest
from ask_kit import by_project_manager, by_task, contract
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import AgentAskDraft, AgentRequestDraft, Handout, RequestType, RiskLevel
from justshipit.contracts.work_path import AnswerEffect, AnswerUse, AskReason, ExpiryPolicy, HoldKind
from justshipit.db.models import ActivityEvent, AgentRequest, Evidence, RequestLink, ReviewRound, Task
from justshipit.i18n import msg
from justshipit.orchestration import company_graph
from justshipit.orchestration import context as ctx
from justshipit.services import projects, requests
from justshipit.services.request_contract import RequestRefused
from sqlalchemy import func, select

PICK, OTHER = "Checklist chat first", "Plain chat first"


def ask(type_=RequestType.QUESTION, *, context=None, **kw) -> AgentAskDraft:
    base = dict(type=type_, title=f"{type_.value.title()} for the owner", summary="s", rationale="r",
                options=[PICK, OTHER] if type_ == RequestType.QUESTION else [], recommended_option=None,
                action_kind="internal.other" if type_ in (RequestType.APPROVAL, RequestType.RESOURCE) else None,
                estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=context or contract(owner_work=False))
    return AgentAskDraft(**{**base, **kw})


def task(s, project, key: str, status: str = "READY", depends_on: tuple[int, ...] = ()) -> Task:
    t = Task(project_id=project.id, key=key, title=key, owner="research", objective="o", acceptance_criteria=["c"],
             status=status, depends_on=list(depends_on))
    s.add(t)
    s.flush()
    return t


def evidence(s, project, t: Task | None = None) -> Evidence:
    e = Evidence(project_id=project.id, task_id=t.id if t else None, agent="research", claim="c", kind="hypothesis")
    s.add(e)
    s.flush()
    return e


def refused(s, draft, origin, project) -> list[str]:
    with pytest.raises(RequestRefused) as exc:
        requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=origin)
    return exc.value.keys


def links(s, req: AgentRequest, rel: str | None = None) -> list[tuple]:
    q = select(RequestLink).where(RequestLink.request_id == req.id).order_by(RequestLink.id)
    return [(x.rel, x.target_kind, x.target_id, x.phase, x.option_key, x.effect, x.revision, x.source,
             x.ended_at is not None) for x in s.scalars(q) if rel is None or x.rel == rel]


# ---------------------------------------------------------------- QUESTION, CLARIFICATION, ESCALATION

@pytest.mark.parametrize("type_", [RequestType.QUESTION, RequestType.CLARIFICATION, RequestType.ESCALATION])
def test_a_question_holds_the_run_only_when_the_goal_is_unclear(s, project, type_):
    assert refused(s, ask(type_, context=contract(HoldKind.RUN, reason=AskReason.INPUT_MISSING, owner_work=False)),
                   by_project_manager(s, project.id), project) == ["refs_empty", "run_not_goal_unclear"]

    held = requests.create(s, ask(type_, context=contract(HoldKind.RUN, owner_work=False)), from_agent="project_manager",
                           project_id=project.id, origin=by_project_manager(s, project.id))
    assert (held.hold_scope, held.ask_reason) == ("run", "GOAL_UNCLEAR")


def test_a_question_shows_why_unless_the_goal_itself_is_unclear(s, project):
    assert refused(s, ask(context=contract(reason=AskReason.STEP_NOT_RUN, owner_work=False)), by_project_manager(s, project.id),
                   project) == ["refs_empty"]


def test_a_question_waits_with_no_end_only_when_it_holds_nothing(s, project):
    origin = by_project_manager(s, project.id)
    assert refused(s, ask(context=contract(HoldKind.PLANNING, expiry=ExpiryPolicy.KEEP_WAITING, owner_work=False)),
                   origin, project) == ["expiry_not_allowed"]

    waits = requests.create(s, ask(context=contract(expiry=ExpiryPolicy.KEEP_WAITING, owner_work=False)),
                            from_agent="project_manager", project_id=project.id, origin=origin)
    assert (waits.expiry_policy, waits.expires_at) == ("keep_waiting", None)


def test_a_question_defaults_only_to_its_recommended_option(s, project):
    origin = by_project_manager(s, project.id)
    draft = ask(recommended_option=PICK, context=contract(expiry=ExpiryPolicy.DEFAULT, default=OTHER, owner_work=False))
    assert refused(s, draft, origin, project) == ["default_not_recommended"]
    nothing = ask(recommended_option=None, context=contract(expiry=ExpiryPolicy.DEFAULT, default=PICK,
                                                            owner_work=False))
    assert refused(s, nothing, origin, project) == ["default_not_recommended"]


@pytest.mark.parametrize("over", [{"estimated_cost_usd": 12.0}, {"action_kind": "external.email_send"}])
def test_a_question_that_spends_or_sends_outside_only_closes(s, project, over):
    draft = ask(recommended_option=PICK, **over,
                context=contract(expiry=ExpiryPolicy.DEFAULT, default=PICK, owner_work=False))
    assert refused(s, draft, by_project_manager(s, project.id), project) == ["expiry_cannot_approve"]


# ---------------------------------------------------------------- APPROVAL, RESOURCE

@pytest.mark.parametrize("type_", [RequestType.APPROVAL, RequestType.RESOURCE])
def test_an_approval_holds_only_its_own_task_or_those_after_it_and_never_goes_ahead_unanswered(s, project, type_):
    project_manager = by_project_manager(s, project.id)
    assert refused(s, ask(type_, context=contract(HoldKind.PLANNING, owner_work=False)), project_manager,
                   project) == ["hold_not_allowed"]
    assert refused(s, ask(type_, context=contract(expiry=ExpiryPolicy.KEEP_WAITING, owner_work=False)), project_manager,
                   project) == ["expiry_cannot_approve"]

    author = by_task(s, project.id)
    later = task(s, project, f"after_{type_.value.lower()}", depends_on=(author.task_id,))
    mine = requests.create(s, ask(type_, title="Spend on the sample data", context=contract(
        HoldKind.THIS_TASK, owner_work=False)), from_agent="research", project_id=project.id, origin=author)
    after = requests.create(s, ask(type_, title="Pay for the panel", context=contract(
        HoldKind.MY_DEPENDENTS, owner_work=False)), from_agent="research", project_id=project.id, origin=author)

    assert (mine.task_id, mine.hold_scope, mine.blocking) == (author.task_id, "tasks", True)
    assert links(s, mine, "holds") == [("holds", "task", author.task_id, "finish", None, None, 1, "agent", False)]
    assert links(s, after, "holds") == [("holds", "task", later.id, "start", None, None, 1, "agent", False)]


# ---------------------------------------------------------------- PROPOSAL

def test_a_proposal_holds_at_most_the_next_plan_and_never_defaults(s, project):
    origin = by_project_manager(s, project.id)
    assert refused(s, ask(RequestType.PROPOSAL, context=contract(HoldKind.RUN, owner_work=False)), origin,
                   project) == ["hold_not_allowed"]
    assert refused(s, ask(RequestType.PROPOSAL, recommended_option=None, context=contract(
        expiry=ExpiryPolicy.DEFAULT, default="Adopt", owner_work=False)), origin, project) == ["default_not_allowed"]

    kept = requests.create(s, ask(RequestType.PROPOSAL, context=contract(
        HoldKind.PLANNING, expiry=ExpiryPolicy.CLOSE, owner_work=False)), from_agent="project_manager", project_id=project.id,
        origin=origin)
    assert (kept.hold_scope, kept.expiry_policy) == ("planning", "close")


# ---------------------------------------------------------------- DEVELOPMENT, agent ask

def test_an_agents_development_ask_holds_nothing_itself(s, project):
    assert refused(s, ask(RequestType.DEVELOPMENT, context=contract(HoldKind.PLANNING, owner_work=False)),
                   by_project_manager(s, project.id), project) == ["development_holds_server_owned"]


# ---------------------------------------------------------------- the server's own cards

def test_a_server_card_takes_its_row_the_flow_that_waits_on_it_and_its_expiry_rule(s, project):
    merge = requests.create(s, ask(RequestType.APPROVAL, title="Merge the build", action_kind="dev.merge",
                                   context=None), from_agent="product", project_id=project.id,
                            origin=ServerOrigin(OriginKind.MERGE, 77), action_params={"dev_job_id": 77}, system=True)
    question = requests.create(s, ask(title="Which screen first", context=None), from_agent="product",
                               project_id=project.id, origin=ServerOrigin(OriginKind.SPEC_QUESTION, 5),
                               action_params={"spec_id": 5}, system=True, dedupe=False)

    assert (merge.context_version, merge.ask_reason, merge.expiry_policy, merge.hold_scope) == (
        1, "NEEDS_PERMISSION", "close", "none")
    assert links(s, merge) == [("raised_by", "dev_job", 77, None, None, None, 1, "server", False),
                               ("holds", "dev_job", 77, None, None, None, 1, "server", False)]
    assert (question.expiry_policy, question.expires_at) == ("keep_waiting", None)
    assert ("holds", "spec", 5, None, None, None, 1, "server", False) in links(s, question)


def test_a_card_from_team_chat_holds_nothing_and_names_its_message(s, project):
    chat = ServerOrigin(OriginKind.CHAT, 4321)
    assert refused(s, ask(context=contract(HoldKind.PLANNING, owner_work=False)), chat, project) == ["hold_not_allowed"]

    filed = requests.create(s, ask(), from_agent="marketing", project_id=project.id, origin=chat, owner_decides=True)
    assert links(s, filed) == [("raised_by", "chat_message", 4321, None, None, None, 1, "server", False)]


# ---------------------------------------------------------------- the owner's own work

def _by_hand(**kw) -> AgentAskDraft:
    return ask(RequestType.APPROVAL, title="Send Tom the support-email ask", action_kind="external.other",
               handout=Handout(kind="message", to="Tom", body="Hi Tom, could you forward one week of emails?"), **kw)


def test_a_card_asking_the_owners_hands_says_why_only_the_owner_can_or_it_is_not_filed(s, project):
    with pytest.raises(RequestRefused) as exc:
        requests.create(s, _by_hand(context=contract(owner_work=False)), from_agent="sales", project_id=project.id,
                        origin=by_project_manager(s, project.id))

    assert exc.value.keys == ["owner_work_missing"]
    assert "a program could do" in exc.value.words("en")
    assert "프로그램이 할 수 있는 일" in exc.value.words("ko")

    filed = requests.create(s, _by_hand(context=contract()), from_agent="sales", project_id=project.id,
                            origin=by_project_manager(s, project.id))
    assert filed.owner_work == {"minutes": 10, "why_human": "account_bound", "if_skipped": "The post never goes up"}


# ---------------------------------------------------------------- holds and keys stay inside the project's DAG

def test_a_hold_on_a_task_the_filer_waits_on_or_one_that_ended_is_refused(s, project):
    parent = task(s, project, "parent_step", status="RUNNING")
    author = by_task(s, project.id, depends_on=(parent.id,))
    task(s, project, "finished_step", status="DONE")
    project_manager = by_project_manager(s, project.id)

    assert refused(s, ask(context=contract(HoldKind.TASKS, keys=("parent_step",), owner_work=False)), author,
                   project) == ["hold_target_is_ancestor", "hold_not_allowed"]
    assert refused(s, ask(context=contract(HoldKind.TASKS, keys=("finished_step",), owner_work=False)), project_manager,
                   project) == ["hold_target_terminal"]
    assert refused(s, ask(context=contract(HoldKind.TASKS, keys=("no_such_step",), owner_work=False)), project_manager,
                   project) == ["hold_target_unknown"]


def test_refs_and_keys_resolve_only_inside_the_cards_own_project(s, project):
    other = projects.create(s, "다른 목표", name="Other", budget_usd=10, start=False)
    theirs, their_ev = task(s, other, "their_step"), evidence(s, other)
    project_manager = by_project_manager(s, project.id)

    assert refused(s, ask(context=contract(reason=AskReason.STEP_NOT_RUN, refs=(f"T-{theirs.id}",),
                                           owner_work=False)), project_manager, project) == ["ref_missing"]
    assert refused(s, ask(context=contract(reason=AskReason.STEP_NOT_RUN, refs=(f"EV-{their_ev.id}",),
                                           owner_work=False)), project_manager, project) == ["ref_missing"]
    assert refused(s, ask(context=contract(HoldKind.TASKS, keys=("their_step",), owner_work=False)), project_manager,
                   project) == ["hold_target_unknown"]


def test_a_refused_draft_writes_no_row_and_no_link(s, project):
    before = (s.scalar(select(func.count(AgentRequest.id))), s.scalar(select(func.count(RequestLink.id))))

    assert refused(s, ask(context=contract(HoldKind.RUN, reason=AskReason.SPEND, refs=("EV-999999",),
                                           owner_work=False)),
                   by_project_manager(s, project.id), project) == ["ref_missing", "run_not_goal_unclear"]
    stored = AgentRequestDraft.model_validate(ask().model_dump(exclude={"context"}))  # a result from before the contract
    assert [r.key for r in requests.check(s, stored, by_task(s, project.id), project.id)] == ["context_missing"]
    assert (s.scalar(select(func.count(AgentRequest.id))), s.scalar(select(func.count(RequestLink.id)))) == before


# ---------------------------------------------------------------- joined and revised cards

def test_a_joined_card_keeps_both_askers_and_holds_what_either_held(s, project):
    first, second = by_task(s, project.id), by_task(s, project.id)
    card = requests.create(s, ask(title="Which audience first"), from_agent="research", project_id=project.id,
                           origin=first)
    same = requests.create(s, ask(title="Which audience first", context=contract(HoldKind.THIS_TASK,
                                                                                 owner_work=False)),
                           from_agent="marketing", project_id=project.id, origin=second)

    assert same.id == card.id and card.hold_scope == "tasks"
    assert [x[:3] for x in links(s, card, "raised_by")] == [("raised_by", "task", first.task_id),
                                                           ("raised_by", "task", second.task_id)]
    assert [x[:4] for x in links(s, card, "holds")] == [("holds", "task", second.task_id, "finish")]


def test_a_revised_card_ends_its_old_agent_rows_and_keeps_the_server_rows(s, project):
    origin = by_project_manager(s, project.id)
    replans = contract(on_answer=(AnswerUse(effect=AnswerEffect.REPLANS, option=None, task_keys=[]),),
                       owner_work=False)
    first = requests.create(s, ask(context=replans), from_agent="project_manager", project_id=project.id, origin=origin)
    informs = contract(on_answer=(AnswerUse(effect=AnswerEffect.INFORMS, option=PICK, task_keys=[]),),
                       owner_work=False)
    again = requests.create(s, ask(title="Which audience comes first", replaces=f"REQ-{first.id}", context=informs),
                            from_agent="project_manager", project_id=project.id, origin=origin)

    assert again.id == first.id and requests.revision(again) == 2
    rows = links(s, again)
    assert ("answer_use", "project", project.id, None, None, "replans", 1, "agent", True) in rows
    assert ("answer_use", "project", project.id, None, PICK, "informs", 2, "agent", False) in rows
    assert ("raised_by", "round", origin.round_id, None, None, None, 1, "server", False) in rows


def test_a_revision_that_changes_the_rule_changes_whether_the_card_has_a_deadline(s, project):
    """A card that waited with no end and is revised to close gets a deadline, so the expiry sweep can close it; one
    revised to wait with no end loses its deadline."""
    origin = by_project_manager(s, project.id)
    waits = requests.create(s, ask(context=contract(expiry=ExpiryPolicy.KEEP_WAITING, owner_work=False)),
                            from_agent="project_manager", project_id=project.id, origin=origin)
    assert waits.expires_at is None

    closes = requests.create(s, ask(title="Which audience comes first", replaces=f"REQ-{waits.id}",
                                    context=contract(owner_work=False)),
                             from_agent="project_manager", project_id=project.id, origin=origin)
    assert closes.id == waits.id and closes.expiry_policy == "close" and closes.expires_at is not None

    again = requests.create(s, ask(title="Which audience to try first", replaces=f"REQ-{waits.id}",
                                   context=contract(expiry=ExpiryPolicy.KEEP_WAITING, owner_work=False)),
                            from_agent="project_manager", project_id=project.id, origin=origin)
    assert again.id == waits.id and again.expires_at is None


# ---------------------------------------------------------------- REQ-6, as a new card

def test_a_req6_shaped_project_manager_question_is_stored_with_every_column_and_link(s, project):
    comparison = task(s, project, "compare_chat_vs_cli", status="FAILED")
    ev = evidence(s, project, comparison)
    step = by_project_manager(s, project.id)
    draft = ask(title="Next feature to build", recommended_option=PICK, context=contract(
        HoldKind.PLANNING, reason=AskReason.STEP_NOT_RUN, refs=(f"T-{comparison.id}", f"EV-{ev.id}"),
        note="The comparison was designed and never run", expiry=ExpiryPolicy.DEFAULT, default=PICK,
        on_answer=(AnswerUse(effect=AnswerEffect.REPLANS, option=None, task_keys=[]),), owner_work=False))

    req = requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=step)

    assert (req.context_version, req.ask_reason, req.reason_note, req.hold_scope, req.blocking, req.expiry_policy,
            req.default_option, req.task_id) == (1, "STEP_NOT_RUN", "The comparison was designed and never run",
                                                 "planning", True, "default", PICK, None)
    assert req.reason_refs == [{"kind": "task", "id": comparison.id}, {"kind": "evidence", "id": ev.id}]
    assert links(s, req) == [("raised_by", "round", step.round_id, None, None, None, 1, "server", False),
                             ("answer_use", "project", project.id, None, None, "replans", 1, "agent", False)]


# ---------------------------------------------------------------- where refusals go

def test_a_project_manager_steps_refusal_goes_on_its_round_and_into_the_next_project_manager_prompt(s, project):
    step = by_project_manager(s, project.id)
    bad = ask(title="Pause everything for pricing", context=contract(HoldKind.RUN, reason=AskReason.SPEND,
                                                                    owner_work=False))

    assert requests.from_agent_drafts(s, [bad], agent="project_manager", project_id=project.id, origin=step) == []

    row = s.get(ReviewRound, step.round_id)
    assert [x["refused"] for x in row.refusals] == [["refs_empty", "run_not_goal_unclear"]]
    brief = ctx.project_brief(s, project)
    assert "Requests the server did not file" in brief and "Pause everything for pricing" in brief


def _drafted(s, project, draft) -> Task:
    t = s.get(Task, by_task(s, project.id).task_id)
    t.iteration = 1
    t.result = {"summary": "s", "findings_markdown": "f", "evidence_ids": [], "request_ids": [],
                "requests": [draft.model_dump(mode="json")]}
    s.flush()
    return t


def test_a_task_is_sent_back_once_then_its_refused_request_is_not_filed(s, project, monkeypatch):
    monkeypatch.setattr(company_graph, "_write_task_artifact", lambda *a, **k: None)
    t = _drafted(s, project, ask(title="Hold the whole project", context=contract(HoldKind.RUN, owner_work=False)))

    feedback = company_graph._send_back(t)
    assert "hold_not_allowed" in feedback and t.status == "REWORK"
    assert company_graph._send_back(t) is None, "once"

    t.status = "REVIEW"
    company_graph._finish(s, s.get(type(project), project.id), t, company_graph.PASSED, "en")

    assert t.result["requests"] == [] and t.result["request_not_filed"][0]["refused"] == ["hold_not_allowed"]
    line = s.scalar(select(ActivityEvent).where(ActivityEvent.verb == "request_not_filed",
                                                ActivityEvent.entity_id == t.id))
    assert line.summary.startswith(msg("path.not_filed", "en", title="", why="").split(":")[0])
    assert s.scalar(select(AgentRequest.id).where(AgentRequest.task_id == t.id)) is None


def test_a_question_cannot_default_to_an_option_that_declines(s, project):
    """A deadline may stand in for an answer, never for a no the owner could not pick themselves (respond refuses an
    answer that is a decline)."""
    no = "Reject this draft"
    draft = ask(options=["Ship it", no], recommended_option=no,
                context=contract(expiry=ExpiryPolicy.DEFAULT, default=no, owner_work=False))
    assert refused(s, draft, by_project_manager(s, project.id), project) == ["default_declines"]
