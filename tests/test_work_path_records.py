"""The work path records: the link pairs the database accepts are the ones the contract names, a hold only widens, the
live task holds, what `ship work-path audit` counts, and what `--from-calls` links."""
import json
from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest
from justshipit import cli
from justshipit.agents import roles
from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
from justshipit.contracts.work_path import ALLOWED_PAIRS, HOLD_RANK, HoldPhase, HoldScope, Rel, TargetKind, wider
from justshipit.db.models import AgentRequest, LLMCallRecord, RequestLink, Task
from justshipit.services import holds, projects, requests, work_path_audit
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner
from ask_kit import contract, by_project_manager
from justshipit.contracts.work_path import HoldKind


def card(s, project, *, title="t", status="PENDING", scope="none", agent="project_manager", **kw) -> AgentRequest:
    r = AgentRequest(project_id=project.id, from_agent=agent, type="QUESTION", title=title, summary="s",
                     rationale="r", policy_mode="approve", status=status, hold_scope=scope, **kw)
    s.add(r)
    s.flush()
    return r


def task(s, project, key="t1", status="RUNNING") -> Task:
    t = Task(project_id=project.id, key=key, title=key, owner="research", objective="o", status=status)
    s.add(t)
    s.flush()
    return t


def link(s, req, rel, kind, target, **kw) -> RequestLink:
    row = RequestLink(request_id=req.id, rel=rel.value, target_kind=kind.value, target_id=target, revision=1,
                      source=kw.pop("source", "server"), known=kw.pop("known", "recorded"), **kw)
    s.add(row)
    s.flush()
    return row


def refused(s, stmt) -> bool:
    """Whether the database refuses the statement. It is undone either way."""
    savepoint = s.begin_nested()
    try:
        s.execute(stmt)
        return False
    except IntegrityError:
        return True
    finally:
        savepoint.rollback()


def edge(req, rel, kind, **kw):
    return insert(RequestLink).values(request_id=req.id, rel=rel, target_kind=kind, target_id=1, revision=1,
                                      source="server", known="recorded", **kw)


def test_the_database_accepts_exactly_the_link_pairs_the_contract_names(s, project):
    req = card(s, project)

    for rel, kind in product(Rel, TargetKind):
        assert refused(s, edge(req, rel.value, kind.value)) == (kind not in ALLOWED_PAIRS[rel]), (rel, kind)
    assert refused(s, edge(req, "unknown", "task")) and refused(s, edge(req, "holds", "nowhere"))


def test_a_phase_belongs_to_a_task_hold_only(s, project):
    req = card(s, project)

    assert not refused(s, edge(req, "holds", "task", phase="start"))
    assert refused(s, edge(req, "raised_by", "task", phase="start"))
    assert refused(s, edge(req, "holds", "spec", phase="start"))


def test_an_edge_is_written_once_per_revision_whatever_its_option_key(s, project):
    req = card(s, project)
    link(s, req, Rel.ANSWER_USE, TargetKind.PROJECT, 1, option_key="a", effect="replans")
    link(s, req, Rel.ANSWER_USE, TargetKind.PROJECT, 1, option_key="b", effect="replans")

    assert refused(s, edge(req, "answer_use", "project", option_key="a", effect="replans"))
    assert not refused(s, edge(req, "answer_use", "project", option_key="c"))


def test_a_card_filed_under_the_work_path_gives_its_reason_and_its_expiry_rule(s, project):
    def filed(**kw):
        return insert(AgentRequest).values(project_id=project.id, from_agent="project_manager", type="QUESTION", title="t",
                                           summary="s", rationale="r", policy_mode="approve", context_version=1, **kw)

    assert refused(s, filed())
    assert refused(s, filed(ask_reason="GOAL_UNCLEAR"))
    assert refused(s, filed(ask_reason="GOAL_UNCLEAR", expiry_policy="default"))
    assert not refused(s, filed(ask_reason="GOAL_UNCLEAR", expiry_policy="default", default_option="A"))
    assert refused(s, filed(ask_reason="GOAL_UNCLEAR", expiry_policy="forever"))


