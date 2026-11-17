"""The people list: stages, no contact details, forgetting someone, today's few, and the owner tasks about people."""
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.db.models import ActivityEvent, OwnerTask, Person
from justshipit.db.session import session_scope
from justshipit.i18n import msg
from justshipit.services import channels, events, owner_tasks, people, people_tasks
from justshipit.services.owner_tasks import CardRefused, OwnerTaskError
from people_kit import ISSUE_QUOTE, add, line, make_business
from sqlalchemy import select

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def days(n: float) -> datetime:
    return NOW + timedelta(days=n)


# ------------------------------------------------------------------ the stage machine

def test_a_person_moves_forward_to_any_later_stage_and_never_back(s):
    b = make_business(s)
    p = add(s, b)
    people.move(s, p.id, "talked", lang="ko")
    assert p.stage == "talked", "the owner may skip a step the meeting made unnecessary"
    people.move(s, p.id, "repeat", lang="ko")
    for backwards in ("first_value", "talked", "contacted", "found"):
        with pytest.raises(people.PeopleError, match="옮길 수 없어요"):
            people.move(s, p.id, backwards, lang="ko")
    assert p.stage == "repeat"


def test_anyone_can_drop_and_only_found_follows_a_drop(s):
    b = make_business(s)
    p = add(s, b)
    people.move(s, p.id, "first_value", lang="ko")
    people.move(s, p.id, "dropped", lang="ko")
    for target in ("contacted", "talked", "first_value", "repeat"):
        with pytest.raises(people.PeopleError):
            people.move(s, p.id, target, lang="ko")
    people.move(s, p.id, "found", lang="ko")
    assert p.stage == "found"


def test_only_the_owner_moves_a_person(s):
    b = make_business(s)
    p = add(s, b)
    with pytest.raises(people.PeopleError, match="오너님만"):
        people.move(s, p.id, "contacted", by="research", lang="ko")
    assert p.stage == "found"


def test_dropping_someone_ends_their_follow_up_date(s):
    b = make_business(s)
    p = add(s, b)
    people.set_next(s, p.id, next_action="다시 연락", next_action_at=days(2), lang="ko")
    people.move(s, p.id, "dropped", lang="ko")
    assert (p.next_action, p.next_action_at) == (None, None)


# ------------------------------------------------------------------ no contact details

@pytest.mark.parametrize("text", [
    "jane@example.com", "mail me: jane.doe+logs@corp.co.kr", "jane [at] example [dot] com", "jane(at)example.com",
    "010-1234-5678", "01012345678", "+82 10 1234 5678", "+1 415 555 0100", "(415) 555-0100", "02-123-4567",
])
def test_an_email_or_a_phone_number_is_recognized(text):
    assert people.contact_info_problem(f"연락은 {text} 로 주세요") is not None


@pytest.mark.parametrize("text", [
    "upgrade to v1.2.3 and retry", "it failed at 2026-11-03 10:23:45 on the server", "connect to 192.168.100.1",
    "see issue #12345 and PR 987", "took 3 hours to find", "ask @alice-dev about it", "2GB of logs, 40 minutes",
])
def test_an_ordinary_number_or_mention_is_not_a_contact_detail(text):
    assert people.contact_info_problem(text) is None


@pytest.mark.parametrize("field, value", [
    ("handle", "alice@example.com"), ("quote", "call me at 010-1234-5678 if it fails again"),
    ("why", "reach her at jane@corp.com"),
])
def test_the_list_refuses_to_store_a_contact_detail(s, field, value):
    b = make_business(s)
    fields = dict(handle="alice", quote=ISSUE_QUOTE, why="이유") | {field: value}
    new = people.NewPerson("github", fields["handle"], "https://github.com/a/b/issues/1", "public_problem",
                           why=fields["why"], quote=fields["quote"])
    with pytest.raises(people.PeopleError, match="저장하지 않았어요"):
        people.insert(s, b.id, new, lang="ko")
    assert s.scalar(select(Person.id).where(Person.business_id == b.id)) is None


def test_the_table_has_no_email_or_phone_column():
    assert not {c.name for c in Person.__table__.columns} & {"email", "phone", "mobile", "tel", "contact"}


# ------------------------------------------------------------------ one row per person

