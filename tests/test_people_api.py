"""The people routes: the list with its counts, today's few, what the owner does to a person, and refusals in the
owner's language (English by default)."""
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api.app import app
from justshipit.db.models import OwnerTask, Person
from justshipit.db.session import session_scope
from fastapi.testclient import TestClient
from people_kit import ISSUE_QUOTE, add, make_business
from sqlalchemy import select


@pytest.fixture
def api(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_the_routes_need_the_console_token(s):
    b = make_business(s)
    bare = TestClient(app, base_url="http://127.0.0.1:8000")
    assert bare.get(f"/api/businesses/{b.id}/people").status_code == 401
    assert bare.post(f"/api/businesses/{b.id}/people", json={"handle": "x"}).status_code == 401


def test_the_list_carries_the_counts_by_stage_and_source_and_the_north_star(s, api):
    b = make_business(s)
    add(s, b, "a", stage="found", source="warm")
    add(s, b, "b", stage="talked", source="public_problem")
    add(s, b, "c", stage="first_value", source="inbound")
    add(s, b, "d", stage="repeat", source="inbound")
    s.commit()

    got = api.get(f"/api/businesses/{b.id}/people").json()

    assert len(got["items"]) == 4 and got["total"] == 4
    assert got["by_stage"]["talked"] == 1 and got["by_stage"]["repeat"] == 1
    assert got["by_source"] == {"warm": 1, "public_problem": 1, "similar_tool": 0, "participation": 0, "inbound": 2}
    assert got["north_star"] == {"contacted": 3, "talked": 3, "first_value": 2, "repeat": 1}
    first = got["items"][0]
    assert first["ref"].startswith("PPL-") and first["stage_ko"] and first["source_ko"] and "next_stages" in first
    only = api.get(f"/api/businesses/{b.id}/people", params={"stage": "talked"}).json()
    assert [p["handle"] for p in only["items"]] == ["b"] and only["total"] == 4, "counts cover the whole list"
    assert [p["handle"] for p in api.get(f"/api/businesses/{b.id}/people", params={"source": "inbound"}).json()["items"]] == ["d", "c"]


def test_an_unknown_filter_is_a_korean_400_and_an_unknown_business_a_404(s, api):
    b = make_business(s)
    bad = api.get(f"/api/businesses/{b.id}/people", params={"stage": "vip"})
    assert bad.status_code == 400 and "Unknown stage" in bad.json()["detail"]
    assert api.get(f"/api/businesses/{b.id}/people", params={"source": "ads"}).status_code == 400
    missing = api.get("/api/businesses/99999999/people")
    assert missing.status_code == 404 and "not found" in missing.json()["detail"]
    assert api.get("/api/people/99999999").status_code == 404


def test_today_returns_the_three_to_reach(s, api):
    b = make_business(s)
    for i in range(5):
        add(s, b, f"p{i}")
    s.commit()
    got = api.get(f"/api/businesses/{b.id}/people/today").json()
    assert got["limit"] == 3 and len(got["items"]) == 3 and {p["reason"] for p in got["items"]} == {"new"}


def test_the_owner_adds_someone_by_hand_and_a_second_try_is_refused(s, api):
    b = make_business(s)
    url = f"/api/businesses/{b.id}/people"
    ok = api.post(url, json={"platform": "github", "handle": "@Dana", "display_name": "Dana K", "quote": ISSUE_QUOTE,
                             "url": "https://github.com/acme/logtool/issues/5", "why": "같은 문제를 겪음"})
    assert ok.status_code == 200
    got = ok.json()
    assert (got["handle"], got["source"], got["stage"], got["display_name"]) == ("dana", "warm", "found", "Dana K")

    again = api.post(url, json={"platform": "github", "handle": "dana"})
    assert again.status_code == 409 and "Already on the list" in again.json()["detail"]
    by_name = api.post(url, json={"handle": "박지원", "source": "warm"})
    assert by_name.status_code == 200 and by_name.json()["where_found_url"] == "owner:박지원"


def test_the_owner_s_form_refuses_a_contact_detail_and_a_body_that_does_not_fit_without_repeating_it(s, api):
    b = make_business(s)
    url = f"/api/businesses/{b.id}/people"
    email = api.post(url, json={"handle": "kim@corp.com"})
    assert email.status_code == 409 and "Not saved" in email.json()["detail"]
    phone = api.post(url, json={"handle": "kim", "quote": "전화는 010-1234-5678"})
    assert phone.status_code == 409 and "a phone number" in phone.json()["detail"] and "5678" not in phone.json()["detail"]
    bad = api.post(url, json={"handle": "kim", "secret": "SECRET-VALUE"})
    assert bad.status_code == 422 and "SECRET-VALUE" not in bad.text and "aren't in the right format" in bad.json()["detail"]
    assert api.post(url, json={"handle": "kim", "source": "ads"}).status_code == 409
    with session_scope() as x:
        assert x.scalar(select(Person.id).where(Person.business_id == b.id)) is None


def test_the_owner_moves_a_person_sets_the_next_step_and_adds_a_quote(s, api):
    b = make_business(s)
    p = add(s, b)
    s.commit()
    base = f"/api/people/{p.id}"

    moved = api.post(f"{base}/stage", json={"stage": "contacted"})
    assert moved.status_code == 200 and moved.json()["stage"] == "contacted"
    back = api.post(f"{base}/stage", json={"stage": "found"})
    assert back.status_code == 409 and "Can't move someone" in back.json()["detail"]
    assert api.post(f"{base}/stage", json={"stage": "vip"}).status_code == 409

    in_a_week = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    nxt = api.post(f"{base}/next", json={"next_action": "답이 없으면 한 번 더",
                                         "next_action_at": in_a_week.astimezone(timezone(timedelta(hours=9))).isoformat()})
    assert nxt.status_code == 200 and nxt.json()["next_action"] == "답이 없으면 한 번 더"
    assert datetime.fromisoformat(nxt.json()["next_action_at"]) == in_a_week
    assert api.post(f"{base}/next", json={"next_action_at": "내일"}).status_code == 422

    said = api.post(f"{base}/quote", json={"text": "결국 로그를 눈으로 읽었다고 함"})
    assert said.status_code == 200 and said.json()["quotes"][-1]["text"] == "결국 로그를 눈으로 읽었다고 함"
    assert api.post(f"{base}/quote", json={"text": ""}).status_code == 409


def test_archiving_over_the_api_hides_the_person_and_empties_the_stub(s, api):
    b = make_business(s)
    p = add(s, b, "alice")
    s.commit()
    done = api.post(f"/api/people/{p.id}/archive")
    assert done.status_code == 200 and done.json()["quotes"] == [] and done.json()["archived_at"]
    assert api.get(f"/api/businesses/{b.id}/people").json()["items"] == []
    shown = api.get(f"/api/businesses/{b.id}/people", params={"include_archived": "true"}).json()["items"]
    assert [x["handle"] for x in shown] == ["alice"] and shown[0]["next_stages"] == []
    assert api.post(f"/api/people/{p.id}/stage", json={"stage": "contacted"}).status_code == 409


def test_a_referral_task_is_refused_until_the_first_value_and_then_has_a_card(s, api):
    b = make_business(s)
    early = add(s, b, "early", stage="talked")
    happy = add(s, b, "happy", stage="first_value")
    s.commit()

    no = api.post(f"/api/people/{early.id}/tasks", json={"kind": "referral"})
    assert no.status_code == 409 and "Ask for an intro only" in no.json()["detail"]
    yes = api.post(f"/api/people/{happy.id}/tasks", json={"kind": "referral"})
    assert yes.status_code == 200 and yes.json()["kind"] == "referral" and yes.json()["kind_label"] == "Ask for an intro"
    assert yes.json()["card"]["person"]["handle"] == "happy"
    assert api.post(f"/api/people/{happy.id}/tasks", json={"kind": "post"}).status_code == 409


def test_finishing_a_task_over_the_api_moves_the_person_and_schedules_the_follow_up(s, api):
    b = make_business(s)
    p = add(s, b)
    s.commit()
    task = api.post(f"/api/people/{p.id}/tasks", json={"kind": "contact"}).json()
    done_url = f"/api/owner-tasks/{task['id']}/done"

    missing = api.post(done_url, json={"quote": "관심 있음"})
    assert missing.status_code == 409 and "stage" in missing.json()["detail"]
    bad_date = api.post(done_url, json={"stage": "talked", "next_action_at": "곧"})
    assert bad_date.status_code == 422 and "next date" in bad_date.json()["detail"]

    demo_at = (datetime.now(timezone.utc) + timedelta(days=9)).replace(microsecond=0)
    ok = api.post(done_url, json={"stage": "talked", "quote": "로그 때문에 주말을 날렸다고 함",
                                  "next_action": "데모 잡기", "next_action_at": demo_at.isoformat()})
    assert ok.status_code == 200 and ok.json()["status"] == "DONE"
    person = api.get(f"/api/people/{p.id}").json()
    assert person["stage"] == "talked" and person["quotes"][-1]["text"] == "로그 때문에 주말을 날렸다고 함"
    assert [t["kind"] for t in person["open_tasks"]] == ["follow_up"]
    with session_scope() as x:
        follow = x.scalar(select(OwnerTask).where(OwnerTask.kind == "follow_up", OwnerTask.business_id == b.id))
        assert follow.due_at == demo_at


def test_asking_to_look_queues_the_search_once_a_day_and_needs_a_brief(s, api):
    from justshipit.services import roadmap
    b = make_business(s)
    first = api.post(f"/api/businesses/{b.id}/people/find")
    assert first.status_code == 200 and first.json() == {"queued": {"find_people": True, "find_threads": True}}
    assert api.post(f"/api/businesses/{b.id}/people/find").json()["queued"] == {"find_people": False, "find_threads": False}
    bare = roadmap.create_business(s, name="요약 없음", one_liner="x")
    s.commit()
    refused = api.post(f"/api/businesses/{bare.id}/people/find")
    assert refused.status_code == 409 and "Approve the plan" in refused.json()["detail"]
