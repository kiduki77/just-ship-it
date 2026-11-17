"""The shape of an analysis run (critic round 2026-11-04): 견적봇 spent $37.97 of $40 on research before development,
원두재고 asked for development only in its final report 2h20m in, the final reports were 57-133KB with 85-91% of it the
evidence table, 162 of 219 evidence items were assumptions, and 동네산책 raised 18 decisions in two hours."""
from sqlalchemy import select

from justshipit.agents.independence import Served
from justshipit.contracts.planning import BuildPlanOut, ProjectManagerPlanOut, ConclusionOut, FinalReportOut
from justshipit.contracts.requests import AgentAskDraft, AgentRequestDraft, RequestType, RiskLevel
from justshipit.contracts.work import AgentResultOut, ReviewOut, ReviewVerdict, TaskDraft
from justshipit.db.models import AgentRequest, Artifact, Evidence, Job, Plan, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.services import budget, rounds, tasks
from ask_kit import contract
from justshipit.contracts.work_path import HoldKind


def _draft(key: str, title: str | None = None) -> TaskDraft:
    return TaskDraft(key=key, title=title or key, owner="research", objective="o", acceptance_criteria=["c"],
                     depends_on=[])


def _card(title: str, *, holds: HoldKind = HoldKind.NOTHING, type_: RequestType = RequestType.APPROVAL) -> AgentAskDraft:
    return AgentAskDraft(type=type_, title=title, summary="s", rationale="r", options=[], recommended_option=None,
                         action_kind="internal.other", estimated_cost_usd=None, risk_level=RiskLevel.LOW,
                         context=contract(holds))


# ---------------------------------------------------------------- the final report


def _report(s, project, monkeypatch, out: FinalReportOut) -> dict[str, str]:
    written: dict[str, str] = {}

    def keep(sess, p, kind, title, rel, content):
        written[kind] = content
        art = Artifact(project_id=p.id, kind=kind, title=title, path=rel, commit_sha="x")
        sess.add(art)
        sess.flush()
        return art

    s.commit()
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: out)
    monkeypatch.setattr(company_graph, "_write_artifact", keep)
    monkeypatch.setattr(company_graph.memory, "index_project", lambda *a, **k: None)
    company_graph.report({"project_id": project.id})
    return written


def test_the_report_keeps_the_ledger_in_its_own_file_and_labels_what_rests_on_assumptions(s, project, monkeypatch,
                                                                                          korean_owner):
    firm = Evidence(project_id=project.id, agent="research", claim="카페 3곳이 하루 2kg을 쓴다", kind="verified_fact",
                    verification_status="supported")
    guess = Evidence(project_id=project.id, agent="product", claim="사장님은 매일 앱을 연다", kind="assumption")
    s.add_all([firm, guess, *(Evidence(project_id=project.id, agent="strategy", claim=f"추론 {n}", kind="hypothesis")
                              for n in range(3))])
    s.flush()
    out = FinalReportOut(
        title="원두재고", executive_summary="바로 만들어요.", recommendation="진행: 카페 3곳이 쓸 수 있어요",
        conclusions=[ConclusionOut(statement="원두는 열흘마다 떨어진다", evidence_ids=[f"EV-{firm.id}"]),
                     ConclusionOut(statement="사장님은 매일 앱을 연다", evidence_ids=[f"EV-{guess.id}"]),
                     ConclusionOut(statement="유료 전환은 된다", evidence_ids=[])],
        report_markdown="## 본문\n짧은 본문", validation_plan=["2주 사용 확인"], development_needs=[], kpis=[])

    written = _report(s, project, monkeypatch, out)

    body, ledger = written["report"], written["evidence"]
    assert "| ID |" not in body and "카페 3곳이 하루 2kg을 쓴다" not in body
    assert "| ID |" in ledger and "카페 3곳이 하루 2kg을 쓴다" in ledger and "추론 2" in ledger
    summary = body.split("## 요약", 1)[1].split("\n## ", 1)[0]
    assert "확인된 근거 1건(20%)" in summary and "가정·추론 4건(80%)" in summary
    assert "결론 3개 중 2개는 가정·추론에 기대요" in summary
    assert f"- [확인된 근거] 원두는 열흘마다 떨어진다 (EV-{firm.id})" in body
    assert "- [가정·추론에 기댐] 사장님은 매일 앱을 연다" in body and "- [가정·추론에 기댐] 유료 전환은 된다" in body
    assert "'근거 목록'" in body