def test_the_same_person_typed_differently_is_one_row_and_a_new_link_adds_a_quote(s):
    b = make_business(s)
    first = add(s, b, "alice")
    again, outcome = people.insert(s, b.id, people.NewPerson(
        "github", "@Alice", "https://github.com/acme/other/issues/9", "similar_tool", quote="Same pain on another repo"), lang="ko")
    assert outcome == "duplicate" and again.id == first.id
    assert [q["url"] for q in again.quotes] == [first.where_found_url, "https://github.com/acme/other/issues/9"]


def test_the_same_link_is_one_person_whoever_it_is_filed_under(s):
    b = make_business(s)
    first = add(s, b, "alice")
    _, outcome = people.insert(s, b.id, people.NewPerson("github", "alice2", first.where_found_url, "public_problem"), lang="ko")
    assert outcome == "duplicate"


def test_another_business_may_list_the_same_person(s):
    one, two = make_business(s), make_business(s, name="다른 사업")
    add(s, one, "alice")
    assert add(s, two, "alice").business_id == two.id


# ------------------------------------------------------------------ inbound

def test_a_collector_adds_someone_who_came_to_us(s):
    b = make_business(s)
    p = people.add_inbound(s, b.id, "github", "carol", "https://github.com/acme/logtool/issues/77",
                           "Your CLI crashed on my 5GB log, any idea why it stops halfway", lang="en")
    assert (p.source, p.stage, p.platform, p.handle) == ("inbound", "found", "github", "carol")
    assert p.quotes[0]["text"].startswith("Your CLI crashed") and p.quotes[0]["url"].endswith("/issues/77")
    assert s.scalar(select(ActivityEvent.id).where(ActivityEvent.verb == "people_found", ActivityEvent.entity_id == p.id))


def test_inbound_refuses_an_email_address_as_a_handle_and_one_in_the_quote(s):
    b = make_business(s)
    with pytest.raises(people.PeopleError):
        people.add_inbound(s, b.id, "email", "carol@corp.com", "https://example.com/mail/1", "hello", lang="en")
    with pytest.raises(people.PeopleError):
        people.add_inbound(s, b.id, "github", "carol", "https://github.com/a/b/issues/1", "write to me at carol@corp.com", lang="en")
    assert people.counts(s, b.id)["total"] == 0


# ------------------------------------------------------------------ forgetting someone

def test_archiving_clears_what_identifies_a_person_and_keeps_a_stub_that_blocks_a_second_find(s):
    b = make_business(s)
    p = add(s, b, "alice", prep={"questions": ["마지막으로 언제였나요?"], "talking_points": []})
    task = people_tasks.create_task(s, p.id, "contact", lang="ko")
    pid, tid, handle = p.id, task.id, p.handle
    s.commit()

    people.archive(s, pid, lang="ko")
    s.commit()
    with session_scope() as x:
        stub = x.get(Person, pid)
        assert stub.quotes == [] and stub.prep == {} and stub.why == "" and stub.profile_url is None
        assert stub.display_name == handle and stub.where_found_url == f"archived:{pid}" and stub.archived_at
        gone = x.get(OwnerTask, tid)
        assert gone.status == "SKIPPED" and gone.card == {"person_id": pid, "archived": True}
        words = [e.summary + str(e.data) for e in x.scalars(select(ActivityEvent).where(
            (ActivityEvent.entity == "person") & (ActivityEvent.entity_id == pid)
            | (ActivityEvent.entity == "owner_task") & (ActivityEvent.entity_id == tid)))]
        assert words and not any("alice" in w or "grepping" in w for w in words if people.archived_note("ko") not in w)
        assert people.counts(x, b.id)["total"] == 0, "a forgotten person is in no count"

    with session_scope() as x:
        assert people.add_inbound(x, b.id, "github", "Alice", "https://github.com/acme/logtool/issues/99", ISSUE_QUOTE,
                                  lang="en") is None
        assert people.counts(x, b.id)["total"] == 0
        with pytest.raises(people.PeopleError, match="지운"):
            people.add_quote(x, pid, "다시 적기", lang="ko")
        with pytest.raises(OwnerTaskError, match="지운"):
            people_tasks.create_task(x, pid, "contact", lang="ko")


def test_archiving_twice_changes_nothing(s):
    b = make_business(s)
    p = add(s, b)
    first = people.archive(s, p.id, lang="ko").archived_at
    assert people.archive(s, p.id, lang="ko").archived_at == first


# ------------------------------------------------------------------ today's few

