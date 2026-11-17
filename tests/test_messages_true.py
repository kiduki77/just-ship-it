"""Every message the company drafts for a real person lands on that person's to-do, ready to copy, and says only true
things (persona round 6, 미영 반찬 P-11 and 준호 P-10).

P-11: the KakaoTalk words for 박은주, 이정희 and 최수진 went up as one request whose `to` named all three, so they reached
none of their "이야기하기" to-dos, and those to-dos asked the generic interview questions ("파는 게 아니라 겪은 일을 듣고
싶다고 말하고") while the plan asked them to join the group chat and pre-order. P-10: the report told the owner to hold
an approved sample-file message for a revision nothing had queued, and a message to a colleague opened with "전에
얘기했던 창고 소개 건으로", a talk the owner never mentioned."""
import pytest
from justshipit.agents.independence import Served
from justshipit.contracts.planning import ProjectManagerPlanOut, FinalReportOut
from justshipit.contracts.requests import AgentAskDraft, Handout, RequestType, RiskLevel
from justshipit.contracts.work import AgentResultOut, ReviewOut, ReviewVerdict, TaskDraft
from justshipit.db.models import AgentRequest, Artifact, OwnerTask, Task
from justshipit.db.session import session_scope
from justshipit.orchestration import company_graph
from justshipit.orchestration import context as ctx
from justshipit.services import owner_tasks, people, people_tasks, projects, requests
from owner_task_kit import approve
from people_kit import line, make_business
from sqlalchemy import select
from ask_kit import contract, by_project_manager

KNOWN = [line("박은주 님, 퇴근길에 가게에 들러 반찬을 사 가는 단골이에요.", id="k1", person="박은주"),
         line("이정희 님, 지난주 금요일에 깻잎장아찌를 찾으셨던 단골이에요.", id="k2", person="이정희"),
         line("최수진 님, 주 1~2번 오시는 단골이에요.", id="k3", person="최수진")]
WORDS = "안녕하세요. 다음 주 반찬 사진을 카톡으로 보내 드려도 될까요? 미리 말씀해 주시면 따로 챙겨 둘게요."


def ask(to: str, title: str = "가게에서 드릴 말", body: str = WORDS, **kw) -> AgentAskDraft:
    base = {"type": RequestType.APPROVAL, "title": title, "summary": "단골께 드릴 말이에요.",
            "rationale": "계획의 첫 대화예요.", "options": [], "recommended_option": None,
            "action_kind": "external.interview_outreach", "estimated_cost_usd": None, "risk_level": RiskLevel.LOW,
            "context": contract(), "handout": Handout(kind="message", to=to, body=body)}
    return AgentAskDraft(**{**base, **kw})


def _shop(s, known=KNOWN):
    b = make_business(s, known=known, name="미영 반찬")
    p = projects.create(s, "단골 30집 미리 주문", name="반찬 예약", budget_usd=40, start=False, business=b)
    people_tasks.first_talks(s, b.id, "", lang="ko")
    s.commit()
    return b, p


def _talks(business_id: int) -> dict[str, OwnerTask]:
    with session_scope() as x:
        rows = list(x.scalars(select(OwnerTask).where(OwnerTask.business_id == business_id,
                                                      OwnerTask.kind == "contact").order_by(OwnerTask.id)))
        x.expunge_all()
    return {t.card["person"]["display_name"]: t for t in rows}


# ------------------------------------------------------------------ item 1: a message reaches each person's to-do

def _author(s, project, monkeypatch, passes: list[list[AgentAskDraft]]) -> tuple[int, list[str]]:
    authored: list[str] = []
    outputs = iter(passes)

    def run_agent(agent, purpose, prompt, model, **kw):
        if model is AgentResultOut:
            authored.append(prompt)
            return AgentResultOut(summary="s", findings_markdown="f", evidence=[], assumptions=[], risks=[],
                                  open_questions=[], unmet_criteria=[], proposed_follow_up_tasks=[],
                                  requests=next(outputs), capability_gaps=[], financial_model=None)
        return ReviewOut(verdict=ReviewVerdict.PASS, criteria_met=["글"], criteria_unmet=[], claim_checks=[],
                         counter_arguments=[], rework_instructions=None, contested=False)

    s.commit()
    monkeypatch.setattr(company_graph, "last_served", lambda: Served("claude", "m"))
    monkeypatch.setattr(company_graph.evidence, "verify_sources", lambda ids: None)
    monkeypatch.setattr(company_graph, "run_agent", run_agent)
    with session_scope() as x:
        t = Task(project_id=project.id, key="kakao", title="세 분께 드릴 말", owner="marketing", objective="o",
                 acceptance_criteria=["글"], status="READY", max_iterations=2)
        x.add(t)
        x.flush()
        tid = t.id
    company_graph.work({"project_id": project.id, "task_id": tid})
    company_graph.schedule({"project_id": project.id})
    return tid, authored