def test_the_report_body_states_its_limit_and_is_never_cut():
    from justshipit.contracts.schema import strict_json_schema
    body = "문장입니다. " * 4000
    out = FinalReportOut(title="t", executive_summary="요약", recommendation="진행", report_markdown=body,
                         validation_plan=[], development_needs=[], kpis=[])
    assert out.report_markdown == body   # the spec writer reads the report
    assert "8,000 Korean characters" in strict_json_schema(FinalReportOut)["properties"]["report_markdown"]["description"]


def test_a_task_summary_has_a_size_limit():
    out = AgentResultOut(summary="결론입니다. " * 400, findings_markdown="f", evidence=[], assumptions=[], risks=[],
                         open_questions=[], proposed_follow_up_tasks=[], requests=[], capability_gaps=[],
                         financial_model=None)
    assert len(out.summary) <= 500


# ---------------------------------------------------------------- owner decisions, one batch per round


def _work(project_id: int, monkeypatch, title: str, card: AgentRequestDraft) -> int:
    result = AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[],
                            open_questions=[], proposed_follow_up_tasks=[], requests=[card], capability_gaps=[],
                            financial_model=None)
    passed = ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["c"], criteria_unmet=[], claim_checks=[],
                       counter_arguments=[], rework_instructions=None, contested=False)
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", lambda agent, purpose, prompt, model, **kw:
                        result if model is AgentResultOut else passed)
    with session_scope() as s2:
        t = Task(project_id=project_id, key=f"k{abs(hash(title)) % 10**6}", title=title, owner="research",
                 objective="o", acceptance_criteria=["c"], status="READY", plan_round=1)
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project_id, "task_id": tid})
    return tid


def _cards(project_id: int) -> list[str]:
    with session_scope() as s2:
        return [r.title for r in s2.scalars(select(AgentRequest).where(AgentRequest.project_id == project_id)
                                             .order_by(AgentRequest.id))]


def test_cards_wait_for_the_round_and_go_up_together(s, project, monkeypatch):
    project.plan_round = 1
    s.commit()
    first = _work(project.id, monkeypatch, "당근 첫 글", _card("당근 동네생활 첫 글 올리기"))
    second = _work(project.id, monkeypatch, "단톡방 글", _card("아파트 단톡방 글 올리기"))
    with session_scope() as s2:
        assert {s2.get(Task, first).status, s2.get(Task, second).status} == {"DONE"}
    assert _cards(project.id) == []

    assert company_graph.schedule({"project_id": project.id})["next"] == "ceo_review"

    assert _cards(project.id) == ["당근 동네생활 첫 글 올리기", "아파트 단톡방 글 올리기"]
    company_graph.schedule({"project_id": project.id})
    assert len(_cards(project.id)) == 2


def test_an_owner_answer_mid_round_lets_the_project_manager_withdraw_a_held_card(s, project, monkeypatch):
    from justshipit.contracts.planning import RealignOut, RequestWithdrawOut
    from justshipit.orchestration import realign

    project.plan_round, project.status = 1, "RUNNING"
    s.commit()
    _work(project.id, monkeypatch, "알바 공고", _card("당근알바 도우미 공고 올리기"))
    with session_scope() as s2:
        trigger = AgentRequest(project_id=project.id, from_agent="project_manager", type="QUESTION", title="채널", summary="s",
                               rationale="r", status="ANSWERED", policy_mode="approve",
                               response={"answer": "지금은 올리지 않음"})
        s2.add(trigger)
        s2.flush()
        trigger_id = trigger.id

    def project_manager(agent, purpose, prompt, model, **kw):
        listed = prompt.split("## Requests waiting for the owner", 1)[1]
        rid = int(listed.split("REQ-", 1)[1].split(" ", 1)[0])
        return RealignOut(assessment="올리지 않음", goal_interpretation="", out_of_scope_additions=[], cancel_task_ids=[],
                          revise_tasks=[], new_tasks=[], withdraw_requests=[RequestWithdrawOut(id=rid, reason="오너가 배제")])

    monkeypatch.setattr(realign, "run_agent", project_manager)
    realign.realign(project.id, trigger_id)

    with session_scope() as s2:
        (card,) = s2.scalars(select(AgentRequest).where(AgentRequest.title == "당근알바 도우미 공고 올리기")).all()
        assert card.status == "CANCELLED"
    company_graph.schedule({"project_id": project.id})
    assert _cards(project.id).count("당근알바 도우미 공고 올리기") == 1


