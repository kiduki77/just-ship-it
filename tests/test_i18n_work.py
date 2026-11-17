"""Projects and tasks speak the owner's language: the owner's controls, the work the server declines to plan, and the
documents and cards a run writes. English carries no Korean, and the Korean reads as written in Korean."""
import re

import pytest
from justshipit.analysis.runner import AnalysisRecord, appendix, classify_evidence
from justshipit.contracts.requests import HumanDecision
from justshipit.contracts.work import TaskDraft
from justshipit.db.models import ActivityEvent, Decision
from justshipit.db.session import session_scope
from justshipit.i18n import catalog, msg
from justshipit.orchestration import company_graph
from justshipit.services import control, plan_screen
from justshipit.services.verdicts import infer_verdict
from sqlalchemy import select
from test_korean_style import smells_in

HANGUL = re.compile(r"[가-힣]")


def _reads_in(text: str, lang: str) -> None:
    if lang == "en":
        assert not HANGUL.search(text), text
    else:
        assert HANGUL.search(text) and not smells_in(text), (text, smells_in(text))


def test_every_work_message_is_english_in_english():
    for key, entry in catalog().items():
        if key.startswith("work."):
            for text in (entry["en"].values() if isinstance(entry["en"], dict) else [entry["en"]]):
                assert not HANGUL.search(text.replace("[내 경험: 직접 쓰기]", "")), key


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_refused_owner_control_says_why_in_the_owners_language(s, project, lang):
    project.status = "DONE"
    s.commit()
    with session_scope() as x, pytest.raises(control.ControlError) as refused:
        control.pause(x, project.id, lang=lang)
    _reads_in(str(refused.value), lang)
    with session_scope() as x, pytest.raises(control.ControlError) as refused:
        control.add_directive(x, project.id, "  ", lang=lang)
    _reads_in(str(refused.value), lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_an_owner_control_logs_its_activity_in_the_owners_language(s, project, lang):
    s.commit()
    with session_scope() as x:
        control.rename(x, project.id, "Renamed", lang=lang)
    row = s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "renamed",
                                                ActivityEvent.project_id == project.id)).one()
    _reads_in(row.summary, lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_options_a_run_offers_say_the_decision_they_are(lang):
    """An option's own words decide what approving it does (`services/verdicts.py`), so each label the server writes
    must read as the decision it stands for in its language."""
    expect = {"write_spec": HumanDecision.APPROVE, "approve": HumanDecision.APPROVE, "hold": HumanDecision.DEFER,
              "decline": HumanDecision.REJECT}
    for key, verdict in expect.items():
        assert infer_verdict(msg(f"work.option.{key}", lang), question=False) == verdict, key


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_ruled_out_task_and_a_refused_rule_say_why_in_the_owners_language(s, project, lang):
    rule = plan_screen.Rule(2, project.id, "market size count", ("market", "size"))
    why = plan_screen.task_problem(s, project, TaskDraft(key="k", title="Market size refresh", owner="research",
                                                         objective="o", acceptance_criteria=[], depends_on=[]),
                                   [rule], lang=lang)
    assert "#2 (P-" in why
    _reads_in(why, lang)
    _, refused = plan_screen.screen_rules(s, project, [{"label": "x", "keywords": ["a"]}], lang=lang)
    _reads_in(refused[0]["why"], lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_decision_record_reads_in_the_owners_language(lang):
    d = Decision(id=7, title="T", decision="adopt", status="EFFECTIVE", rationale="r", supporting_evidence_ids=[],
                 dissenting_evidence_ids=[], dissent_summary="", open_questions=[], action_task_ids=[],
                 approval_required=False)
    text = company_graph._decision_md(d, lang)
    assert "DEC-7" in text
    _reads_in(text, lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_analysis_record_reads_in_the_owners_language(tmp_path, lang):
    rec = AnalysisRecord(workdir=tmp_path)
    rec.manifest = [{"filename": "a.csv", "url": "https://data.example/a.csv", "sha256": "0" * 64, "bytes": 10}]
    rec.final_script, rec.final_results, rec.reproduced, rec.steps = "print(1)", '{"n": 5}', True, 2
    _, note = classify_evidence(rec, "https://data.example/a.csv", "5", lang=lang)
    _reads_in(note, lang)
    _reads_in(re.sub(r"```.*?```", "", appendix(rec, "p", lang=lang), flags=re.S), lang)