@pytest.mark.parametrize("a, b, expected", [
    (HoldScope.NONE, HoldScope.TASKS, HoldScope.TASKS), (HoldScope.RUN, HoldScope.PLANNING, HoldScope.RUN),
    (HoldScope.TASKS, HoldScope.PLANNING, HoldScope.PLANNING), ("none", "none", HoldScope.NONE)])
def test_two_holds_merge_into_the_wider(a, b, expected):
    assert wider(a, b) == wider(b, a) == expected


def test_the_scopes_rank_from_nothing_to_the_whole_run():
    assert sorted(HoldScope, key=HOLD_RANK.__getitem__) == [HoldScope.NONE, HoldScope.TASKS, HoldScope.PLANNING,
                                                           HoldScope.RUN]


def test_blocking_reads_the_scope_in_the_same_session_once_it_is_written(s, project):
    req = card(s, project)
    assert req.blocking is False

    requests._hold(s, req, HoldScope.PLANNING)
    assert (req.hold_scope, req.blocking) == ("planning", True)

    requests._hold(s, req, HoldScope.TASKS)  # a narrower scope never takes a hold away
    assert (req.hold_scope, req.blocking) == ("planning", True)


def test_a_blocking_draft_files_a_card_that_holds_the_run(s, project):
    draft = AgentRequestDraft(type=RequestType.QUESTION, title="어느 쪽?", summary="s", rationale="r", options=[],
                              recommended_option=None, action_kind=None, estimated_cost_usd=None,
                              risk_level=RiskLevel.LOW, context=contract(HoldKind.RUN))

    req = requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))

    assert (req.hold_scope, req.blocking) == ("run", True)
    same = requests.create(s, draft.model_copy(update={"context": contract()}), from_agent="cpo", project_id=project.id,
                           origin=by_project_manager(s, project.id))
    assert (same.id, same.hold_scope) == (req.id, "run")


def test_live_holds_are_the_open_unended_holds_of_tasks_still_working(s, project):
    working, finished, ended_one, answered = (task(s, project, k, st) for k, st in (
        ("a", "RUNNING"), ("b", "DONE"), ("c", "READY"), ("d", "APPROVAL_REQUIRED")))
    open_card, answered_card = card(s, project, scope="tasks"), card(s, project, status="APPROVED")
    link(s, open_card, Rel.HOLDS, TargetKind.TASK, working.id, phase="start")
    link(s, open_card, Rel.HOLDS, TargetKind.TASK, finished.id, phase="start")
    link(s, open_card, Rel.HOLDS, TargetKind.TASK, ended_one.id, phase="start", ended_at=datetime.now(UTC))
    link(s, answered_card, Rel.HOLDS, TargetKind.TASK, answered.id, phase="finish")
    link(s, open_card, Rel.RAISED_BY, TargetKind.TASK, working.id)

    assert holds.live_holds(s, project.id) == [holds.Hold(open_card.id, working.id, HoldPhase.START)]


def test_a_hold_follows_its_card_to_the_card_it_was_merged_into(s, project):
    waiting = task(s, project)
    old, kept = card(s, project, status="CANCELLED"), card(s, project)
    old.response = {"decision": "merged", "into": f"REQ-{kept.id}"}
    link(s, old, Rel.HOLDS, TargetKind.TASK, waiting.id, phase="finish")

    assert holds.live_holds(s, project.id) == [holds.Hold(kept.id, waiting.id, HoldPhase.FINISH)]

    kept.status = "REJECTED"
    assert holds.live_holds(s, project.id) == []


def test_live_holds_are_read_per_project(s, project):
    other = projects.create(s, "다른 목표", name="Other", budget_usd=10, start=False)
    theirs = task(s, other)
    link(s, card(s, project), Rel.HOLDS, TargetKind.TASK, task(s, project).id, phase="start")
    link(s, card(s, other), Rel.HOLDS, TargetKind.TASK, theirs.id, phase="start")

    assert [h.task_id for h in holds.live_holds(s, other.id)] == [theirs.id]


