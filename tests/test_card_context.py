"""A decision card says where it sits in the work from the records alone: why it is asked, the chain from the goal to the
question, what it holds up, what answering does and what happens if nobody answers. What an old card never recorded
reads as unknown, and a failure to build the context never costs the owner the card."""
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from bet_loop_kit import make_bet, make_business
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.contracts.work_path import AskReason, HoldPhase, Rel, ReviewKind, TargetKind
from justshipit.db.models import (
    AgentRequest,
    CycleStage,
    Evidence,
    LLMCallRecord,
    RequestLink,
    ReviewRound,
    Task,
)
from justshipit.agents import roles
from justshipit.services import projects, work_path
from sqlalchemy import select

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def work(s):
    """A project of a business with an approved plan, tied to its running stage and to an experiment."""
    business = make_business(s)
    bet = make_bet(s, business)
    p = projects.create(s, "다음 기능을 정한다", name="비교 실험", budget_usd=10, start=False)
    p.business_id, p.bet_id = business.id, bet.id
    p.stage_id = s.scalar(select(CycleStage.id).where(CycleStage.business_id == business.id))
    s.flush()
    return p


def card(s, project, *, title="다음에 만들 기능은?", status="PENDING", scope="run", **kw) -> AgentRequest:
    r = AgentRequest(project_id=project.id if project else None, from_agent=kw.pop("agent", "project_manager"), type="QUESTION",
                     title=title, summary="s", rationale="r", policy_mode="approve", status=status, hold_scope=scope,
                     **kw)
    s.add(r)
    s.flush()
    return r


def task(s, project, key, status="RUNNING", depends_on=()) -> Task:
    t = Task(project_id=project.id, key=key, title=f"업무 {key}", owner="research", objective="o", status=status,
             depends_on=list(depends_on))
    s.add(t)
    s.flush()
    return t


def link(s, req, rel, kind, target, **kw) -> RequestLink:
    row = RequestLink(request_id=req.id, rel=rel.value, target_kind=kind.value, target_id=target, revision=1,
                      source=kw.pop("source", "agent"), known=kw.pop("known", "recorded"), **kw)
    s.add(row)
    s.flush()
    return row


def contexts(s, req, says=None):
    return work_path.card_for(s, req, says=says, now=NOW)


def kinds(ctx) -> list[str]:
    return [x.kind for x in ctx.chain]


def test_a_card_from_before_the_work_path_shows_what_was_recorded_and_says_not_recorded_for_the_rest(s, work):
    req = card(s, work, expires_at=NOW + timedelta(days=2))

    ctx = contexts(s, req)

    assert kinds(ctx) == ["goal", "stage", "experiment", "project", "origin", "this"]
    goal, stage, experiment, project, origin, this = ctx.chain
    assert "30" in goal.vars["text"] and goal.known == "derived"
    assert (stage.known, experiment.known, project.known) == ("recorded",) * 3
    assert project.ref == f"P-{work.id}" and this.ref == f"REQ-{req.id}" and this.state == "PENDING"
    assert (origin.known, origin.label_key) == ("unknown", "path.step.unknown")
    assert (ctx.why.reason, ctx.why.note, ctx.why.recorded, ctx.why.refs) == (None, None, False, [])
    assert ctx.holds.scope == "run" and ctx.parks_project and not ctx.holds.nothing_live
    assert ctx.unanswered.policy is None and ctx.unanswered.when == req.expires_at
    assert ctx.after == [] and not ctx.complete and not ctx.decided


def test_a_decided_old_card_still_names_what_raised_it(s, work):
    """The backfill links an old card to the task its row names; answering the card does not end that link."""
    origin = task(s, work, "compare", status="DONE")
    req = card(s, work, status="ANSWERED", resolved_at=NOW)
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, origin.id, source="backfill", known="backfilled")

    step = contexts(s, req).chain[-2]
    assert (step.kind, step.ref, step.known) == ("task", f"T-{origin.id}", "backfilled")