def test_a_blocking_question_goes_up_at_once(s, project, monkeypatch):
    project.plan_round = 1
    s.commit()
    _work(project.id, monkeypatch, "가격 확인", _card("정기권 가격을 정해 주세요", holds=HoldKind.THIS_TASK,
                                                   type_=RequestType.QUESTION))
    assert _cards(project.id) == ["정기권 가격을 정해 주세요"]


# ---------------------------------------------------------------- the round's budget


def test_a_round_holds_a_fixed_number_of_planned_tasks_but_never_drops_the_owners(s, project):
    cap = rounds.max_tasks()
    made, warnings = tasks.create_from_drafts(s, project, [_draft(f"r{n}") for n in range(cap + 2)],
                                              origin="ceo_plan", plan_round=1)
    assert len(made) == cap and any("round 1 takes" in w for w in warnings)
    more, _ = tasks.create_from_drafts(s, project, [_draft("late")], origin="follow_up", plan_round=1)
    assert more == []
    next_round, _ = tasks.create_from_drafts(s, project, [_draft("next")], origin="follow_up", plan_round=2)
    assert len(next_round) == 1
    owners, _ = tasks.create_from_drafts(s, project, [_draft("mine")], origin="owner", plan_round=1)
    assert len(owners) == 1


def _ready(s, project, n: int, origin: str = "ceo_plan") -> list[int]:
    made = [Task(project_id=project.id, key=f"{origin}{i}", title=f"{origin} {i}", owner="research", objective="o",
                 acceptance_criteria=["c"], status="READY", origin=origin, plan_round=project.plan_round)
            for i in range(n)]
    s.add_all(made)
    s.flush()
    return [t.id for t in made]


def test_the_round_starts_no_task_past_its_envelope_and_says_why(s, project, korean_owner):
    project.budget_usd, project.plan_round = 40, 1
    spent = Task(project_id=project.id, key="spent", title="이미 한 조사", owner="research", objective="o",
                 acceptance_criteria=["c"], status="DONE", plan_round=1, spent_usd=16.5)
    s.add(spent)
    later = _ready(s, project, 2)
    mine = _ready(s, project, 1, origin="owner")
    s.commit()

    state = company_graph.schedule({"project_id": project.id})

    assert state["batch"] == mine
    company_graph.schedule({"project_id": project.id})   # the owner's task is still READY: nothing ran it
    with session_scope() as s2:
        rows = [s2.get(Task, i) for i in later]
        assert [t.status for t in rows] == ["READY", "READY"]
        s2.get(Task, mine[0]).status = "DONE"
    company_graph.schedule({"project_id": project.id})
    with session_scope() as s2:
        rows = [s2.get(Task, i) for i in later]
        assert [t.status for t in rows] == ["CANCELLED", "CANCELLED"]
        assert "분석 예산" in rows[0].error and "다음 회차" in rows[0].error


def test_a_later_round_gets_only_what_earlier_rounds_left_but_a_retried_task_still_runs(s, project):
    project.budget_usd, project.plan_round, project.spent_usd = 40, 2, 37
    planned = _ready(s, project, 2)
    retried = Task(project_id=project.id, key="retried", title="다시 시도한 조사", owner="research", objective="o",
                   acceptance_criteria=["c"], status="READY", origin="ceo_plan", plan_round=1, iteration=1)
    s.add(retried)
    s.commit()

    assert company_graph.schedule({"project_id": project.id})["batch"] == [retried.id]
    with session_scope() as s2:
        s2.get(Task, retried.id).status = "DONE"
    company_graph.schedule({"project_id": project.id})
    with session_scope() as s2:
        assert [s2.get(Task, i).status for i in planned] == ["CANCELLED", "CANCELLED"]


def test_the_envelope_leaves_room_for_the_round_it_was_planned_for(s, project):
    project.budget_usd, project.plan_round = 40, 1
    ids = _ready(s, project, 3)
    s.commit()
    assert company_graph.schedule({"project_id": project.id})["batch"] == ids