def test_one_message_for_three_people_goes_back_and_comes_up_as_one_ask_each_on_their_to_dos(s, monkeypatch):
    b, p = _shop(s)
    together = ask("박은주 님, 최수진 님 (공통판) / 이정희 님 (이정희 님판)", title="가게에서 세 분께 드릴 말 최종본")
    each = [ask(f"{name} 님", title=f"{name} 님께 드릴 말") for name in ("박은주", "이정희", "최수진")]
    tid, authored = _author(s, p, monkeypatch, [[together], each])

    assert len(authored) == 2, "the server sends the shared message back before review"
    assert "박은주" in authored[1] and "이정희" in authored[1] and "최수진" in authored[1]
    assert "one request per person" in authored[1]
    with session_scope() as x:
        raised = list(x.scalars(select(AgentRequest).where(AgentRequest.task_id == tid).order_by(AgentRequest.id)))
        for req in raised:
            approve(x, req, confirm_text=f"REQ-{req.id}")
    talks = _talks(b.id)
    assert {name: (t.card.get("handout") or {}).get("to") for name, t in talks.items()} == {
        "박은주": "박은주 님", "이정희": "이정희 님", "최수진": "최수진 님"}, "each message is on that person's to-do"


def test_a_message_to_one_person_is_not_sent_back():
    assert owner_tasks.author_problems([ask("박은주 님")], named=lambda text: ["박은주"]) == []


def test_every_author_reads_that_a_message_reaches_a_person_only_as_their_own_ask(s):
    _, p = _shop(s)
    brief = ctx.project_brief(s, p)
    assert "one request per person" in brief
    assert "박은주, 이정희, 최수진" in brief
    assert "name that REQ in `replaces`" in brief, "a person whose to-do has a message already is changed, not doubled"


# ------------------------------------------------------------------ item 1: the talk follows the plan's ask

ASKS = ["다음 주 반찬 사진을 카톡 단체방에서 받아 보실래요?", "받고 싶은 반찬을 미리 말씀해 주실 수 있을까요?"]


def _plan(s, monkeypatch, business, talk_asks: list[str]) -> None:
    project = projects.create(s, "단골 30집 미리 주문", name="반찬 예약", budget_usd=40, start=False, business=business)
    s.commit()
    out = ProjectManagerPlanOut(goal_interpretation="g", deliverables=["d"], success_criteria=["c"], out_of_scope=[],
                     key_uncertainties=[], stop_rule="세 분 중 1명 이하가 주문하면 멈춰요.", talk_asks=talk_asks,
                     build=None, requests=[],
                     tasks=[TaskDraft(key="kakao", title="카톡 문구", owner="marketing", objective="o",
                                      acceptance_criteria=["c"], depends_on=[])])
    monkeypatch.setattr(company_graph, "run_agent", lambda *a, **k: out)
    monkeypatch.setattr(company_graph.memory, "recall_block", lambda *a, **k: "")
    company_graph.plan({"project_id": project.id})


def test_the_talk_to_dos_ask_what_the_plan_asks_not_the_standard_interview(s, monkeypatch, korean_owner):
    b = make_business(s, known=KNOWN, name="미영 반찬")
    _plan(s, monkeypatch, b, ASKS)

    for card in (t.card for t in _talks(b.id).values()):
        assert card["questions"] == ASKS
        points = " ".join(card["talking_points"])
        assert "겪은 일" not in points and "파는 게 아니라" not in points, "no discovery script against a sales ask"