@pytest.mark.parametrize("agent", [roles.PROJECT_MANAGER, roles.FORMER_PROJECT_MANAGER])
def test_a_call_link_names_the_project_manager_step_that_wrote_the_card_and_says_it_was_matched(s, work, agent):
    call = LLMCallRecord(call_id=f"call-matched-1-{agent}", project_id=work.id, agent=agent, purpose="review",
                         provider="claude", status="ok")
    other = LLMCallRecord(call_id=f"call-matched-2-{agent}", project_id=work.id, agent="research", purpose="review",
                          provider="claude", status="ok")
    s.add_all([call, other])
    s.flush()
    req, plain = card(s, work), card(s, work, title="다른 질문")
    link(s, req, Rel.RAISED_BY, TargetKind.CALL, call.id, source="backfill", known="matched")
    link(s, plain, Rel.RAISED_BY, TargetKind.CALL, other.id, source="backfill", known="matched")

    step = contexts(s, req).chain[-2]
    assert (step.kind, step.label_key, step.known) == ("call", "path.step.call.review", "matched")
    assert contexts(s, plain).chain[-2].label_key == "path.step.call.other"


def test_a_backfilled_link_is_shown_as_backfilled(s, work):
    req = card(s, work)
    t = task(s, work, "a")
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, t.id, source="backfill", known="backfilled")

    step = contexts(s, req).chain[-2]

    assert (step.kind, step.known, step.ref) == ("task", "backfilled", f"T-{t.id}")


def test_a_card_filed_under_the_work_path_has_every_field(s, work):
    asker, first, second, after = (task(s, work, k) for k in ("ask", "first", "second", "after"))
    after.depends_on = [first.id]
    ev = Evidence(project_id=work.id, agent="research", claim="비교를 아직 돌리지 않았다", kind="analysis")
    s.add(ev)
    s.flush()
    req = card(s, work, scope="tasks", context_version=1, ask_reason="STEP_NOT_RUN",
               reason_refs=[{"kind": "evidence", "id": ev.id}, {"kind": "task", "id": asker.id}],
               reason_note="비교 업무가 계획만 되고 돌지 않았어요", expiry_policy="default", default_option="체크리스트 채팅",
               expires_at=NOW + timedelta(days=3))
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, asker.id)
    link(s, req, Rel.HOLDS, TargetKind.TASK, first.id, phase="start")
    link(s, req, Rel.HOLDS, TargetKind.TASK, second.id, phase="finish")
    link(s, req, Rel.ANSWER_USE, TargetKind.PROJECT, work.id, option_key="체크리스트 채팅", effect="replans")
    link(s, req, Rel.ANSWER_USE, TargetKind.TASK, after.id, option_key="체크리스트 채팅", effect="feeds")

    ctx = contexts(s, req, says="답이 팀에 전달돼요.")

    assert ctx.why.reason == "STEP_NOT_RUN" and ctx.why.recorded and ctx.why.agent == "project_manager"
    assert ctx.why.note == "비교 업무가 계획만 되고 돌지 않았어요" and not ctx.why.stale
    assert [(x.kind, x.id, x.label) for x in ctx.why.refs] == [("evidence", ev.id, "비교를 아직 돌리지 않았다"),
                                                              ("task", asker.id, "업무 ask")]
    assert kinds(ctx) == ["goal", "stage", "experiment", "project", "task", "this"]
    assert (ctx.chain[-2].ref, ctx.chain[-2].known) == (f"T-{asker.id}", "recorded")
    held = {x.id: (x.phase, x.live) for x in ctx.holds.tasks}
    assert held == {first.id: (HoldPhase.START, True), second.id: (HoldPhase.FINISH, True)}
    assert (ctx.holds.frees, ctx.holds.behind, ctx.holds.nothing_live) == (2, 1, False)
    assert not ctx.parks_project, "the task that asked is still running, so the rest of the project goes on"
    declared = [x for x in ctx.after if x.source == "declared"]
    assert sorted(x.effect for x in declared) == ["feeds", "replans"]
    assert {x.option_key for x in declared} == {"체크리스트 채팅"}
    assert [x.targets[0].id for x in declared if x.effect == "feeds"] == [after.id]
    assert [x.text for x in ctx.after if x.source == "server"] == ["답이 팀에 전달돼요."]
    assert (ctx.unanswered.policy, ctx.unanswered.default_option, ctx.unanswered.when) == (
        "default", "체크리스트 채팅", req.expires_at)
    assert {x.id for x in ctx.unanswered.affects} == {first.id, second.id}
    assert ctx.complete and ctx.as_of == NOW