def test_today_lists_overdue_people_first_then_new_ones_and_stops_at_three(s):
    b = make_business(s)
    new_public = add(s, b, "pub", source="public_problem")
    new_warm = add(s, b, "warm1", source="warm", platform="direct", url="brief:v1:k1")
    new_inbound = add(s, b, "inb", source="inbound")
    overdue_old = add(s, b, "old", stage="contacted")
    overdue_new = add(s, b, "recent", stage="talked")
    later = add(s, b, "later", stage="contacted")
    dropped = add(s, b, "gone", stage="contacted")
    forgotten = add(s, b, "forgotten", stage="contacted")
    people.set_next(s, overdue_old.id, next_action="결과 묻기", next_action_at=days(-5), lang="ko")
    people.set_next(s, overdue_new.id, next_action="데모", next_action_at=days(-1), lang="ko")
    people.set_next(s, later.id, next_action="나중에", next_action_at=days(3), lang="ko")
    people.set_next(s, dropped.id, next_action="이미 그만둠", next_action_at=days(-9), lang="ko")
    people.set_next(s, forgotten.id, next_action="잊어 달라 함", next_action_at=days(-9), lang="ko")
    people.move(s, dropped.id, "dropped", lang="ko")
    people.archive(s, forgotten.id, lang="ko")

    top = people.today(s, b.id, now=NOW, lang="ko")
    assert [(x["id"], x["reason"]) for x in top] == [(overdue_old.id, "overdue"), (overdue_new.id, "overdue"),
                                                    (new_inbound.id, "new")]
    everyone = people.today(s, b.id, now=NOW, limit=10, lang="ko")
    assert [x["id"] for x in everyone] == [overdue_old.id, overdue_new.id, new_inbound.id, new_warm.id, new_public.id]


def test_today_is_empty_when_nobody_is_due(s):
    b = make_business(s)
    add(s, b, "quiet", stage="contacted")
    assert people.today(s, b.id, now=NOW, lang="ko") == []


# ------------------------------------------------------------------ the numbers

def test_counts_by_stage_and_source_and_the_north_star(s):
    b = make_business(s)
    for handle, stage, source in [("a", "found", "warm"), ("b", "contacted", "public_problem"),
                                  ("c", "talked", "public_problem"), ("d", "first_value", "inbound"),
                                  ("e", "repeat", "warm"), ("f", "repeat", "inbound"), ("g", "dropped", "warm")]:
        add(s, b, handle, stage=stage, source=source)
    gone = add(s, b, "h", stage="repeat")
    people.archive(s, gone.id, lang="ko")

    got = people.counts(s, b.id)
    assert got["by_stage"] == {"found": 1, "contacted": 1, "talked": 1, "first_value": 1, "repeat": 2, "dropped": 1}
    assert got["by_source"] == {"warm": 3, "public_problem": 2, "similar_tool": 0, "participation": 0, "inbound": 2}
    assert got["total"] == 7
    assert people.north_star(s, b.id) == {"contacted": 5, "talked": 4, "first_value": 3, "repeat": 2}


# ------------------------------------------------------------------ the cards

def test_a_contact_card_gives_the_person_the_questions_and_the_points_but_no_message(s):
    b = make_business(s)
    p = add(s, b, prep={"questions": ["마지막으로 원인을 못 찾은 때가 언제였나요?"], "talking_points": ["공개 글을 보고 연락한다고 밝힌다"]})
    task = people_tasks.create_task(s, p.id, "contact", lang="ko")
    card = task.card
    assert task.kind == "contact" and task.status == "OPEN" and task.business_id == b.id
    assert card["person"]["handle"] == "alice" and card["person"]["where_found_url"] == p.where_found_url
    assert card["quotes"] == [{"text": ISSUE_QUOTE, "url": p.where_found_url}]
    assert card["why"] == p.why and "서버 로그" in card["problem"]
    assert card["questions"] == ["마지막으로 원인을 못 찾은 때가 언제였나요?"]
    assert card["talking_points"] == ["공개 글을 보고 연락한다고 밝힌다"]
    assert owner_tasks.fixed_check("ko") in card["checklist"] and "note" not in card
    assert not {"draft", "message", "body", "title", "email"} & set(card)


