"""What approving a decision says it does, for each kind of to-do it becomes, and the second check in plain words.
A budget card said "Adds a to-do that you do yourself" with nothing about the project's budget; a post and a payment
read the same."""
import pytest
from justshipit.i18n import msg
from justshipit.services import requests
from owner_task_kit import draft, post_card
from ask_kit import by_project_manager


def says(s, project, **kw) -> str:
    req = requests.create(s, draft(**kw), from_agent="finance", project_id=project.id, origin=by_project_manager(s, project.id))
    return requests.outcome(s, req, "en").says


@pytest.mark.parametrize("kind, key", [
    ("external.social_post", "request.effect.owner_task.post_outline"),
    ("external.landing_publish", "request.effect.owner_task.publish"),
    ("external.ads_run", "request.effect.owner_task.spend"),
    ("data.purchase", "request.effect.owner_task.spend"),
    ("budget.spend", "request.effect.owner_task.budget"),
    ("external.other", "request.effect.owner_task"),
])
def test_each_kind_of_to_do_says_what_the_owner_does_and_what_the_company_does_not(s, project, kind, key):
    text = says(s, project, action_kind=kind)
    assert msg(key, "en") in text
    assert "company" in text.lower() or "budget" in text.lower()


def test_approving_a_budget_card_says_it_does_not_raise_the_budget_and_where_that_is_done(s, project):
    text = says(s, project, action_kind="budget.spend", estimated_cost_usd=4.0)
    assert "doesn't raise the project's budget" in text and "settings" in text
    assert "Adds a to-do that you do yourself" not in text


def test_a_post_the_owner_writes_does_not_say_it_comes_ready_to_copy(s, project):
    """Approving a Show HN card said "you write it" on the card and "Adds a to-do with the post ready to copy" as what
    approving does. Where the channel takes only points (outline), the owner writes the post; where it takes a draft,
    the post is ready to copy."""
    outline = says(s, project, action_kind="external.social_post")
    written = says(s, project, action_kind="external.social_post", post_card=post_card("hashnode"))
    assert "ready to copy" not in outline and "write" in outline
    assert msg("request.effect.owner_task.post", "en") in written and "ready to copy" in written


def test_a_post_does_not_say_the_company_pays_or_publishes(s, project):
    post = says(s, project, action_kind="external.social_post")
    assert "post it yourself" in post and "never posts for you" in post
    assert "pay" not in post


def test_the_second_check_asks_for_the_decision_number_in_plain_words(s, project):
    text = msg("request.typed_confirm", "en", ref="REQ-7")
    assert text.startswith("This approval is hard to take back") and text.endswith("REQ-7, in the confirmation box.")
    assert "exactly as shown" not in text
