"""Words fit the owner's trade: a shop owner reads 손님, not "external users"; no CPC, no CPO.

Persona round 3: 박정숙 read "외부 사용자", "재사용", "실험 BET-7", "CPC" and "CPO가 명세를 쓰고" and none of it meant
anything to her."""

from justshipit.i18n import catalog
from justshipit.orchestration import context as ctx
from justshipit.services import projects, roadmap
from i18n_kit import web_catalog
from people_kit import line, make_business
from profile_kit import describe


def _shop_project(s, reach=("local",)):
    b = roadmap.create_business(s, name="정숙이네 반찬", one_liner="망원동에서 12년 한 반찬가게예요")
    sections = roadmap.empty_sections()
    texts = {"one_liner": ["망원동 반찬가게, 근처 아파트 손님이 단골 되게"], "customer_problem": ["아파트 사람들은 가게를 몰라요"],
             "goal_deadline": ["두 달 안에 단골 열 명"]}
    for sec in sections:
        sec["lines"] = [line(x) for x in texts.get(sec["key"], [])]
    roadmap.new_brief_version(s, b.id, sections=sections, assumptions=[], author="project_manager", approved=True)
    s.commit()
    describe(s, b.id, ["consumers"], reach)
    return projects.create(s, "단골 만들기", name="단골", start=False, business=b)


def test_every_agent_of_a_local_shop_is_told_to_say_customers_and_drop_ad_jargon(s):
    brief = ctx.project_brief(s, _shop_project(s))
    assert "### Words for this business" in brief
    assert "손님" in brief and "CPC" in brief and "BET-" in brief


def test_a_software_business_gets_no_shop_words(s):
    b = make_business(s)
    p = projects.create(s, "첫 사용자", name="로그", start=False, business=b)
    assert "### Words for this business" not in ctx.project_brief(s, p)


def test_a_shop_whose_customers_are_reached_online_gets_no_shop_words_whatever_its_plan_says(s):
    assert "### Words for this business" not in ctx.project_brief(s, _shop_project(s, reach=("communities",)))


def test_every_agent_reads_the_customer_description_with_how_sure_the_company_is_of_it(s):
    brief = ctx.project_brief(s, _shop_project(s))
    assert "Customer description" in brief and "In person, nearby (Confirmed)" in brief


def test_screens_every_owner_sees_name_the_product_agent_not_a_cpo():
    web = {k: v for area in web_catalog().values() for k, v in area.items()}
    found = [k for k, v in {**catalog(), **web}.items() if "CPO" in str(v)]
    assert found == []