def test_a_card_for_someone_found_in_public_says_to_answer_in_public_first_and_a_warm_one_does_not(s):
    b = make_business(s)
    public = people_tasks.create_task(s, add(s, b, "pub").id, "contact", lang="ko").card["checklist"]
    warm = people_tasks.create_task(s, add(s, b, "kim", source="warm", platform="direct", url="brief:v1:k1").id,
                                    "contact", lang="ko").card["checklist"]
    assert msg("people.check.public_first", "ko") in public and msg("people.check.public_first", "ko") not in warm
    assert public[0] == msg("people.check.own_words", "ko") and public[-1] == owner_tasks.fixed_check("ko")


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_contact_card_says_it_reminds_the_owner_to_disclose_in_any_wording(s, lang):
    """The to-do screen adds its own reminder only to a card without one, and reads that from the card, not its words."""
    b = make_business(s)
    contact = people_tasks.create_task(s, add(s, b, "pub").id, "contact", lang=lang).card
    follow_up = people_tasks.create_task(s, add(s, b, "kim").id, "follow_up", lang=lang).card
    assert contact["disclosed"] is True and msg("people.check.disclose", lang) in contact["checklist"]
    assert follow_up["disclosed"] is False


def test_a_person_without_prepared_notes_gets_the_past_behavior_questions(s):
    b = make_business(s)
    task = people_tasks.create_task(s, add(s, b).id, "contact", lang="ko")
    assert any("마지막으로" in q for q in task.card["questions"]) and len(task.card["talking_points"]) >= 1
    assert not any("쓰시겠어요" in q or "좋아하" in q for q in task.card["questions"]), "no opinion questions"


def test_asking_again_for_the_same_kind_returns_the_open_task(s):
    b = make_business(s)
    p = add(s, b)
    first = people_tasks.create_task(s, p.id, "contact", lang="ko")
    assert people_tasks.create_task(s, p.id, "contact", lang="ko").id == first.id
    assert s.scalar(select(OwnerTask.id).where(OwnerTask.kind == "follow_up", OwnerTask.business_id == b.id)) is None


def test_a_card_with_an_ask_for_votes_stars_or_reviews_is_refused(s):
    b = make_business(s)
    p = add(s, b, prep={"questions": ["써 보시고 GitHub에 star 눌러 주실 수 있나요?"], "talking_points": []})
    with pytest.raises(CardRefused, match="star"):
        people_tasks.create_task(s, p.id, "contact", lang="ko")


def test_a_card_with_a_contact_detail_is_refused(s):
    b = make_business(s)
    p = add(s, b)
    p.prep = {"questions": ["메일 jane@corp.com 으로 보내도 될까요?"], "talking_points": []}
    with pytest.raises(CardRefused, match="이메일"):
        people_tasks.create_task(s, p.id, "contact", lang="ko")


def test_the_model_s_questions_are_cleaned_item_by_item():
    kept, dropped = people_tasks.clean_points([
        "마지막으로 원인을 찾은 때가 언제였나요?",
        "이 도구를 쓰시겠어요? 그리고 star도 눌러 주세요. 감사합니다.",
        "x" * 130, "010-1234-5678로 전화해도 될까요?", "그때 무엇을 하셨나요?", "세 번째 질문입니다", "네 번째 질문입니다"], 3)
    assert kept == ["마지막으로 원인을 찾은 때가 언제였나요?", "그때 무엇을 하셨나요?", "세 번째 질문입니다"]
    assert dropped == 3


# ------------------------------------------------------------------ finishing a task about a person

def finish(s, task, **outcome):
    return owner_tasks.done(s, task.id, outcome=people_tasks.Outcome(**outcome))


def test_done_moves_the_person_keeps_the_quote_and_makes_the_follow_up_task(korean_owner, s):
    b = make_business(s)
    p = add(s, b)
    task = people_tasks.create_task(s, p.id, "contact", lang="ko")

    finish(s, task, stage="talked", quote="결국 로그를 에디터로 열어 눈으로 찾았다고 함", next_action="데모 일정 잡기",
           next_action_at=days(3))

    assert task.status == "DONE" and p.stage == "talked"
    assert p.quotes[-1]["text"] == "결국 로그를 에디터로 열어 눈으로 찾았다고 함"
    assert (p.next_action, p.next_action_at) == ("데모 일정 잡기", days(3))
    follow = s.scalar(select(OwnerTask).where(OwnerTask.business_id == b.id, OwnerTask.kind == "follow_up",
                                              OwnerTask.status == "OPEN"))
    assert follow.card["person_id"] == p.id and follow.due_at == days(3)
    assert follow.card["quotes"][-1]["text"].startswith("결국 로그를"), "the follow-up shows what they said last"
    assert s.scalar(select(ActivityEvent.id).where(ActivityEvent.verb == "person_moved", ActivityEvent.entity_id == p.id))


