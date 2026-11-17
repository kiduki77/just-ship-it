"""The customer description's routes: the view the console renders whole, the owner's four edits, and undo. An edit
files no decision card, writes an activity row, and refuses a line that changed since the owner read it, in the owner's
language."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from justshipit.api.app import app
from justshipit.db.models import ActivityEvent, AgentRequest, CustomerProfileAnswer, Job
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import roadmap
from justshipit.services.profile import store, vocab
from justshipit.services.profile.store import NewValue
from sqlalchemy import delete, func, select
from test_profile_infer import approve

LINES = ("buyer", "reach", "has")


@pytest.fixture(autouse=True)
def _settled_jobs():
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(Job).where(Job.kind == "profile_infer"))
    wipe()
    yield
    wipe()


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


@pytest.fixture
def biz(s):
    b = roadmap.create_business(s, name="Profile api", one_liner="A loyalty card for bakeries")
    s.commit()
    return b.id


def guess(s, bid, line, *codes, brief_id=None, source="backfill:1"):
    store.set_line(s, bid, line, [NewValue(c, None, "guess", source) for c in codes], cause="backfill",
                   written_by="project_manager", brief_id=brief_id, if_live=None, lang="en")
    s.commit()


def get(api, bid):
    r = api.get(f"/api/businesses/{bid}/profile")
    assert r.status_code == 200, r.text
    return r.json()


def line_of(view, name):
    return next(x for x in view["lines"] if x["line"] == name)


def post(api, bid, line, **body):
    return api.post(f"/api/businesses/{bid}/profile/{line}", json=body)


def cards():
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(AgentRequest))


def events(bid):
    with session_scope() as s:
        return list(s.scalars(select(ActivityEvent).where(ActivityEvent.verb == "profile",
                                                          ActivityEvent.business_id == bid).order_by(ActivityEvent.id)))


def test_a_business_nobody_wrote_a_description_for_says_so_and_offers_the_question_and_the_choices(api, biz):
    v = get(api, biz)

    assert (v["state"], v["reading"], v["backfilled"], v["business_id"]) == ("not_written", False, False, biz)
    assert v["question"] == msg("kickoff.profile.question", "en")
    assert [x["line"] for x in v["lines"]] == list(LINES)
    assert line_of(v, "buyer") == {"line": "buyer", "label": "Who buys", "sentence": "", "values": [],
                                   "weakest": "unknown", "weakest_label": "Unknown", "known": False, "asked": False,
                                   "guessed": False, "confirmable": False, "stale": False, "stale_since": None, "live_ids": [],
                                   "uses": []}
    assert {line: [c["code"] for c in cs] for line, cs in v["choices"].items()} == {
        line: list(vocab.live_codes(line)) for line in LINES}
    assert v["choices"]["buyer"][0] == {"code": "consumers", "label": "People buying for themselves"}


def test_a_written_description_comes_with_every_word_the_console_shows(s, api, biz):
    guess(s, biz, "buyer", "small_business", "consumers")
    store.set_line(s, biz, "has", [NewValue(None, "A cousin who runs a cafe", "said", "answer:1", "A cousin")],
                   cause="kickoff_answer", written_by="project_manager", brief_id=None, if_live=None, lang="en")
    s.commit()

    v = get(api, biz)

    buyer = line_of(v, "buyer")
    assert v["state"] == "ready" and v["backfilled"] is False, "the has line came from the owner's answer"
    assert buyer["sentence"] == "Who buys: Small businesses and shops; People buying for themselves"
    assert buyer["values"] == [{"code": "small_business", "label": "Small businesses and shops", "sentence": None,
                                "provenance": "guess", "provenance_label": "Guess"},
                               {"code": "consumers", "label": "People buying for themselves", "sentence": None,
                                "provenance": "guess", "provenance_label": "Guess"}]
    assert (buyer["weakest"], buyer["weakest_label"], buyer["known"], buyer["asked"], buyer["guessed"]) == (
        "guess", "Guess", True, True, True)
    assert buyer["uses"] == ["Small businesses and shops", "People buying for themselves"]
    assert buyer["live_ids"] == sorted(x.row_id for x in store.live_rows(s, biz) if x.line == "buyer")
    has = line_of(v, "has")
    assert has["values"][0]["sentence"] == "A cousin who runs a cafe" and has["values"][0]["label"] is None
    assert has["weakest"] == "said" and has["uses"] == [] and has["guessed"] is False
    reach = line_of(v, "reach")
    assert (reach["asked"], reach["known"], reach["weakest"]) == (False, False, "unknown")


def test_the_view_is_in_the_owners_language(s, api, biz, korean_owner):
    guess(s, biz, "buyer", "developers")

    v = get(api, biz)

    assert line_of(v, "buyer")["label"] == "누가 사나" and line_of(v, "buyer")["weakest_label"] == "짐작"
    assert line_of(v, "buyer")["sentence"] == "누가 사나: 개발자" and v["question"] == msg("kickoff.profile.question", "ko")


def test_backfilled_is_true_only_while_every_value_is_the_companys_reading_and_the_owner_never_acted(s, api, biz):
    guess(s, biz, "buyer", "consumers")
    assert get(api, biz)["backfilled"] is True
    store.set_line(s, biz, "reach", [NewValue("search", None, "guess", "answer:4")], cause="kickoff_answer",
                   written_by="project_manager", brief_id=None, if_live=None, lang="en")
    s.commit()
    assert get(api, biz)["backfilled"] is False, "a value from the owner's answer is not a backfill"
    store.set_line(s, biz, "reach", [NewValue("local", None, "guess", "plan:4")], cause="plan", written_by="project_manager",
                   brief_id=None, if_live=None, lang="en")
    s.commit()
    assert get(api, biz)["backfilled"] is True
    live = get(api, biz)
    assert post(api, biz, "buyer", action="right", if_live=line_of(live, "buyer")["live_ids"]).status_code == 200
    assert get(api, biz)["backfilled"] is False


def test_right_confirms_the_guesses_files_no_card_and_records_the_change(s, api, biz):
    guess(s, biz, "buyer", "small_business")
    before_cards = cards()
    ids = line_of(get(api, biz), "buyer")["live_ids"]

    r = post(api, biz, "buyer", action="right", if_live=ids)

    assert r.status_code == 200, r.text
    body = r.json()
    assert line_of(body["profile"], "buyer")["weakest"] == "confirmed" and line_of(body["profile"], "buyer")["guessed"] is False
    assert body["event_id"] == events(biz)[-1].id and events(biz)[-1].actor == "owner"
    assert events(biz)[-1].data["cause"] == "owner_right" and events(biz)[-1].summary.startswith("You confirmed")
    assert cards() == before_cards, "an edit that can be undone files no decision card"
    again = post(api, biz, "buyer", action="right", if_live=line_of(body["profile"], "buyer")["live_ids"])
    assert again.status_code == 200 and again.json()["event_id"] is None, "nothing left to confirm"
    assert len(events(biz)) == 2


def test_right_keeps_what_the_owner_typed_and_confirms_the_rest(s, api, biz):
    store.set_line(s, biz, "buyer", [NewValue(None, "my aunt's friends", "said", "console", "my aunt's friends"),
                                     NewValue("companies", None, "guess", "plan:1")],
                   cause="owner_change", written_by="owner", brief_id=None, if_live=None, lang="en")
    s.commit()
    ids = line_of(get(api, biz), "buyer")["live_ids"]

    v = post(api, biz, "buyer", action="right", if_live=ids).json()["profile"]

    assert {(x["code"], x["provenance"]) for x in line_of(v, "buyer")["values"]} == {(None, "said"),
                                                                                       ("companies", "confirmed")}


def test_not_right_writes_the_unknown_marker_and_the_old_values_become_rejected(s, api, biz):
    guess(s, biz, "reach", "search")
    ids = line_of(get(api, biz), "reach")["live_ids"]

    r = post(api, biz, "reach", action="not_right", if_live=ids)

    assert r.status_code == 200
    reach = line_of(r.json()["profile"], "reach")
    assert (reach["known"], reach["asked"], reach["weakest"], reach["values"]) == (False, True, "unknown", [])
    assert events(biz)[-1].data["cause"] == "owner_not_right"
    with session_scope() as t:
        assert [(v.line, v.code) for v in store.rejected(t, biz)] == [("reach", "search")]
    assert post(api, biz, "reach", action="not_right", if_live=reach["live_ids"]).json()["event_id"] is None


def test_change_writes_picked_codes_as_confirmed_and_a_typed_sentence_as_said_with_itself_as_the_quote(s, api, biz):
    guess(s, biz, "buyer", "consumers")
    ids = line_of(get(api, biz), "buyer")["live_ids"]

    r = post(api, biz, "buyer", action="change", if_live=ids, codes=["professionals", "small_business"],
             sentence="  Dentists in Seoul ")

    assert r.status_code == 200, r.text
    assert {(x["code"], x["sentence"]): x["provenance"] for x in line_of(r.json()["profile"], "buyer")["values"]} == {
        ("professionals", None): "confirmed", ("small_business", None): "confirmed",
        (None, "Dentists in Seoul"): "said"}
    with session_scope() as t:
        said = next(v for v in store.live_rows(t, biz) if v.sentence)
        assert said.quote == "Dentists in Seoul" and said.written_by == "owner" and said.source == "console"
    assert events(biz)[-1].data["cause"] == "owner_change"


def test_change_refuses_nothing_picked_a_code_off_the_list_and_a_line_that_does_not_exist(s, api, biz):
    nothing = post(api, biz, "buyer", action="change", if_live=[], codes=[], sentence="  ")
    assert nothing.status_code == 422 and nothing.json()["detail"] == msg("profile.error.nothing_picked", "en")
    off = post(api, biz, "buyer", action="change", if_live=[], codes=["local"])
    assert off.status_code == 422 and off.json()["detail"] == msg("profile.error.bad_code", "en")
    assert post(api, biz, "colour", action="right").json()["detail"] == msg("profile.error.bad_line", "en")
    assert post(api, biz, "buyer", action="delete").json()["detail"] == msg("profile.error.bad_action", "en")
    assert events(biz) == []


def test_answer_keeps_the_words_and_queues_one_reading_and_nothing_is_written_yet(s, api, biz):
    r = post(api, biz, "buyer", action="answer", text="  Small bakeries near me, in person  ")

    assert r.status_code == 200 and r.json()["event_id"] is None
    assert r.json()["profile"]["reading"] is True and r.json()["profile"]["state"] == "not_written"
    with session_scope() as t:
        row = t.scalar(select(CustomerProfileAnswer).where(CustomerProfileAnswer.business_id == biz))
        assert (row.text, row.asked_in, row.kickoff_session_id) == ("Small bakeries near me, in person", "business", None)
        job = t.scalar(select(Job).where(Job.kind == "profile_infer", Job.business_id == biz))
        assert job.idempotency_key == f"profile:{biz}:answer:{row.id}" and job.payload["trigger"] == "kickoff_answer"
    blank = post(api, biz, "buyer", action="answer", text="   ")
    assert blank.status_code == 422 and blank.json()["detail"] == msg("profile.error.no_answer", "en")
    with session_scope() as t:
        assert t.scalar(select(func.count()).select_from(CustomerProfileAnswer)
                        .where(CustomerProfileAnswer.business_id == biz)) == 1


def test_an_edit_from_a_stale_read_is_refused_with_409_in_the_owners_language_and_writes_nothing(s, api, biz,
                                                                                                  korean_owner):
    guess(s, biz, "buyer", "consumers")
    old = line_of(get(api, biz), "buyer")["live_ids"]
    store.set_line(s, biz, "buyer", [NewValue("developers", None, "confirmed", "console")], cause="owner_change",
                   written_by="owner", brief_id=None, if_live=None, lang="en")
    s.commit()
    n = len(events(biz))

    r = post(api, biz, "buyer", action="change", if_live=old, codes=["companies"])

    assert r.status_code == 409 and r.json()["detail"] == msg("profile.error.stale_write", "ko")
    assert "바뀌었어요" in r.json()["detail"]
    assert [x["code"] for x in line_of(get(api, biz), "buyer")["values"]] == ["developers"] and len(events(biz)) == n


def test_the_refusal_is_english_by_default(s, api, biz):
    guess(s, biz, "buyer", "consumers")
    old = line_of(get(api, biz), "buyer")["live_ids"]
    post(api, biz, "buyer", action="not_right", if_live=old)

    r = post(api, biz, "buyer", action="right", if_live=old)

    assert r.status_code == 409 and r.json()["detail"] == msg("profile.error.stale_write", "en")
    assert "changed in the meantime" in r.json()["detail"]


def test_undo_puts_the_line_back_and_returns_the_view(s, api, biz):
    guess(s, biz, "buyer", "consumers")
    ids = line_of(get(api, biz), "buyer")["live_ids"]
    done = post(api, biz, "buyer", action="change", if_live=ids, codes=["developers"]).json()
    assert line_of(done["profile"], "buyer")["weakest"] == "confirmed"

    r = api.post(f"/api/businesses/{biz}/profile/undo", json={"event_id": done["event_id"]})

    assert r.status_code == 200, r.text
    buyer = line_of(r.json(), "buyer")
    assert [(x["code"], x["provenance"]) for x in buyer["values"]] == [("consumers", "guess")]
    assert events(biz)[-1].data["cause"] == "owner_undo"


def test_undo_refuses_an_event_that_is_not_this_businesss_description(s, api, biz):
    other = roadmap.create_business(s, name="Another", one_liner="x").id
    guess(s, other, "buyer", "consumers")
    s.commit()
    foreign = events(other)[-1].id

    r = api.post(f"/api/businesses/{biz}/profile/undo", json={"event_id": foreign})

    assert r.status_code == 422 and r.json()["detail"] == msg("profile.error.not_undoable", "en")


def test_a_business_that_does_not_exist_is_a_404_in_the_owners_words(api):
    assert api.get("/api/businesses/999999/profile").status_code == 404
    r = post(api, 999999, "buyer", action="not_right", if_live=[])
    assert r.status_code == 404 and r.json()["detail"] == msg("profile.error.no_business", "en", id=999999)


def test_a_line_written_against_an_older_plan_is_stale_until_the_owner_says_it_is_right(s, api, biz):
    first = approve(s, biz)
    store.set_line(s, biz, "buyer", [NewValue("small_business", None, "said", "answer:1", "small bakeries")],
                   cause="kickoff_answer", written_by="project_manager", brief_id=first.id, if_live=None, lang="en")
    s.commit()
    assert line_of(get(api, biz), "buyer")["stale"] is False
    changed = approve(s, biz, "Bakeries cannot reach tourists")
    with session_scope() as t:
        approved_at = t.get(type(changed), changed.id).approved_at

    buyer = line_of(get(api, biz), "buyer")

    assert buyer["stale"] is True and datetime.fromisoformat(buyer["stale_since"]) == approved_at
    assert line_of(get(api, biz), "reach")["stale"] is False
    r = post(api, biz, "buyer", action="right", if_live=buyer["live_ids"])
    assert r.status_code == 200 and r.json()["event_id"] is not None
    cleared = line_of(r.json()["profile"], "buyer")
    assert cleared["stale"] is False and cleared["stale_since"] is None and cleared["weakest"] == "confirmed"


def said_by_the_project_manager(s, bid, line, code=None, sentence=None):
    store.set_line(s, bid, line, [NewValue(code, sentence, "said" if code else "guess", "answer:1", "my words")],
                   cause="kickoff_answer", written_by="project_manager", brief_id=None, if_live=None, lang="en")
    s.commit()


def test_right_confirms_a_value_the_project_manager_read_as_said_so_a_launch_can_go(api, biz, s):
    said_by_the_project_manager(s, biz, "buyer", "developers")
    before = line_of(get(api, biz), "buyer")
    assert before["confirmable"] is True and before["guessed"] is False

    r = post(api, biz, "buyer", action="right", if_live=before["live_ids"])

    after = line_of(r.json()["profile"], "buyer")
    assert r.status_code == 200 and after["values"][0]["provenance"] == "confirmed" and after["confirmable"] is False


def test_saving_the_editor_with_the_project_managers_sentence_unchanged_confirms_it_and_does_not_make_it_said(api, biz, s):
    said_by_the_project_manager(s, biz, "buyer", sentence="Busy parents who book after school")
    line = line_of(get(api, biz), "buyer")

    r = post(api, biz, "buyer", action="change", if_live=line["live_ids"], codes=["consumers"],
             sentence="busy parents who book  after school")

    values = {(v["code"], v["sentence"], v["provenance"]) for v in line_of(r.json()["profile"], "buyer")["values"]}
    assert values == {("consumers", None, "confirmed"), (None, "Busy parents who book after school", "confirmed")}


def test_a_line_holding_a_retired_code_can_still_be_edited(api, biz, s, monkeypatch):
    guess(s, biz, "reach", "short_video")
    monkeypatch.setattr(vocab, "RETIRED", frozenset({"short_video"}))
    line = line_of(get(api, biz), "reach")

    r = post(api, biz, "reach", action="right", if_live=line["live_ids"])

    assert r.status_code == 200, r.text
    assert line_of(r.json()["profile"], "reach")["values"][0]["provenance"] == "confirmed"