def _counts(s) -> dict[str, dict[str, int]]:
    return {f: dict(c) for f, c in work_path_audit.audit(s).items()}


def test_the_audit_counts_each_field_by_how_it_is_known(s, project):
    before = _counts(s)
    filed = card(s, project, context_version=1, ask_reason="STEP_NOT_RUN", expiry_policy="close")
    asked = card(s, project, expiry_policy="keep_waiting")
    bare = card(s, project)
    link(s, asked, Rel.RAISED_BY, TargetKind.TASK, 1, source="backfill", known="backfilled")
    link(s, filed, Rel.RAISED_BY, TargetKind.ROUND, 1)
    link(s, bare, Rel.RAISED_BY, TargetKind.CALL, 1, source="backfill", known="matched")
    after = _counts(s)

    def moved(field: str) -> dict[str, int]:
        return {k: after[field][k] - before[field][k] for k in after[field] if after[field][k] != before[field][k]}

    assert moved("origin") == {"recorded": 1, "backfilled": 1, "matched": 1}
    assert moved("reason") == {"recorded": 1, "unknown": 2}
    assert moved("holds") == {"recorded": 1, "backfilled": 2}
    assert moved("answer use") == {"recorded": 1, "unknown": 2}
    assert moved("expiry rule") == {"recorded": 1, "backfilled": 1, "unknown": 1}


def _call(s, project, tmp_path, name: str, drafts, *, agent="project_manager", status="ok", file=True, later=False):
    folder = tmp_path / name
    folder.mkdir()
    if file:
        (folder / "output.json").write_text(json.dumps({"requests": drafts}, ensure_ascii=False), encoding="utf-8")
    row = LLMCallRecord(call_id=f"call-{name}-{uuid4().hex[:8]}", project_id=project.id, agent=agent, purpose="plan",
                        provider="claude", status=status, raw_path=str(folder),
                        created_at=datetime.now(UTC) + timedelta(days=1 if later else -1))
    s.add(row)
    s.flush()
    return row


def mine(found, *cards):
    """What the backfill reported for these cards: the test database also holds the cards other tests left."""
    return [m for m in found if m.request_id in {c.id for c in cards}]


def _raised_by(s, req) -> list[tuple]:
    return [(x.target_kind, x.target_id, x.known, x.source) for x in s.query(RequestLink).filter_by(
        request_id=req.id, rel=Rel.RAISED_BY.value)]


def test_one_call_whose_draft_has_the_cards_title_links_the_card_as_matched(s, project, tmp_path):
    asked = card(s, project, title="가격을 정해 주세요")
    wrote = _call(s, project, tmp_path, "a", [{"title": "  가격을   정해 주세요 "}, {"title": "다른 질문"}])
    _call(s, project, tmp_path, "b", [{"title": "다른 질문"}])
    _call(s, project, tmp_path, "c", [{"title": "가격을 정해 주세요"}], agent="research")
    _call(s, project, tmp_path, "d", [{"title": "가격을 정해 주세요"}], status="error")
    _call(s, project, tmp_path, "e", [{"title": "가격을 정해 주세요"}], later=True)

    found = mine(work_path_audit.backfill_from_calls(s), asked)

    assert [(m.request_id, m.call_ids, m.linked) for m in found] == [(asked.id, (wrote.call_id,), True)]
    assert _raised_by(s, asked) == [("call", wrote.id, "matched", "backfill")]


def test_a_card_filed_under_the_former_id_is_linked_to_the_call_that_wrote_it(s, project, tmp_path):
    asked = card(s, project, title="어느 도시부터 갈까요", agent=roles.FORMER_PROJECT_MANAGER)
    wrote = _call(s, project, tmp_path, "old", [{"title": "어느 도시부터 갈까요"}], agent=roles.FORMER_PROJECT_MANAGER)

    found = mine(work_path_audit.backfill_from_calls(s), asked)

    assert [(m.call_ids, m.linked) for m in found] == [((wrote.call_id,), True)]