def test_finishing_the_follow_up_without_a_date_takes_the_person_off_today_s_list(s):
    b = make_business(s)
    p = add(s, b, stage="contacted")
    people.set_next(s, p.id, next_action="물어보기", next_action_at=days(-1), lang="ko")
    follow = people_tasks.create_task(s, p.id, "follow_up", due_at=days(-1), lang="ko")
    assert [x["id"] for x in people.today(s, b.id, now=NOW, lang="ko")] == [p.id]

    finish(s, follow, stage="contacted")

    assert (p.next_action, p.next_action_at) == (None, None) and people.today(s, b.id, now=NOW, lang="ko") == []


def test_done_needs_the_stage_and_a_stage_the_person_can_reach(korean_owner, s):
    b = make_business(s)
    p = add(s, b, stage="talked")
    task = people_tasks.create_task(s, p.id, "follow_up", lang="ko")
    with pytest.raises(OwnerTaskError, match="단계"):
        owner_tasks.done(s, task.id)
    with pytest.raises(OwnerTaskError, match="옮길 수 없어요"):
        finish(s, task, stage="contacted")
    with pytest.raises(OwnerTaskError, match="모르는 단계"):
        finish(s, task, stage="vip")
    with pytest.raises(OwnerTaskError, match="날짜"):
        finish(s, task, stage="dropped", next_action_at=days(2))
    assert task.status == "OPEN" and p.stage == "talked" and p.next_action_at is None, "a refusal changes nothing"


def test_done_refuses_a_quote_with_a_phone_number_and_changes_nothing(korean_owner, s):
    b = make_business(s)
    p = add(s, b)
    task = people_tasks.create_task(s, p.id, "contact", lang="ko")
    with pytest.raises(people.PeopleError, match="전화번호"):
        finish(s, task, stage="contacted", quote="010-1234-5678로 연락 달라고 함")
    assert task.status == "OPEN" and p.stage == "found" and len(p.quotes) == 1


def test_dropping_through_a_task_makes_no_follow_up(s):
    b = make_business(s)
    p = add(s, b)
    finish(s, people_tasks.create_task(s, p.id, "contact", lang="ko"), stage="dropped", quote="지금은 관심 없다고 함")
    assert p.stage == "dropped" and s.scalar(select(OwnerTask.id).where(OwnerTask.kind == "follow_up", OwnerTask.business_id == b.id)) is None


def test_a_task_that_is_not_about_a_person_refuses_a_stage(korean_owner, s):
    b = make_business(s)
    task = OwnerTask(business_id=b.id, kind="other", title="결제", card={}, status="OPEN")
    s.add(task)
    s.flush()
    with pytest.raises(OwnerTaskError, match="단계나 인용"):
        finish(s, task, stage="talked")


# ------------------------------------------------------------------ referral

@pytest.mark.parametrize("stage", ["found", "contacted", "talked"])
def test_a_referral_is_for_someone_who_saw_the_first_value(s, stage):
    b = make_business(s)
    p = add(s, b, stage=stage)
    with pytest.raises(OwnerTaskError, match="도움 됨이나 재사용 단계인 사람에게만"):
        people_tasks.create_task(s, p.id, "referral", lang="ko")
    assert s.scalar(select(OwnerTask.id).where(OwnerTask.kind == "referral", OwnerTask.business_id == b.id)) is None


def test_no_task_is_made_for_someone_who_dropped(s):
    b = make_business(s)
    p = add(s, b, stage="dropped")
    with pytest.raises(OwnerTaskError, match="그만둔"):
        people_tasks.create_task(s, p.id, "contact", lang="ko")


@pytest.mark.parametrize("stage", ["first_value", "repeat"])
def test_a_referral_card_asks_for_a_name_and_never_for_stars_votes_or_reviews(s, stage):
    b = make_business(s)
    p = add(s, b, stage=stage)
    card = people_tasks.create_task(s, p.id, "referral", lang="ko").card
    text = " ".join([*card["questions"], *card["talking_points"], card["why"]])
    assert channels.forbidden_reason([text]) is None
    assert any("주변에" in q for q in card["questions"]) and owner_tasks.fixed_check("ko") in card["checklist"]