def test_a_card_several_agents_asked_keeps_every_asker(s, work):
    one, two = task(s, work, "one"), task(s, work, "two")
    req = card(s, work)
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, one.id)
    link(s, req, Rel.RAISED_BY, TargetKind.TASK, two.id)

    ctx = contexts(s, req)

    assert ctx.chain[-2].ref_id == one.id and [x.ref_id for x in ctx.also_asked] == [two.id]


def test_a_card_whose_held_task_was_cancelled_holds_nothing_now_and_stays_answerable(s, work):
    gone = task(s, work, "gone", status="CANCELLED")
    gone.finished_at = NOW - timedelta(days=1)
    req = card(s, work, scope="tasks")
    link(s, req, Rel.HOLDS, TargetKind.TASK, gone.id, phase="start")

    ctx = contexts(s, req)

    assert ctx.holds.nothing_live and (ctx.holds.frees, ctx.holds.behind) == (0, 0)
    assert [(x.id, x.live, x.state, x.ended_on) for x in ctx.holds.tasks] == [
        (gone.id, False, "CANCELLED", gone.finished_at)]
    assert ctx.unanswered.affects == []


def test_a_task_held_by_two_cards_is_freed_by_neither(s, work):
    shared, mine = task(s, work, "shared"), task(s, work, "mine")
    a, b = card(s, work, scope="tasks"), card(s, work, title="다른 질문", scope="tasks")
    for req in (a, b):
        link(s, req, Rel.HOLDS, TargetKind.TASK, shared.id, phase="start")
    link(s, a, Rel.HOLDS, TargetKind.TASK, mine.id, phase="start")

    assert contexts(s, a).holds.frees == 1 and contexts(s, b).holds.frees == 0


def test_a_decided_card_reads_what_followed_and_no_longer_holds_or_waits(s, work):
    t = task(s, work, "go")
    req = card(s, work, status="ANSWERED", scope="tasks", context_version=1, ask_reason="INPUT_MISSING",
               expiry_policy="close")
    link(s, req, Rel.HOLDS, TargetKind.TASK, t.id, phase="start")
    link(s, req, Rel.ANSWER_USE, TargetKind.PROJECT, work.id, effect="replans")
    link(s, req, Rel.FOLLOWED, TargetKind.TASK, t.id, option_key="a", effect="replans", source="server")

    ctx = contexts(s, req, says="ignored once decided")

    assert ctx.decided and ctx.holds is None and ctx.unanswered is None and not ctx.parks_project
    assert [(x.source, x.option_key, x.effect, x.targets[0].id) for x in ctx.after] == [("followed", "a", "replans", t.id)]
    assert ctx.after[0].at is not None