def test_with_no_ask_in_the_plan_the_talk_keeps_the_standard_questions(s, monkeypatch, korean_owner):
    b = make_business(s, known=KNOWN, name="미영 반찬")
    _plan(s, monkeypatch, b, [])
    assert all(t.card["questions"] and t.card["questions"] != ASKS for t in _talks(b.id).values())


def test_a_later_plan_does_not_rewrite_the_asks_the_talks_carry(s, monkeypatch, korean_owner):
    b = make_business(s, known=KNOWN, name="미영 반찬")
    _plan(s, monkeypatch, b, ASKS)
    _plan(s, monkeypatch, b, ["다른 질문이에요?"])
    assert all(t.card["questions"] == ASKS for t in _talks(b.id).values())


# ------------------------------------------------------------------ item 2: the report promises nothing unqueued

SAMPLE = ("대현 님, 하루치 출고 CSV를 한 번 보내 주실 수 있을까요? 이름과 전화번호 열은 지우거나 별표로 가려 주시고, "
          "주소는 시·군까지만 남겨 주시면 충분해요.")


def test_the_report_reads_the_approved_messages_as_they_stand_and_that_nothing_more_is_queued(
        s, monkeypatch, korean_owner):
    _, p = _shop(s, known=[line("정대현 님, 김포 창고 관리자예요.", id="k1", person="정대현")])
    long = SAMPLE + "\n\n" + "출고 파일의 열 이름만 맞으면 돼요. " * 60 + "끝까지 읽어 주셔서 고마워요."
    req = requests.create(s, ask("정대현 님", title="정대현 님께 샘플 파일 부탁", body=long), from_agent="project_manager",
                          project_id=p.id, origin=by_project_manager(s, p.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    seen: dict = {}

    def fake(agent, purpose, prompt, model, **kw):
        seen["prompt"] = prompt
        return FinalReportOut(title="결과", executive_summary="요약", recommendation="GO", report_markdown="본문",
                              validation_plan=["통화"], development_needs=[], kpis=[])

    def keep(sess, proj, kind, title, rel, content):
        art = Artifact(project_id=proj.id, kind=kind, title=title, path=rel, commit_sha="x")
        sess.add(art)
        sess.flush()
        return art

    monkeypatch.setattr(company_graph, "run_agent", fake)
    monkeypatch.setattr(company_graph, "_write_artifact", keep)
    monkeypatch.setattr(company_graph.memory, "index_project", lambda *a, **k: None)
    company_graph.report({"project_id": p.id})

    prompt = seen["prompt"]
    assert "주소는 시·군까지만 남겨 주시면 충분해요" in prompt, "the approved message as it stands"
    assert "끝까지 읽어 주셔서 고마워요." in prompt, "whole, however long"
    assert "nothing it says is queued" in prompt
    assert "never tell the owner to hold" in prompt


# ------------------------------------------------------------------ item 3: no invented past conversation

@pytest.mark.parametrize("opening", ["오랜만이에요. 전에 얘기했던 창고 소개 건으로 연락했어요.",
                                     "지난번에 이야기 나눴던 반찬 예약 건이에요.",
                                     "As we discussed, I'm building a small tool for warehouses.",
                                     "Following up on our conversation last week about the digest."])
def test_a_message_that_claims_an_earlier_talk_goes_back_to_its_author(opening):
    problems = owner_tasks.author_problems([ask("예전 회사 물류팀 동료", body=f"{opening}\n\n{WORDS}")])
    assert len(problems) == 1 and "talked before" in problems[0]


@pytest.mark.parametrize("opening", ["대현 님, 지난주에 말씀하신 이중 출고 건 계속 생각나서 연락드려요.",
                                     "Hi Tom, I'm building a small tool for coffee-gear stores.",
                                     "주문하기 전에 말씀해 주시면 따로 챙겨 둘게요.",
                                     "다음 주에 한 번 이야기 나눈 뒤에 정해도 괜찮아요.",
                                     "위에 말한 대로 사진만 보내 드릴게요.",
                                     "As I mentioned above, the file stays on your machine."])
def test_a_message_without_a_claimed_earlier_talk_is_fine(opening):
    assert owner_tasks.author_problems([ask("정대현 님", body=f"{opening}\n\n{WORDS}")]) == []


# ------------------------------------------------------------------ item 4: a Korean name with a particle

@pytest.mark.parametrize("text", ["김민수님께 드릴 말", "김민수 님과 이야기", "김민수님", "김민수씨에게"])
def test_a_korean_name_with_an_honorific_or_a_particle_names_the_person(s, text):
    b, _ = _shop(s, known=[line("김민수 님, 동네 카페 사장님이에요.", id="k1", person="김민수")])
    assert [x.display_name for x in people.named(s, b.id, text)] == ["김민수"]


@pytest.mark.parametrize("text", ["이정희님께", "박은주미님께", "최수진영 님", "이하나에게", "이하나"])
def test_a_longer_name_that_starts_with_a_known_one_is_someone_else(s, text):
    b, _ = _shop(s, known=[line("이정 님", id="k1", person="이정"), line("박은주 님", id="k2", person="박은주"),
                           line("최수진 님", id="k3", person="최수진"), line("이하 님", id="k4", person="이하")])
    assert people.named(s, b.id, text) == []


def test_a_message_to_a_name_with_a_particle_lands_on_that_person_s_to_do(s):
    b, p = _shop(s)
    req = requests.create(s, ask("박은주님께"), from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()
    assert _talks(b.id)["박은주"].card["handout"]["to"] == "박은주님께"


# ------------------------------------------------------------------ item 5: the card names the to-do it goes on

def test_the_card_says_the_message_goes_on_the_person_s_open_to_do(s):
    _, p = _shop(s)
    req = requests.create(s, ask("박은주 님"), from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    says = requests.outcome(s, req, "en").says
    assert "Adds a to-do" not in says
    assert "박은주 님과 이야기하기" in says or "Talk to 박은주" in says


# ------------------------------------------------------------------ item 6: a revision to someone else

def test_a_revision_addressed_to_someone_else_leaves_the_first_person_s_to_do_as_approved(s):
    b, p = _shop(s)
    first = requests.create(s, ask("박은주 님", title="박은주 님께 드릴 말"), from_agent="marketing", project_id=p.id,
                            origin=by_project_manager(s, p.id))
    approve(s, first, confirm_text=f"REQ-{first.id}")
    s.commit()
    again = requests.create(s, ask("최수진 님", title="최수진 님께 드릴 말", body="최수진 님, " + WORDS,
                                   replaces=f"REQ-{first.id}"), from_agent="marketing", project_id=p.id,
                                   origin=by_project_manager(s, p.id))
    says = requests.outcome(s, again, "en").says
    assert "박은주" not in says, "the card does not say the first person's to-do changes"
    approve(s, again, confirm_text=f"REQ-{again.id}")
    s.commit()

    talks = _talks(b.id)
    assert talks["박은주"].card["handout"]["body"] == WORDS and talks["박은주"].request_id == first.id
    assert talks["최수진"].card["handout"]["body"].startswith("최수진 님") and talks["최수진"].request_id == again.id


def test_a_revision_to_the_same_person_still_changes_their_to_do(s):
    b, p = _shop(s)
    first = requests.create(s, ask("박은주 님", title="박은주 님께 드릴 말"), from_agent="marketing", project_id=p.id,
                            origin=by_project_manager(s, p.id))
    approve(s, first, confirm_text=f"REQ-{first.id}")
    s.commit()
    again = requests.create(s, ask("박은주 님", title="박은주 님께 드릴 말 고친 안", body="은주 님, " + WORDS,
                                   replaces=f"REQ-{first.id}"), from_agent="marketing", project_id=p.id,
                                   origin=by_project_manager(s, p.id))
    approve(s, again, confirm_text=f"REQ-{again.id}")
    s.commit()
    talk = _talks(b.id)["박은주"]
    assert talk.request_id == again.id and talk.card["handout"]["body"].startswith("은주 님")


# ------------------------------------------------------------------ item 7: 님 after a full name takes a space

def test_a_talk_to_do_spaces_the_honorific_after_the_name(s):
    b, _ = _shop(s)
    assert sorted(t.title for t in _talks(b.id).values()) == ["박은주 님과 이야기하기", "이정희 님과 이야기하기",
                                                                "최수진 님과 이야기하기"]
