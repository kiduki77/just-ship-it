"""The build lane's owner text in each language: English has no Korean in it, and Korean passes the 번역체 lint.
The product being built speaks the language its spec records, whatever language the owner reads."""
import re

import pytest
from justshipit.contracts.handoff import Handoff, HandoffTarget, SpecDraftOut, language_tag
from justshipit.engineering import owner_trace, status, worker
from justshipit.i18n import catalog, msg
from justshipit.i18n.said import SaidError, words
from test_korean_style import smells_in

HANGUL = re.compile("[가-힣]")


def _reads_well(text: str | None, lang: str) -> None:
    assert text, text
    if lang == "en":
        assert not HANGUL.search(text), text
    else:
        assert HANGUL.search(text) and not smells_in(text), (text, smells_in(text))


@pytest.fixture
def s():
    """Rolled back: a failed or running job left behind would show in other tests' alerts."""
    from justshipit.db.session import session_scope
    with session_scope() as session:
        yield session
        session.rollback()


def test_no_english_build_message_holds_korean():
    for key, entry in catalog().items():
        if key.startswith("build."):
            en = entry["en"]
            for text in en.values() if isinstance(en, dict) else [en]:
                assert not HANGUL.search(text), (key, text)


# ------------------------------------------------------------------ the 개발 board

@pytest.mark.parametrize("lang", ["en", "ko"])
def test_each_board_stage_reads_in_the_owners_language(s, project, lang):
    from test_dev_board import _approved_development, _job, _spec, _write_spec_job

    def spec(tag):
        made = _spec(s, project)
        made.spec_key = f"BOARD-{tag}-{project.id}"
        s.flush()
        return made

    _write_spec_job(s, project, _approved_development(s, project), status="queued")
    _job(s, spec("run"), "IMPLEMENTING", steps=[{"at": "t", "status": "IMPLEMENTING", "summary": "raw",
                                                 "say": {"key": "build.step.build_start"}}])
    _job(s, spec("budget"), "FAILED", error=f"{msg('build.budget.stop', lang)} x")
    _job(s, spec("failed"), "FAILED", error=msg("build.cause.host", lang, cause="git add: ignored"))
    s.flush()

    stages = status.board(s, project.id, lang=lang)

    assert len(stages) == 4
    for st in stages:
        _reads_well(st["headline"], lang)
        if st.get("link"):
            _reads_well(st["link"]["label"], lang)
    assert any(st["headline"] == msg("build.budget.stop", lang) for st in stages)


def test_a_step_reads_in_the_language_it_is_shown_in():
    step = {"say": {"key": "build.step.accept_retry", "vars": {"attempt": 1, "max": 3}}}
    _reads_well(worker.step_line(step, "en"), "en")
    _reads_well(worker.step_line(step, "ko"), "ko")
    assert worker.step_line({"say": "기능을 만들기 시작했어요."}, "en") == "기능을 만들기 시작했어요.", \
        "a step from before lines were keys keeps its words"
    assert worker.step_line({"say": {"key": "build.step.gone"}}, "en") is None
    assert worker.step_line({"summary": "raw"}, "en") is None


# ------------------------------------------------------------------ errors and refusals

