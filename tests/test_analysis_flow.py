"""Work node and analysis loop with stubbed model calls (no subscription usage)."""
import pytest

from justshipit.agents.independence import Served
from justshipit.analysis import runner
from justshipit.contracts.analysis import AnalysisAction, AnalysisInputOut, AnalysisStepOut
from justshipit.contracts.work import AgentResultOut, ClaimCheckOut, EvidenceKind, EvidenceOut, ReviewOut, ReviewVerdict
from justshipit.db.models import Evidence, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from sandbox_kit import needs_image

URL = "local:pop.csv"


def result(quote: str) -> AgentResultOut:
    return AgentResultOut(
        summary="전남 비율 계산", findings_markdown="전남 65세 이상 비율 [E1]",
        evidence=[EvidenceOut(claim="전남 65세 이상 비율", kind=EvidenceKind.SOURCED_CLAIM, source_url=URL,
                              source_title="인구", quote=quote, published_at="2025", confidence=0.9)],
        assumptions=[], risks=[], open_questions=[], proposed_follow_up_tasks=[], requests=[], capability_gaps=[],
        financial_model=None)


def review(prompts: list[str]) -> ReviewOut:
    return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["c"], criteria_unmet=[],
                     claim_checks=[ClaimCheckOut(evidence_index=0, status="supported", note="ok",
                                                 counter_source_url=None)],
                     counter_arguments=[], rework_instructions=None, contested=False)


def _task(project_id: int) -> int:
    with session_scope() as s:
        t = Task(project_id=project_id, key="pop", title="고령 비율", owner="analyst", objective="계산",
                 acceptance_criteria=["c"], status="READY", max_iterations=1)
        s.add(t)
        s.flush()
        return t.id


@needs_image
def test_analysis_task_end_to_end(s, project, monkeypatch):
    from justshipit.settings import settings
    s.commit()  # the work node uses its own sessions
    slug = project.slug
    data = settings().var_dir / "data" / slug
    data.mkdir(parents=True, exist_ok=True)
    (data / "pop.csv").write_text("region,total,old\n전남,1800000,480000\n서울,9400000,1700000\n", encoding="utf-8")
    script = ("import json, pandas as pd\n"
              "d = pd.read_csv('inputs/pop.csv')\n"
              "r = d.set_index('region')\n"
              "print(d.head())\n"
              "json.dump({'jeonnam_old_share': round(r.loc['전남','old']/r.loc['전남','total'], 4)},"
              " open('out/results.json','w'), ensure_ascii=False)\n")
    calls: list[tuple[str, str]] = []
    prompts: list[str] = []

    def fake_run_agent(agent, purpose, prompt, model, **kw):
        calls.append((agent, purpose))
        prompts.append(prompt)
        if model is AnalysisStepOut:
            first = sum(1 for a, p in calls if p == "analysis") == 1
            return AnalysisStepOut(reasoning="r", action=AnalysisAction.RUN if first else AnalysisAction.FINISH,
                                   inputs=[AnalysisInputOut(url=URL, filename="pop.csv")] if first else [],
                                   script=script if first else "")
        if model is AgentResultOut:
            return result("0.2667")
        return review(prompts)

    monkeypatch.setattr(runner, "run_agent", fake_run_agent)
    monkeypatch.setattr(company_graph, "run_agent", fake_run_agent)
    served = iter([Served("claude", "sonnet"), Served("claude", "opus")])
    monkeypatch.setattr(company_graph, "last_served", lambda: next(served))
    tid = _task(project.id)
    company_graph.work({"project_id": project.id, "task_id": tid})

    assert [p for _, p in calls] == ["analysis", "analysis", "work", "review"]
    assert "jeonnam_old_share" in prompts[1] and "0.2667" in prompts[1]   # observation fed back
    assert "data analyst" in prompts[3]                                  # analyst-specific review guidance
    with session_scope() as s2:
        t = s2.get(Task, tid)
        assert t.status == "DONE"
        a = t.result["analysis"]
        assert a["reproduced"] is True and a["inputs"][0]["url"] == URL
        e = s2.get(Evidence, t.result["evidence_ids"][0])
        # reproduced from data + independent different-model support → verified
        assert e.kind == "verified_fact" and e.verification_status == "supported"
        assert "Data analysis record" in t.result["findings_markdown"]


def test_plain_task_review_prompt_points_to_analyst(s, project, monkeypatch):
    s.commit()
    prompts: list[str] = []

    def fake_run_agent(agent, purpose, prompt, model, **kw):
        prompts.append(prompt)
        return result("x") if model is AgentResultOut else review(prompts)

    monkeypatch.setattr(company_graph, "run_agent", fake_run_agent)
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    with session_scope() as s2:
        t = Task(project_id=project.id, key="r", title="t", owner="research", objective="o",
                 acceptance_criteria=["c", "d"], status="READY", max_iterations=1)  # checked: two criteria
        s2.add(t)
        s2.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    assert "(tool limit)" in prompts[-1] and "analyst" in prompts[-1]