def test_a_referral_task_cannot_be_finished_for_someone_who_dropped_since(korean_owner, s):
    b = make_business(s)
    p = add(s, b, stage="first_value")
    task = people_tasks.create_task(s, p.id, "referral", lang="ko")
    people.move(s, p.id, "dropped", lang="ko")
    with pytest.raises(OwnerTaskError, match="도움 됨이나 재사용"):
        finish(s, task, stage="dropped")
    assert task.status == "OPEN"


# ------------------------------------------------------------------ the warm list

def test_the_names_in_the_brief_become_warm_people_with_a_contact_task_each(s):
    b = make_business(s, known=[line("김민수, 전 직장 동료로 서버 운영을 함", id="k1", person="김민수"),
                                line("박지원, 스터디 친구", "confirmed", id="k2", person="박지원"),
                                line("이름은 모르는 짐작", "guess", id="k3", person="누군가"),
                                {"id": "k4", "text": "", "provenance": "blank", "source": None}])
    with session_scope() as x:
        assert people_tasks.import_warm(x, b.id, lang="ko") == {"imported": 2, "existing": 0}
    with session_scope() as x:
        warm = list(x.scalars(select(Person).where(Person.business_id == b.id).order_by(Person.id)))
        assert [(p.source, p.stage, p.platform) for p in warm] == [("warm", "found", "direct")] * 2
        assert warm[0].display_name == "김민수" and warm[0].where_found_url.endswith(":k1")
        assert warm[0].why == "김민수, 전 직장 동료로 서버 운영을 함", "how the owner knows them is the card's why"
        tasks = list(x.scalars(select(OwnerTask).where(OwnerTask.business_id == b.id).order_by(OwnerTask.id)))
        assert [(t.kind, t.title) for t in tasks] == [("contact", "김민수 님과 이야기하기"), ("contact", "박지원 님과 이야기하기")]
        assert people_tasks.import_warm(x, b.id, lang="ko") == {"imported": 0, "existing": 2}, "a second run adds nobody"


def test_a_warm_to_do_is_a_short_action_with_a_clean_name_not_a_slice_of_the_owner_s_sentence(s):
    """OT-15..18 read 'Reach out: dani, a barista at the café you go to most mornings in your neighbourhood. you're a
    regular and you (In person)' and 'Reach out: you don't know any café owners or roasters in toronto yet': the
    whole plan line was the person's name, cut at 100 characters, and a line naming a group or nobody became a person."""
    long = ("Dani, a barista at the café you go to most mornings in your neighbourhood. You're a regular and you've "
            "watched her run out of oat milk twice · seen firsthand")
    b = make_business(s, known=[
        line(long, id="k1", person="Dani"),
        line("Jake, a coworker on your team", id="k2", person="Jake"),
        line("About 40 agency owners in your LinkedIn network, plus the Austin agency founders Slack, r/agency and "
             "Indie Hackers", id="k3"),
        line("You don't know any café owners or roasters in Toronto yet · seen firsthand", id="k4")])
    assert people_tasks.import_warm(s, b.id, lang="en") == {"imported": 2, "existing": 0}

    tasks = list(s.scalars(select(OwnerTask).where(OwnerTask.business_id == b.id).order_by(OwnerTask.id)))
    assert [t.title for t in tasks] == ["Talk to Dani", "Talk to Jake"]
    dani = tasks[0].card["person"]
    assert dani["display_name"] == "Dani" and tasks[0].card["why"] == long


def test_a_person_found_in_public_is_reached_where_they_were_found(s):
    b = make_business(s)
    p = add(s, b, "alice")
    assert people_tasks.create_task(s, p.id, "contact", lang="en").title == "Reach out to alice on GitHub"
    p.stage = "first_value"
    assert people_tasks.create_task(s, p.id, "referral", lang="en").title == "Ask alice for an intro"


@pytest.mark.parametrize("known", [None, []])
def test_a_brief_without_the_section_or_with_it_empty_adds_nobody(s, known):
    b = make_business(s, known=known)
    assert people_tasks.import_warm(s, b.id, lang="ko") == {"imported": 0, "existing": 0}
    assert people.counts(s, b.id)["total"] == 0 and s.scalar(select(OwnerTask.id).where(OwnerTask.business_id == b.id)) is None


def test_a_phone_number_or_email_the_owner_typed_in_the_brief_is_left_out(s):
    b = make_business(s, known=[line("박지원 010-1234-5678 jiwon@corp.com 스터디 친구", id="k1", person="박지원")])
    people_tasks.import_warm(s, b.id, lang="ko")
    p = s.scalar(select(Person).where(Person.business_id == b.id))
    assert "010" not in p.handle + p.display_name and "@" not in p.handle + p.display_name and "박지원" in p.display_name