@pytest.mark.parametrize("lang", ["en", "ko"])
def test_what_stopped_a_job_reads_in_the_owners_language(lang):
    from justshipit.agents.executor import AgentFailed
    from justshipit.services.budget import BudgetExceeded

    for exc in (AgentFailed("quota"), BudgetExceeded("project budget 5.0 USD-eq would be exceeded"),
                RuntimeError("git add: ignored"), KeyError("HEAD"),
                SaidError("build.error.not_plain", path=".ship/spec"),
                worker.MergeRefused("build.merge.conflict", branch="ai/x", base="main", files="README.md", name="x")):
        _reads_well(worker._cause(exc, lang), lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("op", ["merge", "cherry_pick", "revert", "rebase", "rebase_apply", "bisect"])
def test_an_operation_in_progress_refuses_the_merge_in_the_owners_language(lang, op):
    refused = worker.MergeRefused(f"build.merge.busy.{op}", where="products/x", name="x", branch="ai/x")
    _reads_well(refused.words(lang), lang)
    assert "ai/x" in refused.words(lang)


def test_a_said_error_wrapped_by_another_is_still_said_in_the_owners_language():
    try:
        try:
            raise SaidError("build.error.not_plain", path=".git/info/exclude")
        except SaidError as inner:
            raise RuntimeError("prepare failed") from inner
    except RuntimeError as outer:
        assert words(outer, "ko") == msg("build.error.not_plain", "ko", path=".git/info/exclude")
    assert words(ValueError("plain"), "ko") == "plain"


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_sandbox_says_what_is_missing_in_the_owners_language(lang, monkeypatch, tmp_path):
    from justshipit.runtime import host_auth
    from justshipit.sandbox import docker

    for refusal in (docker.SandboxUnavailable("build.sandbox.docker_slow", s=30),
                    docker.SandboxUnavailable("build.sandbox.docker_down"),
                    docker.SandboxUnavailable("build.sandbox.image_missing", image="ship/engineer"),
                    docker.SandboxUnavailable("build.sandbox.egress_down", proxy="egress")):
        _reads_well(refusal.words(lang), lang)
    monkeypatch.setattr(host_auth, "_read", lambda: None)
    _reads_well(host_auth.status(lang), lang)


# ------------------------------------------------------------------ the spec card and the owner's trace

@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_spec_cards_trace_line_reads_in_the_owners_language(lang):
    from justshipit.contracts.handoff import OwnerTraceOut
    sources = [owner_trace.OwnerSource("l-1", "plan", "a"), owner_trace.OwnerSource("DEC-1", "decision", "b"),
               owner_trace.OwnerSource("DEC-2", "decision", "c")]
    rows = owner_trace.settle(sources, [
        OwnerTraceOut(source="DEC-1", says="b", disposition="dropped", requirement_ids=[], reason="later"),
        OwnerTraceOut(source="DEC-2", says="c", disposition="included", requirement_ids=["REQ-1"], reason="")],
        {"REQ-1"}, lang)
    _reads_well(owner_trace.card_line(rows, lang), lang)
    included = owner_trace.settle(sources[2:], [OwnerTraceOut(
        source="DEC-2", says="c", disposition="included", requirement_ids=["REQ-1"], reason="")], {"REQ-1"}, lang)
    _reads_well(owner_trace.card_line(included, lang), lang)


# ------------------------------------------------------------------ the product's own language

def test_a_product_language_is_a_tag_or_nothing():
    """It reaches a shell line (chromium --lang), so only a language tag passes."""
    assert language_tag("ko-KR") == "ko-KR" and language_tag(" en ") == "en"
    for bad in ("en; rm -rf /", "$(id)", "ko KR", "", None, "a" * 40):
        assert language_tag(bad) is None
    assert HandoffTarget(repository="demo", language="en-US; reboot").language is None


def test_the_product_language_the_spec_records_reaches_the_engineer_and_the_capture():
    h = Handoff(spec_id="SPEC-L", project="Test", title="t", source={"path": "specs/l", "commit_sha": "abc"},
                target={"repository": "demo", "language": "ko-KR"})
    assert "`ko-KR`" in worker._product_language(h)
    assert "--lang=ko-KR " in worker._capture("ko-KR")
    assert "--lang=en " in worker._capture(None)
    assert worker._product_language(h.model_copy(update={"target": HandoffTarget(repository="demo")})) == ""
    out = SpecDraftOut.model_fields["product_language"]
    assert out.default is None


def test_the_prd_names_the_product_language_first():
    from justshipit.engineering import specs
    assert specs._language_line("en-US").startswith("Product language: en-US")
    assert specs._language_line(None) == ""