def test_a_note_is_stale_when_its_round_was_followed_by_another_or_a_task_it_cites_ended(s, work):
    done = task(s, work, "done", status="DONE")
    old, new = (ReviewRound(project_id=work.id, seq=n, kind="review", plan_round=n) for n in (1, 2))
    s.add_all([old, new])
    s.flush()
    cited = card(s, work, context_version=1, ask_reason="STEP_NOT_RUN", expiry_policy="close",
                 reason_refs=[{"kind": "task", "id": done.id}], reason_note="n")
    stale = card(s, work, title="낡은 질문", context_version=1, ask_reason="GOAL_UNCLEAR", expiry_policy="close",
                 reason_note="n")
    link(s, stale, Rel.RAISED_BY, TargetKind.ROUND, old.id)
    fresh = card(s, work, title="새 질문", context_version=1, ask_reason="GOAL_UNCLEAR", expiry_policy="close",
                 reason_note="n")
    link(s, fresh, Rel.RAISED_BY, TargetKind.ROUND, new.id)

    assert [(x.kind, x.id) for x in contexts(s, cited).why.changed] == [("task", done.id)]
    assert contexts(s, cited).why.stale
    assert [(x.kind, x.id) for x in contexts(s, stale).why.changed] == [("round", old.id)]
    assert not contexts(s, fresh).why.stale
    assert contexts(s, stale).chain[-2].label_key == "path.step.round.review"


def test_a_company_wide_card_has_no_goal_or_project_to_name(s):
    req = card(s, None, scope="none")

    ctx = contexts(s, req)

    assert kinds(ctx) == ["origin", "this"] and ctx.holds.tasks == []


def test_the_route_returns_the_context_with_the_card(s, work, api):
    req = card(s, work, expires_at=NOW + timedelta(days=2))
    s.commit()

    body = api.get(f"/api/requests/{req.id}").json()

    ctx = body["context"]
    assert ctx["request_id"] == req.id and ctx["holds"]["scope"] == "run" and ctx["unanswered"]["policy"] is None
    assert [x["kind"] for x in ctx["chain"]][-2:] == ["origin", "this"]
    assert datetime.fromisoformat(ctx["as_of"]).tzinfo is not None


def test_the_card_still_returns_when_its_context_cannot_be_built(s, work, api, monkeypatch, caplog):
    req = card(s, work)
    s.commit()

    def broken(*a, **k):
        raise RuntimeError("no graph today")
    monkeypatch.setattr(work_path, "card_context", broken)

    r = api.get(f"/api/requests/{req.id}")

    assert r.status_code == 200 and r.json()["context"] == {"error": True} and r.json()["title"] == req.title
    assert "no graph today" in caplog.text


def test_every_text_the_server_names_is_worded_in_both_languages_by_the_console():
    """The server sends catalog keys (`path.step.*`, an `AskReason`, an effect); a key the console lacks would read as
    "Not recorded", so each one the records can produce must exist, in English and Korean."""
    source = (Path(__file__).parents[1] / "web" / "src" / "i18n" / "areas" / "path.ts").read_text(encoding="utf-8")
    entries = dict(re.findall(r'"(path\.[\w.]+)": \{(.*?)\},?\r?\n', source, re.DOTALL))
    steps = ["goal", "cycle", "stage", "experiment", "project", "task", "decision", "request", "chat", "unknown", "this",
             *(f"round.{k.value}" for k in ReviewKind), *(f"server.{k.value}" for k in work_path.SERVER_STEPS),
             *(f"call.{p}" for p in (*work_path.PROJECT_MANAGER_CALLS, "other"))]
    effects = ["informs", "replans", "feeds"]
    keys = [*(f"path.step.{s}" for s in steps), *(f"path.reason.{r.value}" for r in AskReason),
            *(f"path.after.{e}" for e in effects), *(f"path.happened.{e}" for e in (*effects, "other"))]

    assert [k for k in keys if k not in entries] == []
    assert [k for k in keys if not all(f"{lang}:" in entries[k] for lang in ("en", "ko"))] == []


@pytest.mark.parametrize(("status", "parks"), [("WAITING_HUMAN", True), ("COMPLETED", False), ("FAILED", False)])
def test_a_planning_card_says_the_project_waits_only_while_its_run_is_live(s, work, status, parks):
    work.status = status
    task(s, work, "done", status="DONE")
    req = card(s, work, scope="planning")

    assert contexts(s, req).parks_project is parks