def test_a_card_stays_unknown_when_its_call_cannot_be_told_from_another_or_cannot_be_read(s, project, tmp_path):
    twice, revised, missing, unread, by_product = (card(s, project, title=t) for t in (
        "두 번 물음", "고친 질문", "파일 없음", "읽을 수 없음", "다른 에이전트"))
    by_product.from_agent = "product"
    first = _call(s, project, tmp_path, "a", [{"title": "두 번 물음"}])
    second = _call(s, project, tmp_path, "b", [{"title": "두 번 물음"}])
    _call(s, project, tmp_path, "c", [{"title": "고치기 전 질문"}])
    _call(s, project, tmp_path, "d", [{"title": "파일 없음"}], file=False)
    _call(s, project, tmp_path, "e", [{"title": "읽을 수 없음"}])
    (tmp_path / "e" / "output.json").write_text("not json", encoding="utf-8")
    _call(s, project, tmp_path, "f", [{"title": "다른 에이전트"}])

    found = mine(work_path_audit.backfill_from_calls(s), twice, revised, missing, unread, by_product)

    assert {m.request_id: (m.call_ids, m.linked) for m in found} == {
        twice.id: ((first.call_id, second.call_id), False), revised.id: ((), False), missing.id: ((), False),
        unread.id: ((), False)}
    assert [_raised_by(s, c) for c in (twice, revised, missing, unread, by_product)] == [[]] * 5


def test_backfilling_again_links_nothing_new_and_a_dry_run_links_nothing(s, project, tmp_path):
    asked = card(s, project, title="한 번만 연결")
    _call(s, project, tmp_path, "a", [{"title": "한 번만 연결"}])

    assert [m.linked for m in mine(work_path_audit.backfill_from_calls(s, dry_run=True), asked)] == [True]
    assert _raised_by(s, asked) == []

    work_path_audit.backfill_from_calls(s)
    work_path_audit.backfill_from_calls(s)

    assert len(_raised_by(s, asked)) == 1


def test_a_card_with_an_origin_already_is_left_to_it(s, project, tmp_path):
    known = card(s, project, title="이미 아는 질문")
    link(s, known, Rel.RAISED_BY, TargetKind.ROUND, 4)
    _call(s, project, tmp_path, "a", [{"title": "이미 아는 질문"}])

    assert mine(work_path_audit.backfill_from_calls(s), known) == []
    assert [x[0] for x in _raised_by(s, known)] == ["round"]


def test_the_cli_names_what_it_would_link_and_refuses_without_a_source(s, project, tmp_path):
    card(s, project, title="명령으로 연결")
    wrote = _call(s, project, tmp_path, "a", [{"title": "명령으로 연결"}])
    s.commit()
    run = CliRunner()

    assert run.invoke(cli.app, ["work-path", "backfill"]).exit_code == 1
    dry = run.invoke(cli.app, ["work-path", "backfill", "--from-calls", "--dry-run"])
    assert dry.exit_code == 0 and "would be linked" in dry.output and f"matched call {wrote.call_id}" in dry.output

    audit = run.invoke(cli.app, ["work-path", "audit"])
    assert audit.exit_code == 0 and all(f in audit.output for f in ("origin", "reason", "holds", "answer use"))


def test_the_activity_log_records_a_new_cards_scope_as_blocking(s, project):
    from justshipit.db.models import ActivityEvent
    draft = AgentRequestDraft(type=RequestType.QUESTION, title="로그에 남는 질문", summary="s", rationale="r", options=[],
                              recommended_option=None, action_kind=None, estimated_cost_usd=None,
                              risk_level=RiskLevel.LOW, context=contract(HoldKind.RUN))

    req = requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    made = s.query(ActivityEvent).filter_by(entity="request", entity_id=req.id, verb="created").one()
    assert made.data["blocking"] is True
