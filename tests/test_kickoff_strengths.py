"""What the owner is good at is the kickoff chat's first question, no longer a field on the New business form (owner
decision Q24, 2026-11-09). The server asks it, not the CEO, so a model that drifts can't skip it: an owner who never
answered gets it as turn 0, the CEO's first turn waits for the answer, and the answer goes where Settings > About you
keeps it. An owner who already answered is not asked again, and a skip leaves the question for the next business."""
import uuid

import pytest
from justshipit.db.models import AppSetting, Job
from justshipit.db.session import session_scope
from justshipit.engineering import current_state
from justshipit.i18n import msg
from justshipit.services import app_settings, existing_product, kickoff
from sqlalchemy import delete, select
from test_existing_product import owner_repo, products, report  # noqa: F401 (products is a fixture)
from test_kickoff import _settled_jobs, api, project_manager, kickoff_jobs, open_session, out, reply, view  # noqa: F401


@pytest.fixture
def never_asked():
    """Settings > About you never answered, before and after the test."""
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "owner"))
    wipe()
    yield
    wipe()


def opening(v: dict) -> dict:
    [turn] = [t for t in v["turns"] if t.get("asks") == "strengths"]
    return turn


def test_a_new_owner_is_asked_what_they_are_good_at_before_the_project_manager_speaks(api, never_asked):
    sid = open_session(api)

    v = view(api, sid)
    turn = opening(v)
    assert v["session"]["status"] == "WAITING_OWNER" and v["open_turn"] == 0 == turn["n"]
    assert turn["ceo_text"] == msg("kickoff.strengths.question", "en") and turn["chips"] == []
    assert v["session"]["turns_used"] == 0 and v["session"]["turns_left"] == 20, "it burns none of the CEO's turns"
    assert kickoff_jobs(sid) == [], "the CEO's first question waits for the answer"


def test_the_answer_is_saved_as_about_you_and_the_project_manager_starts_with_it(api, project_manager, never_asked):
    sid = open_session(api)

    v = reply(api, sid, strengths=["marketing", "software"])

    assert app_settings.reader()[1] == ["software", "marketing"], "kept where Settings > About you keeps it"
    assert opening(v)["owner_choice"] == "software,marketing" and v["session"]["status"] == "OPEN"
    [job] = kickoff_jobs(sid)
    assert job.payload == {"session_id": sid, "n": 1, "close": False}
    project_manager.says(out("누가 제일 필요로 할까요?"))
    kickoff.run_turn(sid, 1)
    v = view(api, sid)
    assert v["open_turn"] == 1 and v["session"]["turns_used"] == 1
    assert "(no conversation yet)" in project_manager.prompts[0], "the strengths question is no plan conversation"


def test_none_of_these_yet_is_an_answer(api, never_asked):
    sid = open_session(api)

    v = reply(api, sid, strengths=[])

    assert app_settings.reader()[1] == [] and opening(v)["owner_choice"] == "none"
    assert len(kickoff_jobs(sid)) == 1


def test_a_skip_starts_the_chat_and_leaves_the_question_for_the_next_business(api, never_asked):
    sid = open_session(api)

    v = reply(api, sid, action="unknown")

    assert app_settings.reader()[1] is None and opening(v)["owner_choice"] == "unknown"
    assert len(kickoff_jobs(sid)) == 1
    assert opening(view(api, open_session(api, name="다음 사업")))["answered_at"] is None


def test_an_answer_with_nothing_picked_is_refused_in_plain_words(api, never_asked):
    sid = open_session(api)

    r = api.post(f"/api/kickoff/{sid}/answer", json={"action": "answer", "text": "다 잘해요"})
    bad = api.post(f"/api/kickoff/{sid}/answer", json={"action": "answer", "strengths": ["accounting"]})

    assert r.status_code == 409 and r.json()["detail"] == msg("kickoff.strengths.empty", "en")
    assert bad.status_code == 422 and "accounting" not in bad.text
    assert app_settings.reader()[1] is None and kickoff_jobs(sid) == []


def test_later_pauses_on_the_question_and_resume_comes_back_to_it(api, never_asked):
    sid = open_session(api)

    reply(api, sid, action="later")
    v = api.post(f"/api/kickoff/{sid}/resume").json()

    assert v["session"]["status"] == "WAITING_OWNER" and v["open_turn"] == 0 and kickoff_jobs(sid) == []


def test_an_owner_who_already_answered_is_not_asked_again(api, never_asked):
    with session_scope() as s:
        app_settings.save(s, "owner", {"strengths": ["design"]}, actor="owner")

    sid = open_session(api)

    v = view(api, sid)
    assert v["turns"] == [] and v["session"]["status"] == "OPEN" and len(kickoff_jobs(sid)) == 1
    assert app_settings.reader()[1] == ["design"], "a business opened before this keeps its strengths"


def test_an_imported_product_still_waits_for_its_report_after_the_answer(tmp_path, products, monkeypatch,  # noqa: F811
                                                                         never_asked):
    repo = owner_repo(tmp_path / "owner", name=f"rooms-{uuid.uuid4().hex[:6]}")
    product = existing_product.clone(existing_product.parse_source(str(repo), products), products)
    with session_scope() as s:
        v = kickoff.open_session(s, name="Rooms", one_liner="", product=product, lang="en")
    bid, sid = v["session"]["business_id"], v["session"]["id"]
    with session_scope() as s:
        for job in s.scalars(select(Job).where(Job.kind == "product_report", Job.status == "queued",
                                               Job.payload["business_id"].as_integer() != bid)):
            job.status = "done"

    with session_scope() as s:
        kickoff.answer(s, sid, action="answer", strengths=["software"], lang="en")
    assert kickoff_jobs(sid) == [], "the code is still being read"

    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    assert [j.payload["n"] for j in kickoff_jobs(sid)] == [1], "the report starts the chat once"


def test_an_imported_product_read_before_the_answer_starts_the_chat_on_the_answer(tmp_path, products,  # noqa: F811
                                                                                monkeypatch, never_asked):
    repo = owner_repo(tmp_path / "owner", name=f"rooms-{uuid.uuid4().hex[:6]}")
    product = existing_product.clone(existing_product.parse_source(str(repo), products), products)
    with session_scope() as s:
        v = kickoff.open_session(s, name="Rooms", one_liner="", product=product, lang="en")
    bid, sid = v["session"]["business_id"], v["session"]["id"]
    monkeypatch.setattr(current_state, "run_agent", lambda *a, **k: report())
    current_state.run(bid)
    assert kickoff_jobs(sid) == [], "the strengths question is still open"

    with session_scope() as s:
        kickoff.answer(s, sid, action="unknown", lang="en")
    assert [j.payload["n"] for j in kickoff_jobs(sid)] == [1]
