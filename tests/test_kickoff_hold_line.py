"""The kickoff holds the line once (rule 16), and the plan records how it ended. The words the owner turned a test down
with stay out of the plan while the CEO's one proposal back waits for an answer. A yes puts the smaller step in the
plan and never the refusal, on that turn or later. A no leaves the owner's choice for the CEO to write. Round 4 (Sam,
B-17): "You see it as a tool for every Shopify store." stayed a said line beside the confirmed "Every Shopify store
comes later", and the spec built from the plan listed one as left out and the other as included."""
import pytest
from justshipit.services import kickoff
from test_kickoff import _settled_jobs, api, project_manager, lines, open_session, out, play, reply, view  # noqa: F401

pytestmark = pytest.mark.usefixtures("strengths_answered")

REFUSAL = "No. Let's just build it, it's for every Shopify store. I don't want to hand-make digests, I want the tool."
EVERY_STORE = ("customer_problem", "You see it as a tool for every Shopify store.", "said", "it's for every Shopify store")
THE_TOOL = ("path", "You want to build the tool itself, not make digests by hand.", "said",
            "I don't want to hand-make digests, I want the tool.")
FIRST_THREE = ("customer_problem", "The first users are Priya, Tom and Lena. Every Shopify store comes later.", "guess")
SMALL_STEP = ("path", "Each of the three forwards one week of support emails before the inbox connection is built.", "guess")


def refused(api, project_manager) -> int:
    """Turn 1 proposes doing the first digests by hand; the owner turns it down."""
    sid = open_session(api, name="Monday digest", one_liner="Turn a small store's support emails into a Monday summary")
    play(api, project_manager, sid, out("I'd make the first digests by hand. Is that right?", kind="proposal", section="path",
                            patch=[("path", "Make the first digests by hand from forwarded emails.", "guess")]),
         answer={"text": REFUSAL})
    return sid


def hold_the_line(project_manager, sid: int, n: int = 2, *, patch=(EVERY_STORE, THE_TOOL, FIRST_THREE, SMALL_STEP)) -> None:
    project_manager.says(out("Building first takes about 6 weeks before anyone tells us. The smallest step: each forwards a week "
                 "of emails first. Is that right?", kind="proposal", section="path", patch=patch)
             .model_copy(update={"holds_line": True}))
    kickoff.run_turn(sid, n)


def texts(v: dict, key: str) -> list[tuple[str, str]]:
    return [(x["provenance"], x["text"]) for x in lines(v, key)]


def test_the_refusal_is_no_plan_line_while_the_project_manager_holds_the_line(api, project_manager):
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)

    v = view(api, sid)
    assert texts(v, "customer_problem") == [("guess", FIRST_THREE[1])]
    assert texts(v, "path") == [("guess", "Make the first digests by hand from forwarded emails."), ("guess", SMALL_STEP[1])]
    assert [(n["what"], n["text"]) for n in v["turns"][1]["patch_notes"] if n["what"] == "held"] == [
        ("held", EVERY_STORE[1]), ("held", THE_TOOL[1])]


def test_a_yes_keeps_the_step_and_never_the_refusal(api, project_manager):
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)
    reply(api, sid, choice="yes")
    project_manager.says(out("Who are the three?", section="known_people", patch=[EVERY_STORE, THE_TOOL]))
    kickoff.run_turn(sid, 3)

    v = view(api, sid)
    assert texts(v, "customer_problem") == [("confirmed", FIRST_THREE[1])]
    assert ("confirmed", SMALL_STEP[1]) in texts(v, "path") and THE_TOOL[1] not in [t for _, t in texts(v, "path")]
    assert [n["what"] for n in v["turns"][2]["patch_notes"]] == ["held", "held"]


