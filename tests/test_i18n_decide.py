"""The decide lane's server words follow the owner's language: English output has no Hangul, Korean output reads like
a Korean founder wrote it (docs/KOREAN-STYLE.md). Text an agent or the owner wrote stays as written, so these feed
only the server's own sentences."""
import re

import pytest
from justshipit.contracts.owner_text import one_line
from justshipit.contracts.requests import HumanDecision, HumanResponse, RequestType
from justshipit.i18n import LANGS
from justshipit.i18n.said import say
from justshipit.services import (
    channels,
    copy_rules,
    door_rules,
    evidence,
    owner_quote,
    owner_tasks,
    policy,
    requests,
)
from test_decision_card import card, draft
from test_korean_style import smells_in
from ask_kit import by_project_manager

HANGUL = re.compile("[가-힣]")


def reads_in(text: str, lang: str) -> None:
    if lang == "en":
        assert not HANGUL.search(text), text
    else:
        assert HANGUL.search(text) and smells_in(text) == set(), text


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("kind,type_", [
    ("external.email_send", RequestType.APPROVAL), ("external.social_post", RequestType.APPROVAL),
    ("external.other", RequestType.APPROVAL), (None, RequestType.QUESTION), (None, RequestType.PROPOSAL),
])
def test_what_approving_does_and_how_to_take_it_back_are_said_in_the_owners_language(s, project, lang, kind, type_):
    from owner_task_kit import LANDING_CARD
    extra = {"post_card": LANDING_CARD} if kind == "external.social_post" else {}
    req = requests.create(s, draft(type=type_, action_kind=kind, card=card(), **extra), from_agent="marketing",
                          project_id=project.id, origin=by_project_manager(s, project.id))
    s.commit()

    out = requests.report(s, req, lang)

    reads_in(out["on_approve"], lang)
    for text in (out["owner_does"], (out["undo"] or {}).get("says"), (out["undo"] or {}).get("how")):
        if text and not text.startswith(card().owner_does):
            reads_in(text, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_a_refused_decision_is_worded_in_the_owners_language(s, project, lang):
    req = requests.create(s, draft(card=card()), from_agent="marketing", project_id=project.id,
                          origin=by_project_manager(s, project.id))
    s.commit()
    with pytest.raises(requests.ResponseRejected) as stale:
        requests.respond(s, req.id, HumanResponse(decision=HumanDecision.APPROVE, revision=revision_before(req)))
    with pytest.raises(requests.ResponseRejected) as missing:
        requests.respond(s, 99_999_999, HumanResponse(decision=HumanDecision.APPROVE))

    for exc in (stale.value, missing.value):
        reads_in(exc.words(lang), lang)
    assert str(stale.value) == stale.value.words("en"), "a log line or the CLI reads the English"


def revision_before(req) -> int:
    return requests.revision(req) - 1


@pytest.mark.parametrize("lang", LANGS)
def test_an_owner_to_do_and_its_refusals_read_in_the_owners_language(s, lang):
    view = {"kind": owner_tasks.kind_label("post", lang), "status": owner_tasks.status_label("OPEN", lang),
            "check": owner_tasks.fixed_check(lang)}
    with pytest.raises(owner_tasks.OwnerTaskError) as missing:
        owner_tasks.done(s, 99_999_999)

    for text in (*view.values(), missing.value.words(lang)):
        reads_in(text, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_the_stock_decision_rules_and_a_refused_rule_read_in_the_owners_language(lang):
    notes = {r["note"] for r in door_rules.default_rules(lang)}
    problem = policy.door_rule_problem({"action": "no.such_kind", "decider": "project_manager"})

    for text in (*notes, say(problem, lang)):
        reads_in(text, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_a_refused_post_reads_in_the_owners_language(lang):
    asked = channels.forbidden_reason(["Please upvote this if it helps!"])
    link = channels.link_problem(channels.registry()["reddit"], "https://example.com/post")
    copy = copy_rules.public_copy_problem("I tried every dog walker in town and quit them all.")

    assert asked and link and copy
    for text in (say(asked, lang), say(link, lang), say(copy[0], lang)):
        reads_in(text, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_the_evidence_mix_and_a_quote_that_cannot_back_a_line_read_in_the_owners_language(lang):
    mixed = evidence.Mix({g: n for g, n in zip(evidence.Grade, (1, 2, 3))})
    empty = evidence.Mix(dict.fromkeys(evidence.Grade, 0))
    short = owner_quote.problem(owner_quote.norm("hi"), "Dog walks", gate=False, said="hi")
    numbers = owner_quote.problem(owner_quote.norm("walks every morning at seven"), "Dog walks 30 times a month",
                                  gate=True, said="walks every morning at seven")

    assert short and numbers
    for text in (mixed.line(lang), empty.line(lang), say(short, lang), say(numbers, lang)):
        reads_in(text, lang)
    assert "6 pieces of evidence" in mixed.line("en") and "1 piece of evidence" in evidence.Mix(
        {evidence.Grade.FIRM: 1, evidence.Grade.SOURCED: 0, evidence.Grade.ASSUMED: 0}).line("en")


@pytest.mark.parametrize(("text", "plain"), [
    ("Three open_questions remain (plan l-972536)", "Three open questions remain"),
    ("open_questions의 자문 후보 7개 (지시서 l-972536)", "미해결 질문의 자문 후보 7개"),
    ("The verdict is CONDITIONAL-GO (REQ-25 follow-up)", "The verdict is go, with conditions"),
])
def test_a_field_name_reads_in_the_language_of_the_text_it_sits_in(text, plain):
    assert one_line(text, 120) == plain
