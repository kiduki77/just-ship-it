"""The CEO holds the line on talking to users, once (persona round 3). Daniel's kickoff proposed a hand test with "you
and 3 people like you"; the founder said "just build it, for everyone" and the CEO answered "Got it. We build the app
first, and it's open to everyone", never asked who again, and the plan read "build the app now and skip testing by
hand". Maya's business name "Maya - agency status reports" made the CEO call the product Maya. Maya's first LinkedIn
message to people she had worked with opened with "Our AI provider may keep a copy up to 30 days".

These pin what each agent is told, read the way the agent gets it."""
from justshipit.agents.executor import system_prompt
from justshipit.contracts.kickoff import BriefPatchLine
from justshipit.contracts.planning import ProjectManagerPlanOut
from justshipit.services import kickoff


def test_the_kickoff_project_manager_states_the_cost_once_offers_the_smallest_step_and_records_a_no_as_a_risk():
    text = kickoff._system("en")
    rule = text[text.index("**Hold the line once on real people.**"):]
    rule = rule[:rule.index("\n## ")]
    assert "do not just agree" in rule and "the cost in one sentence" in rule
    assert "talk to 3 people like you this week, by hand, then we build" in rule, "the smallest step, concrete"
    assert 'For "everyone", ask once more who would use it first' in rule
    assert "Do this once, then respect the answer and never raise it again" in rule, "not preachy or repetitive"
    assert "one `top_risks` `guess` line" in rule and "The skipped step is never a `path` line" in rule
    assert "once in the whole chat, whichever refusal comes first" in rule, "two triggers, still one nudge"
    assert "When the product already exists (rule 15), there is no build to hold back" in rule
    assert "If they already said they know no one, do not ask again" in rule, "rule 6's no stays a no"


def test_the_kickoff_project_manager_never_names_the_product_after_a_person_and_carries_a_correction_everywhere():
    text = kickoff._system("en")
    assert "Call the product only by a name the owner gives it" in text
    assert "rewrite in that turn every line that still carries the old one" in text


def test_the_kickoff_project_manager_names_each_known_person_in_a_field_of_its_own():
    assert "person" in BriefPatchLine.model_json_schema()["properties"]
    assert 'Write one line per person, with that person\'s name in `person`' in kickoff._system("en")


def test_the_planning_project_manager_puts_the_talks_and_a_stop_rule_before_any_launch_work():
    assert "stop_rule" in ProjectManagerPlanOut.model_json_schema()["properties"]
    text = system_prompt("project_manager")
    assert "Talking to real people comes first" in text and "write `stop_rule`" in text
    assert "round 1 plans no launch-channel work" in text
    assert "or the plan's `top_risks` records that the owner chose to skip these talks" in text, \
        "an owner who declined the talks gets the plan they chose (kickoff rule 16)"


def test_a_message_to_people_the_owner_knows_opens_with_the_ask_not_legal_text():
    assert "never with legal or data-handling text" in system_prompt("marketing")