# ---------------------------------------------------------------- software as the first deliverable


def _plan(s, project, monkeypatch, build: BuildPlanOut | None) -> dict[str, int]:
    project.budget_usd = 40
    s.commit()
    out = ProjectManagerPlanOut(goal_interpretation="g", deliverables=["동작하는 웹앱"], success_criteria=["3곳 사용"],
                     out_of_scope=[], key_uncertainties=[], build=build,
                     tasks=[_draft("spec_rules", "소진일 계산 규칙"), _draft("market", "시장 규모")], requests=[])
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: out)
    monkeypatch.setattr(company_graph.memory, "recall_block", lambda *a, **k: "")
    company_graph.plan({"project_id": project.id})
    with session_scope() as s2:
        return {t.key: t.id for t in s2.scalars(select(Task).where(Task.project_id == project.id))}


BUILD = BuildPlanOut(title="원두 소진일 웹앱", problem="원두가 떨어지기 전에 주문하게 한다", why_software="매일 열어 보는 화면이다",
                     needs_task_keys=["spec_rules"])


def _dev_asks(project_id: int) -> list[Plan]:
    """What asking for the build left: the product plan it started, which the owner approves before any spec."""
    with session_scope() as s2:
        rows = list(s2.scalars(select(Plan).where(Plan.project_id == project_id)))
        assert s2.scalar(select(AgentRequest.id).where(AgentRequest.project_id == project_id,
                                                       AgentRequest.type == "DEVELOPMENT")) is None
        s2.expunge_all()
        return rows


def test_a_product_first_plan_keeps_developments_share_from_the_start(s, project, monkeypatch):
    _plan(s, project, monkeypatch, BUILD)
    with session_scope() as s2:
        assert budget.dev_hold(s2, project.id) == budget.dev_need_usd()


def test_a_research_first_plan_keeps_nothing_back(s, project, monkeypatch):
    _plan(s, project, monkeypatch, None)
    with session_scope() as s2:
        assert budget.dev_hold(s2, project.id) == 0


def test_development_is_asked_for_as_soon_as_the_spec_inputs_are_done(s, project, monkeypatch, korean_owner):
    ids = _plan(s, project, monkeypatch, BUILD)
    state = company_graph.schedule({"project_id": project.id})
    assert sorted(state["batch"]) == sorted(ids.values())
    assert _dev_asks(project.id) == []

    with session_scope() as s2:
        t = s2.get(Task, ids["spec_rules"])
        t.status = "DONE"
    company_graph.schedule({"project_id": project.id})

    (ask,) = _dev_asks(project.id)
    assert ask.status == "DRAFT" and ask.title == "원두 소진일 웹앱"
    with session_scope() as s2:
        assert s2.scalar(select(Job.id).where(Job.idempotency_key == f"write_plan:{ask.id}")) is not None
        assert budget.dev_hold(s2, project.id) == budget.dev_need_usd()
    company_graph.schedule({"project_id": project.id})
    assert len(_dev_asks(project.id)) == 1


def test_the_last_round_ending_asks_for_development_whatever_its_inputs_did(s, project, monkeypatch):
    ids = _plan(s, project, monkeypatch, BUILD)
    with session_scope() as s2:
        s2.get(company_graph.Project, project.id).plan_round = int(company_graph.limits()["max_plan_rounds"])
        for i in ids.values():
            s2.get(Task, i).status = "FAILED"
    assert company_graph.schedule({"project_id": project.id})["next"] == "ceo_review"
    assert len(_dev_asks(project.id)) == 1


def _fail_the_spec_input(project, ids: dict[str, int]) -> None:
    with session_scope() as s2:
        s2.get(Task, ids["spec_rules"]).status = "FAILED"
        s2.get(Task, ids["market"]).status = "DONE"
    assert company_graph.schedule({"project_id": project.id})["next"] == "ceo_review"