def test_a_no_leaves_the_owners_choice_for_the_project_manager_to_write(api, project_manager):
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)
    reply(api, sid, text="No, build the full tool first.")
    risk = ("top_risks", "You chose to build before any store tried a digest. First sign: no store reads the first "
                         "two. Response: ask Priya why.", "guess")
    project_manager.says(out("Then we build first.", section="red_lines", patch=[EVERY_STORE, THE_TOOL, risk]))
    kickoff.run_turn(sid, 3)

    v = view(api, sid)
    assert ("said", EVERY_STORE[1]) in texts(v, "customer_problem")
    assert ("said", THE_TOOL[1]) in texts(v, "path")
    assert texts(v, "top_risks") == [("guess", risk[1])]


def test_the_line_is_held_once_and_a_correction_after_it_stays_the_owners(api, project_manager):
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)
    reply(api, sid, choice="yes")
    play(api, project_manager, sid, out("Here's the plan so far. Is that right?", kind="proposal", section="customer_problem"),
         answer={"text": "Not quite: Lena isn't on Gmail, she uses Shopify Inbox."})
    hold_the_line(project_manager, sid, 4, patch=[("customer_problem", "Lena uses Shopify Inbox, not Gmail.", "said",
                                       "Lena isn't on Gmail, she uses Shopify Inbox")])

    v = view(api, sid)
    assert ("said", "Lena uses Shopify Inbox, not Gmail.") in texts(v, "customer_problem")
    assert not [n for n in v["turns"][3]["patch_notes"] if n["what"] == "held"]


def test_only_the_refusal_is_held_and_the_owners_other_words_in_that_answer_stay(api, project_manager):
    """Review M2: the answer that turns a step down can say more. A red line in it is the owner's and stays."""
    sid = open_session(api, name="Monday digest", one_liner="Turn a small store's support emails into a Monday summary")
    play(api, project_manager, sid, out("I'd make the first digests by hand. Is that right?", kind="proposal", section="path",
                            patch=[("path", "Make the first digests by hand from forwarded emails.", "guess")]),
         answer={"text": REFUSAL + " Also, never run paid ads."})
    red_line = ("red_lines", "Never run paid ads.", "said", "never run paid ads.")
    hold_the_line(project_manager, sid, patch=(EVERY_STORE, red_line, SMALL_STEP))

    v = view(api, sid)
    assert texts(v, "red_lines") == [("said", "Never run paid ads.")]
    assert texts(v, "customer_problem") == []


def progress(prompt: str) -> str:
    return prompt.split("## Progress", 1)[1].split("## Plan draft", 1)[0]


def test_the_answer_to_the_hold_is_read_for_any_no_left_in_it(api, project_manager):
    """Round 6 (미영, B-21): "세 분한테 … 이번 주에 할 수 있어요. 그래도 결국은 모든 손님이 쓸 수 있게 만들어 주세요." The
    CEO read it as a yes, and the "everyone" it still refused was no risk line. The turn after the hold tells the CEO the
    owner's words answer it and that a no kept beside a yes is one top_risks line."""
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)
    reply(api, sid, text="Fine, I'll ask Priya, Tom and Lena this week. But in the end make it for every Shopify store.")
    project_manager.says(out("What hours can you give it?", section="owner_time"), out("Anything off limits?", section="red_lines"))
    kickoff.run_turn(sid, 3)
    reply(api, sid, text="Two evenings a week.")
    kickoff.run_turn(sid, 4)

    after_hold, later = progress(project_manager.prompts[-2]), progress(project_manager.prompts[-1])
    assert "[2]" in after_hold and "top_risks" in after_hold
    assert "top_risks" not in later


def test_a_plain_yes_to_the_hold_asks_for_no_risk(api, project_manager):
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)
    reply(api, sid, choice="yes")
    project_manager.says(out("Who are the three?", section="known_people"))
    kickoff.run_turn(sid, 3)

    assert "top_risks" not in progress(project_manager.prompts[-1])


def test_a_typed_no_to_the_hold_asks_for_the_risk(api, project_manager):
    sid = refused(api, project_manager)
    hold_the_line(project_manager, sid)
    reply(api, sid, text="No, build the full tool first.")
    project_manager.says(out("Then we build first.", section="red_lines"))
    kickoff.run_turn(sid, 3)

    assert "top_risks" in progress(project_manager.prompts[-1])