# ------------------------------------------------------------------ taking part

THREAD = dict(platform="stackoverflow", url="https://stackoverflow.com/questions/77/trace", asker="bob",
              quote="How do I find which request caused the crash in a 3GB nginx log file",
              asks="큰 nginx 로그에서 장애를 일으킨 요청을 찾는 방법을 묻는다",
              talking_points=["요청 id로 묶어서 본다", "시간대를 먼저 좁힌다"])


def test_a_participate_card_is_an_outline_with_the_help_only_line(s):
    b = make_business(s)
    task = people_tasks.create_participate(s, b.id, people_tasks.Thread(**THREAD), lang="ko")
    card = task.card
    assert task.kind == "participate" and card["thread"]["url"] == THREAD["url"]
    assert card["asks"] == THREAD["asks"] and card["talking_points"] == THREAD["talking_points"]
    assert card["checklist"][0] == "링크·홍보 없이 도움만" and owner_tasks.fixed_check("ko") in card["checklist"]
    assert not {"draft", "body", "title", "message"} & set(card)


@pytest.mark.parametrize("change, rule", [
    ({"talking_points": ["이 글에 답하면서 우리 도구 링크를 같이 남기고 star도 눌러 달라고 하면 된다"]}, "forbidden_ask"),
    ({"talking_points": ["x" * 130]}, "outline_too_long"),
    ({"talking_points": ["첫째 문장입니다. 둘째 문장도 있습니다."]}, "outline_many_sentences"),
    ({"talking_points": []}, "outline_empty"),
    ({"asks": "메일 jane@corp.com 으로 답을 달라고 한다"}, "contact_info"),
])
def test_a_participate_card_that_is_prose_an_ask_or_has_a_contact_detail_is_refused(s, change, rule):
    b = make_business(s)
    with pytest.raises(CardRefused) as refused:
        people_tasks.create_participate(s, b.id, people_tasks.Thread(**{**THREAD, **change}), lang="ko")
    assert refused.value.rule == rule


def test_the_same_thread_is_offered_once_even_after_the_owner_skipped_it(s):
    b = make_business(s)
    task = people_tasks.create_participate(s, b.id, people_tasks.Thread(**THREAD), lang="ko")
    owner_tasks.skip(s, task.id, note="답할 수 없음")
    assert people_tasks.create_participate(s, b.id, people_tasks.Thread(**THREAD), lang="ko") is None


def test_answering_a_thread_puts_the_asker_on_the_list_as_met_by_taking_part(s):
    b = make_business(s)
    task = people_tasks.create_participate(s, b.id, people_tasks.Thread(**THREAD), lang="ko")
    owner_tasks.done(s, task.id, link="https://stackoverflow.com/a/78", note="답했다")
    asker = s.scalar(select(Person).where(Person.business_id == b.id))
    assert (asker.handle, asker.source, asker.stage, asker.platform) == ("bob", "participation", "found", "stackoverflow")
    assert asker.quotes[0]["text"].startswith("How do I find which request")


def test_skipping_a_thread_adds_nobody(s):
    b = make_business(s)
    task = people_tasks.create_participate(s, b.id, people_tasks.Thread(**THREAD), lang="ko")
    owner_tasks.skip(s, task.id, note="안 함")
    assert people.counts(s, b.id)["total"] == 0


# ------------------------------------------------------------------ the reports

def test_the_weekly_review_counts_tasks_about_people_but_does_not_list_the_people(s):
    b = make_business(s)
    p = add(s, b, "alice")
    people_tasks.create_task(s, p.id, "contact", lang="ko")
    people_tasks.create_participate(s, b.id, people_tasks.Thread(**THREAD), lang="ko")
    text = owner_tasks.portfolio_section(s)
    assert f"B-{b.id}" in text and "open 2" in text.split(f"B-{b.id}")[1].split("\n")[0]
    assert "alice" not in text and "REQ-None" not in text and THREAD["asks"] not in text


# ------------------------------------------------------------------ the console watches the table

def test_a_new_person_reaches_the_live_event_stream(s):
    b = make_business(s)
    p = add(s, b)
    assert events._describe(p) == {"table": "people", "id": p.id, "business_id": b.id}