def test_a_failed_spec_input_holds_the_ask_and_the_task_redoing_it_stands_in(s, project, monkeypatch):
    """반품 예측: the round's end asked for the build while its rule spec (T-87) had failed. The spec was written
    without it, and three tasks then tried to fill it in after the engineer had read it."""
    ids = _plan(s, project, monkeypatch, BUILD)
    _fail_the_spec_input(project, ids)
    assert _dev_asks(project.id) == []

    with session_scope() as s2:
        (redo,), _ = tasks.create_from_drafts(
            s2, s2.get(company_graph.Project, project.id),
            [_draft("spec_rules_v2", "소진일 계산 규칙 다시").model_copy(update={"redoes": f"T-{ids['spec_rules']}"})],
            origin="follow_up", plan_round=2)
        redo_id = redo.id
    company_graph.schedule({"project_id": project.id})
    assert _dev_asks(project.id) == []
    with session_scope() as s2:
        s2.get(Task, redo_id).status = "DONE"
    company_graph.schedule({"project_id": project.id})
    (ask,) = _dev_asks(project.id)
    assert redo_id in ask.need["task_ids"]


def test_the_runs_last_review_asks_for_the_build_its_failed_input_held(s, project, monkeypatch):
    from justshipit.contracts.planning import ProjectManagerReviewAction, ProjectManagerReviewOut
    ids = _plan(s, project, monkeypatch, BUILD)
    _fail_the_spec_input(project, ids)
    prompts = []

    def project_manager(agent, purpose, prompt, model, **kw):
        prompts.append(prompt)
        return ProjectManagerReviewOut(assessment="a", unmet_success_criteria=[], action=ProjectManagerReviewAction.COMPLETE,
                            meeting_agenda=None, meeting_participants=[], follow_up_tasks=[], requests=[])
    monkeypatch.setattr(company_graph, "run_agent", project_manager)
    assert company_graph.ceo_review({"project_id": project.id})["next"] == "decide"
    assert f"Its spec waits for T-{ids['spec_rules']} (FAILED)" in prompts[0]
    assert len(_dev_asks(project.id)) == 1


def test_a_meeting_the_project_manager_calls_still_lets_a_follow_up_redo_the_failed_input(s, project, monkeypatch):
    from justshipit.contracts.planning import ProjectManagerReviewAction, ProjectManagerReviewOut
    ids = _plan(s, project, monkeypatch, BUILD)
    _fail_the_spec_input(project, ids)
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: ProjectManagerReviewOut(
        assessment="a", unmet_success_criteria=[], action=ProjectManagerReviewAction.MEETING, meeting_agenda="which rule?",
        meeting_participants=["strategy"], follow_up_tasks=[], requests=[]))
    assert company_graph.ceo_review({"project_id": project.id})["next"] == "meeting"
    assert _dev_asks(project.id) == []


def test_a_build_whose_inputs_name_no_task_waits_on_the_product_tasks(s, project, monkeypatch):
    out = ProjectManagerPlanOut(goal_interpretation="g", deliverables=["웹앱"], success_criteria=["c"], out_of_scope=[],
                     key_uncertainties=[], build=BUILD.model_copy(update={"needs_task_keys": ["T-1 화면 명세"]}),
                     tasks=[TaskDraft(key="screens", title="화면 명세", owner="product", objective="o",
                                      acceptance_criteria=["c"], depends_on=[]), _draft("market", "시장 규모")],
                     requests=[])
    project.budget_usd = 40
    s.commit()
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: out)
    monkeypatch.setattr(company_graph.memory, "recall_block", lambda *a, **k: "")
    company_graph.plan({"project_id": project.id})
    company_graph.schedule({"project_id": project.id})
    assert _dev_asks(project.id) == []
    with session_scope() as s2:
        p = s2.get(company_graph.Project, project.id)
        assert [s2.get(Task, i).key for i in p.plan["build"]["task_ids"]] == ["screens"]


def test_a_budget_that_cannot_cover_development_keeps_nothing_back(s, project, monkeypatch):
    _plan(s, project, monkeypatch, BUILD)
    with session_scope() as s2:
        p = s2.get(company_graph.Project, project.id)
        p.budget_usd = budget.dev_need_usd()
    with session_scope() as s2:
        p = s2.get(company_graph.Project, project.id)
        assert budget.dev_hold(s2, project.id) == 0 and rounds.analysis_usd(p) == p.budget_usd


def test_the_build_is_planned_first(s, project, monkeypatch):
    ids = _plan(s, project, monkeypatch, BUILD)
    with session_scope() as s2:
        p = s2.get(company_graph.Project, project.id)
        start, _ = rounds.admit(s2, p, sorted((s2.get(Task, i) for i in ids.values()), key=lambda t: -t.id))
        assert start[0].id == ids["spec_rules"]
