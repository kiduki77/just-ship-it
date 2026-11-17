"""One to-do per person, and the CEO reads that list (persona round 4, Sam, B-17).

The plan named Priya, Tom and Lena. "Ask to Lena: one week of support emails" was folded into "Ask to Tom: … (upload
route)" (REQ-95) as "[marketing asked the same] Lena is one of the three…", so Lena never got an ask. Priya had both
"Talk to Priya" (OT-36) and "Ask message to Priya" (OT-40). And the CEO's round review guessed "Lena's ask was drafted
but is not among the decisions waiting, so whether it was raised is unknown": it never saw the to-dos about people."""
from people_kit import line, make_business
from sqlalchemy import select

from justshipit.contracts.requests import AgentRequestDraft, Handout, RequestType, RiskLevel
from justshipit.db.models import OwnerTask
from justshipit.db.session import session_scope
from justshipit.orchestration import context as ctx
from justshipit.services import owner_tasks, people_tasks, projects, requests
from owner_task_kit import approve
from ask_kit import contract, by_project_manager

KNOWN = [line("Priya, who runs a candle shop.", id="k1", person="Priya"),
         line("Tom, who runs a coffee-gear store.", id="k2", person="Tom"),
         line("Lena, who runs a kids' clothing store.", id="k3", person="Lena")]


def ask(to: str, title: str) -> AgentRequestDraft:
    return AgentRequestDraft(
        type=RequestType.APPROVAL, title=title, summary=f"Ask {to} for one week of support emails.",
        rationale=f"{to} is one of the three first users.", options=[], recommended_option=None,
        action_kind="external.interview_outreach", estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract(),
        handout=Handout(kind="message", to=to, body=f"Hi {to},\n\nCould you send me one week of your support emails?"))


def _business_project(s):
    b = make_business(s, known=KNOWN, name="Monday digest")
    p = projects.create(s, "8 stores reading the digest", name="Digest", budget_usd=40, start=False, business=b)
    people_tasks.first_talks(s, b.id, "", lang="en")
    s.commit()
    return b, p


def _open(business_id: int) -> list[OwnerTask]:
    with session_scope() as x:
        rows = list(x.scalars(select(OwnerTask).where(OwnerTask.business_id == business_id,
                                                      OwnerTask.status == owner_tasks.OPEN).order_by(OwnerTask.id)))
        x.expunge_all()
        return rows


def test_asks_to_two_people_are_two_asks_however_alike_their_titles(s):
    _, p = _business_project(s)
    tom = requests.create(s, ask("Tom", "Ask to Tom: one week of support emails (upload route)"),
                          from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    lena = requests.create(s, ask("Lena", "Ask to Lena: one week of support emails"), from_agent="marketing",
                           project_id=p.id, origin=by_project_manager(s, p.id))
    assert lena.id != tom.id
    assert "Lena" not in tom.rationale


def test_the_same_ask_raised_again_names_who_asked_in_the_owner_s_words(s):
    _, p = _business_project(s)
    first = requests.create(s, ask("Tom", "Ask to Tom: one week of support emails"), from_agent="marketing",
                            project_id=p.id, origin=by_project_manager(s, p.id))
    again = requests.create(s, ask("Tom", "Ask to Tom: one week of support emails"), from_agent="research",
                            project_id=p.id, origin=by_project_manager(s, p.id))
    assert again.id == first.id
    assert "[Market research asked the same]" in first.rationale and "[research" not in first.rationale


def test_an_approved_message_to_someone_with_a_talk_to_do_goes_on_that_to_do(s):
    b, p = _business_project(s)
    req = requests.create(s, ask("Priya", "Ask message to Priya: one week of support emails"),
                          from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()

    about_priya = [t for t in _open(b.id) if "Priya" in t.title]
    assert [t.title for t in about_priya] == ["Talk to Priya"], "one to-do for Priya"
    assert about_priya[0].card["handout"]["body"].startswith("Hi Priya")
    assert sorted(t.title for t in _open(b.id)) == ["Talk to Lena", "Talk to Priya", "Talk to Tom"]
    with session_scope() as x:
        assert owner_tasks.create_for_request(x, x.get(type(req), req.id)).id == about_priya[0].id, "asking again"


def test_a_message_carried_onto_a_person_s_to_do_is_still_what_the_project_promised(s):
    from justshipit.engineering import usable

    b, p = _business_project(s)
    req = requests.create(s, ask("Priya", "Ask message to Priya: one week of support emails"),
                          from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()

    assert [t.project_id for t in _open(b.id) if t.title == "Talk to Priya"] == [None], "a to-do about a person"
    with session_scope() as x:
        assert "To Priya:\nHi Priya" in usable.promised(x, p.id)


def test_every_person_the_plan_names_gets_their_own_ask_on_their_own_to_do(s):
    b, p = _business_project(s)
    for name, title in (("Tom", "Ask to Tom: one week of support emails (upload route)"),
                        ("Lena", "Ask to Lena: one week of support emails")):
        req = requests.create(s, ask(name, title), from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
        approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()

    carried = {t.title: (t.card.get("handout") or {}).get("to") for t in _open(b.id)}
    assert carried == {"Talk to Priya": None, "Talk to Tom": "Tom", "Talk to Lena": "Lena"}


def test_the_project_manager_reads_the_to_dos_about_people_with_the_project(s):
    b, p = _business_project(s)
    req = requests.create(s, ask("Lena", "Ask to Lena: one week of support emails"), from_agent="marketing",
                          project_id=p.id, origin=by_project_manager(s, p.id))
    approve(s, req, confirm_text=f"REQ-{req.id}")
    s.commit()

    text = ctx.project_brief(s, p)
    lena = next(t for t in _open(b.id) if t.title == "Talk to Lena")
    assert f"[OT-{lena.id} · open] Talk to Lena (REQ-{req.id}): not done yet" in text
    assert "Talk to Priya" in text and "Talk to Tom" in text
