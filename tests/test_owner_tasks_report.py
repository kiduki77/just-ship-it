"""What the company may say it did: a connector-less action counts as done only when its owner task is DONE. The
section lands in the owner's report, so it is in the owner's language: Korean here."""
import pytest
from justshipit.contracts.planning import FinalReportOut
from justshipit.db.models import AgentRequest, Artifact
from justshipit.orchestration import company_graph
from justshipit.orchestration import context as ctx
from justshipit.services import owner_tasks
from lang_kit import owner_speaks
from owner_task_kit import as_stored_before_the_rules_check, open_task, tasks_of


@pytest.fixture(autouse=True)
def korean():
    with owner_speaks("ko"):
        yield


def test_a_prompt_says_an_open_task_is_not_done_and_quotes_a_done_one(s, project):
    open_one = as_stored_before_the_rules_check(open_task(s, project, title="Reddit 게시"), "reddit")
    done_one = open_task(s, project, title="Show HN 게시")
    owner_tasks.done(s, done_one.id, link="https://news.ycombinator.com/item?id=5", numbers={"points": 9},
                     note="좋은 반응")
    s.commit()

    text = ctx.project_brief(s, project)
    assert "## 오너가 직접 하는 일의 실제 상태" in text
    assert f"[OT-{open_one.id} · 열림] Reddit 게시" in text and "아직 하지 않았어요" in text
    assert f"[OT-{done_one.id} · 완료] Show HN 게시" in text
    assert "링크 https://news.ycombinator.com/item?id=5" in text and "points=9" in text and "메모 좋은 반응" in text


def test_a_skipped_or_reopened_task_is_not_reported_as_done(s, project):
    skipped = open_task(s, project, title="뉴스레터 투고")
    owner_tasks.skip(s, skipped.id, note="규정 확인 전")
    back = as_stored_before_the_rules_check(open_task(s, project, title="X 게시"), "x")
    owner_tasks.done(s, back.id, link="https://x.com/me/status/1")
    owner_tasks.reopen(s, back.id)
    s.commit()

    text = owner_tasks.status_section(s, project.id)
    assert f"[OT-{skipped.id} · 건너뜀] 뉴스레터 투고" in text and "규정 확인 전" in text
    assert f"[OT-{back.id} · 열림] X 게시" in text and "x.com/me/status/1" not in text


def test_an_old_approval_with_no_task_still_shows_and_says_nothing_confirms_it(s, project):
    old = AgentRequest(project_id=project.id, from_agent="marketing", type="APPROVAL", title="랜딩 공개(예전 승인)",
                       summary="s", rationale="r", action_kind="external.landing_publish", policy_mode="approve",
                       status="APPROVED")
    s.add(old)
    s.commit()
    assert tasks_of(old.id) == []

    text = ctx.project_brief(s, project)
    assert f"[REQ-{old.id} · 추적 기록 없음] 랜딩 공개(예전 승인)" in text and "했는지는 몰라요" in text


def test_a_project_with_nothing_to_carry_out_adds_nothing_to_the_prompt(s, project):
    assert owner_tasks.status_section(s, project.id) == ""
    assert "오너가 직접 하는 일" not in ctx.project_brief(s, project)


def test_the_final_report_ends_with_the_true_state_whatever_the_model_wrote(s, project, monkeypatch, korean_owner):
    task = open_task(s, project, title="Show HN 게시")
    seen: dict = {}

    def fake(agent, purpose, prompt, model, **kw):
        seen["prompt"] = prompt
        return FinalReportOut(title="결과", executive_summary="요약", recommendation="GO", report_markdown="Show HN에 올렸다.",
                              validation_plan=["인터뷰"], development_needs=[], kpis=[])

    def keep(sess, p, kind, title, rel, content):
        seen["report"] = content
        art = Artifact(project_id=p.id, kind=kind, title=title, path=rel, commit_sha="x")
        sess.add(art)
        sess.flush()
        return art

    monkeypatch.setattr(company_graph, "run_agent", fake)
    monkeypatch.setattr(company_graph, "_write_artifact", keep)
    monkeypatch.setattr(company_graph.memory, "index_project", lambda *a, **k: None)
    company_graph.report({"project_id": project.id})

    assert f"[OT-{task.id} · 열림] Show HN 게시" in seen["prompt"]
    assert "say done only for what the section on the real status" in seen["prompt"]
    body = seen["report"]
    assert body.index("Show HN에 올렸다.") < body.index("## 오너가 직접 하는 일의 실제 상태") < body.index("\n## 근거\n")
    assert f"[OT-{task.id} · 열림] Show HN 게시" in body
