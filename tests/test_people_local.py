"""A business whose customers come in person nearby (its description says `reach: local`) gets to-dos that fit its
neighborhood and its trade.

Persona round 3: a 망원동 side-dish shop got a to-do to answer a "건대 근처에서 싸게 살 반찬가게…" post, and the
questions for her regular customers read like a software interview ("지금 쓰는 도구나 방법이 있나요? 어디서 막히나요?",
"명단에서 지워 달라고 할 수 있다고 말하기")."""
from justshipit.services import people, people_finder, people_tasks, roadmap
from people_kit import line, make_business
from profile_kit import describe


def _shop(s, reach=("local",), provenance="confirmed"):
    b = roadmap.create_business(s, name="정숙이네 반찬", one_liner="망원동에서 12년 한 반찬가게예요")
    sections = roadmap.empty_sections()
    texts = {"one_liner": ["망원동에서 12년 한 반찬가게예요. 근처 새 아파트 맞벌이 부부가 단골 되게 하고 싶어요"],
             "customer_problem": ["새로 생긴 아파트 사람들은 가게를 몰라요"],
             "goal_deadline": ["두 달 안에 아파트 단골 열 명"],
             "background": ["가게 이름은 정숙이네 반찬이고, 망원시장 근처에서 알바 1명이랑 둘이 해요"]}
    for sec in sections:
        sec["lines"] = [line(x) for x in texts.get(sec["key"], [])]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    s.commit()
    describe(s, b.id, ["consumers"], reach, provenance=provenance)
    return b


def _warm(s, b):
    person, _ = people.insert(s, b.id, people.NewPerson(
        "direct", "지난달 온 새 아파트 애기 엄마", "brief:v1:l-1", "warm", "지난달 온 새 아파트 애기 엄마",
        why="이미 아는 사람"), lang="ko", actor="system")
    return person


def test_a_shops_regular_is_asked_about_the_shop_not_a_tool(s):
    b = _shop(s)
    card = people_tasks.person_card(s, _warm(s, b), "contact", lang="ko")
    asked = " ".join(card["questions"] + card["talking_points"] + card["checklist"])
    assert "도구" not in asked and "지워 달라고" not in asked
    assert card["questions"][0] == people_tasks.msg("people.ask.contact_local.1", "ko")


def test_a_software_business_keeps_its_interview(s):
    card = people_tasks.person_card(s, people_kit_person(s), "contact", lang="ko")
    assert card["questions"][2] == people_tasks.msg("people.ask.contact.3", "ko")


def people_kit_person(s):
    from people_kit import add
    return add(s, make_business(s))


def test_the_finder_is_told_where_a_local_shop_is_and_to_stay_there(s):
    b = _shop(s)
    brief = roadmap.approved_brief(s, b.id)
    look = people_finder._look(s, b.id)
    prompt = people_finder.threads_prompt(brief, [], look)
    assert "망원시장" in prompt and "망원동" in prompt
    assert "another neighborhood" in prompt
    assert "망원시장" in people_finder.people_prompt(brief, [], look)


def test_a_business_with_no_place_gets_no_place_rule(s):
    b = make_business(s)
    prompt = people_finder.threads_prompt(roadmap.approved_brief(s, b.id), [], people_finder._look(s, b.id))
    assert "another neighborhood" not in prompt


def test_the_place_rule_follows_the_description_not_the_words_of_the_plan(s):
    """The plan names 망원동 and 망원시장, but the owner says these customers are reached in online communities."""
    b = _shop(s, reach=("communities",))
    assert "another neighborhood" not in people_finder.threads_prompt(
        roadmap.approved_brief(s, b.id), [], people_finder._look(s, b.id))
    card = people_tasks.person_card(s, _warm(s, b), "contact", lang="ko")
    assert card["questions"][0] != people_tasks.msg("people.ask.contact_local.1", "ko")


def test_a_guess_that_customers_come_in_person_counts_and_the_prompt_says_it_is_a_guess(s):
    b = _shop(s, provenance="guess")
    prompt = people_finder.threads_prompt(roadmap.approved_brief(s, b.id), [], people_finder._look(s, b.id))
    assert "another neighborhood" in prompt and "Guess" in prompt
