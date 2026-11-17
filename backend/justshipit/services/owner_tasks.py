"""Owner tasks: what only the owner can do, and the record that says whether it was done.

Approving a request for an action with no connector (a post, a publish, a payment) proves nothing happened. The
approval makes one task. The company may say it did the thing only when that task is DONE, and the result the owner
filled in (a link, numbers, a note) is what it may quote.

Status moves along `TRANSITIONS`, a table of edges and who may take them. Nothing but the owner's console action takes
one, so the company cannot mark its own homework.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import unquote_plus

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from justshipit.contracts import notify as notices
from justshipit.contracts import owner_text
from justshipit.contracts.post_card import PostCardDraft, PostWhat
from justshipit.contracts.requests import AgentRequestDraft, Handout, RequestType
from justshipit.db.models import OWNER_TASK_KINDS, ActionExecution, AgentRequest, Business, OwnerTask, Project
from justshipit.i18n import DEFAULT, LANGS, Lang, msg
from justshipit.i18n.match import handout as handout_words
from justshipit.i18n.match.copy_rules import TITLE_WORDS
from justshipit.i18n.said import Refusal, Said, Text, said, say
from justshipit.services import (activity, app_settings, channels, copy_rules, gate, launch_gate, policy, profile,
                                 queue, todo_steps, tracking)
from justshipit.services.fence import fenced
from justshipit.services.profile import vocab
from justshipit.services.verdicts import declines

OPEN, DONE, SKIPPED = "OPEN", "DONE", "SKIPPED"

TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    (OPEN, DONE): frozenset({"owner"}),
    (OPEN, SKIPPED): frozenset({"owner"}),
    (DONE, OPEN): frozenset({"owner"}),
    (SKIPPED, OPEN): frozenset({"owner"}),
}

# The actions a person carries out, with the kind of task each becomes. Every kind here is one the CEO may never
# decide (policy.is_irreversible), and none has a connector.
ACTION_TASK_KIND: dict[str, str] = {
    "external.social_post": "post",
    "external.landing_publish": "publish",
    "dev.deploy_production": "publish",
    "external.ads_run": "spend",
    "budget.spend": "spend",
    "data.purchase": "spend",
    "external.contract_sign": "other",
    "external.legal_consult": "other",
    "external.official_inquiry": "other",
    "external.interview_outreach": "other",
    "external.other": "other",
    "owner.check": "check",
}
POST_CARD_KINDS = ("external.social_post", "external.landing_publish", "external.other")
OUTLINE_ITEM_MAX = 120
MAX_LINK = 2000
MAX_NOTE = 2000
CHECK = "check"

KINDS = OWNER_TASK_KINDS
STATUSES = (OPEN, DONE, SKIPPED)


def fixed_check(lang: Lang) -> str:
    """The line every post card's checklist ends with: the owner is the last check for asks this code cannot catch."""
    return msg("post.fixed_check", lang)


def kind_label(kind: str, lang: Lang) -> str:
    return msg(f"owner_task.kind.{kind}", lang)


# Labels for a to-do whose kind would misname it: running a merged build on the owner's own computer comes from the
# deploy action, a `publish`, yet nothing is published. Its card names the label it reads under (`card["label"]`).
LABELS = ("run",)


def shown_kind(task: OwnerTask) -> str:
    """The kind a to-do is labelled as: its card's label when it has one, else its kind."""
    label = (task.card or {}).get("label")
    return label if label in LABELS else task.kind


def status_label(status: str, lang: Lang) -> str:
    return msg(f"owner_task.status.{status}", lang)


# Tasks about a person, and the thread task. Every project prompt lists the business's latest ones (`_people_lines`),
# so a review reads whether each person the plan names was reached; the card's contact details never reach a prompt.
PERSON_KINDS = ("contact", "onboard", "follow_up", "referral")
PEOPLE_KINDS = (*PERSON_KINDS, "participate")


class OwnerTaskError(Refusal):
    """A refusal the console shows, worded by the route in the owner's language."""


class OwnerTaskNotFound(OwnerTaskError):
    pass


class InvalidQuery(OwnerTaskError):
    """A request the console made wrongly (an unknown status), as opposed to a refusal of a valid one."""


class CardRefused(OwnerTaskError):
    """A card the server will not keep. `rule` names which check fired, for the activity log."""

    def __init__(self, what: Text, rule: str):
        super().__init__(what)
        self.rule = rule


def display_id(task_id: int) -> str:
    return f"OT-{task_id}"


def kind_for(action_kind: str | None) -> str | None:
    return ACTION_TASK_KIND.get(action_kind or "")


def task_kind(req: AgentRequest) -> str | None:
    """The kind of to-do approving `req` makes: its action's, or a payment when money goes out on an action the
    company has no rule for (an agent's `spend.purchase` collapsed to `internal.other`). The company pays for nothing
    by hand, so that payment is the owner's. A question's cost is no payment: answering it records the answer and pays
    for nothing (`requests.respond`)."""
    if (kind := kind_for(req.action_kind)) is not None:
        return kind
    asks = req.type == RequestType.QUESTION.value
    return "spend" if not asks and policy.no_rule_spend(req.action_kind, req.estimated_cost_usd) else None


_PAY_WHERE = ("where", "vendor", "merchant", "store", "provider", "site", "url")


def _payment(req: AgentRequest) -> dict:
    """What the owner pays and where, from the decision: its estimated cost, and the place the action's payload names,
    if it names one (None when the decision doesn't say)."""
    payload = (req.action_params or {}).get("payload")
    named = (payload.get(k) for k in _PAY_WHERE) if isinstance(payload, dict) else ()
    where = next((owner_text.clip(" ".join(v.split()), 200) for v in named if isinstance(v, str) and v.strip()), None)
    return {"amount_usd": req.estimated_cost_usd, "where": where, "request": f"REQ-{req.id}"}


# ------------------------------------------------------------------ the post card

def _texts(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for v in node.values() for t in _texts(v)]
    if isinstance(node, list):
        return [t for v in node for t in _texts(v)]
    return []


def card_required(action_kind: str | None) -> bool:
    """Whether a request for this action must carry a post card: anything that makes a post task, and a landing page
    going public. Without one nothing would check what the owner is about to post."""
    return kind_for(action_kind) == "post" or action_kind == "external.landing_publish"


def _request_texts(draft: AgentRequestDraft) -> list[str]:
    """Everything of the request the owner reads, except the post card, which `build_post_card` reads."""
    own = [draft.title, draft.summary, draft.rationale, draft.recommended_option or "", draft.action_payload_json or "",
           *draft.options, *((draft.handout.to or "", draft.handout.body) if draft.handout else ())]
    return own + _texts(draft.card.model_dump(mode="json") if draft.card else {})


_SENTENCE_BREAK = re.compile(r"[.!?。！？]+\s+\S")
_SENTENCE_END = re.compile(r"[.!?。！？]+(?=\s|$)")
_HEADLINE = re.compile(r"^\s*(?:show\s+hn|" + "|".join(rf"{w}\s*:" for w in TITLE_WORDS) + r"|[\"'“”‘’「」『』])",
                       re.IGNORECASE)


def point_problem(item: str) -> tuple[Said, str] | None:
    """Why an item is a sentence to post and not a short point, as (the reason, the rule that fired), or None."""
    if len(item) > OUTLINE_ITEM_MAX:
        return said("post.point.too_long", n=OUTLINE_ITEM_MAX), "outline_too_long"
    if _SENTENCE_BREAK.search(item) or len(_SENTENCE_END.findall(item)) > 1:
        return said("post.point.many_sentences"), "outline_many_sentences"
    if _HEADLINE.match(item):
        return said("post.point.headline"), "outline_headline"
    return None


def _outline_problem(channel: channels.Channel, where: str, item: str) -> CardRefused | None:
    """Why one item of an outline card is a sentence to post and not a point, or None. Outline channels forbid
    generated and AI-edited text, so an item that reads as prose or as a headline is refused, never trimmed."""
    if found := point_problem(item):
        reason, rule = found
        return CardRefused(said("post.refuse.outline_item", channel=channel.names, where=said(f"post.item.{where}"),
                                reason=reason, item=item[:30]), rule)
    return None


def _outline_items(what: PostWhat) -> list[tuple[str, str]]:
    return ([("fact", f) for f in what.facts] + [("checklist", c) for c in what.checklist]
            + [("question", q.q) for q in what.expected_questions]
            + [("talking_point", tp) for q in what.expected_questions for tp in q.talking_points])


def _rules_refusal(channel: channels.Channel) -> CardRefused:
    return CardRefused(said("post.refuse.rules_unchecked", channel=channel.names, why=channels.unchecked_said(channel)),
                       "rules_unchecked")


def _own_place(draft: PostCardDraft, own_places: Collection[str], own_page: bool) -> None:
    """Checks a card filed under `other`, which is for the owner's own pages and for no outside place. A card is the
    owner's own when its address is under one of the saved places (`own_places`) or the request publishes a landing page
    (`own_page`) with no address, an address under a saved place or one on a static-hosting domain. An own card may name
    anything in its title. Raises when the card carries another address, or has none and names a place in its title: a
    place whose rules were not read is refused, and a checked one is told to use its own channel, so `other` never
    skips a place's mode or launch gate."""
    url = draft.where_url
    if url and channels.under_places(url, own_places):
        return
    if own_page and (not url or channels.on_static_host(url)):
        return
    if url:
        raise CardRefused(said("post.refuse.landing_place", hosts=", ".join(channels.STATIC_HOSTS)) if own_page
                          else said("post.refuse.own_place", channel=channels.get("other").names), "rules_unchecked")
    if (named := channels.place_in(draft.what.title or "")) is None:
        return
    if not channels.writable(named):
        raise _rules_refusal(named)
    raise CardRefused(said("post.refuse.names_place", channel=named.names, place_key=named.key), "wrong_channel")


def build_post_card(draft: PostCardDraft, gate: launch_gate.Gate | None, lang: Lang, own_places: Collection[str] = (),
                    own_page: bool = False) -> tuple[dict, list[str]]:
    """The card the owner reads, and the fields the server dropped. The rules and the mode come from the registry,
    the place's name and rules in `lang`.
    A place whose rules were not read (`channels.writable`) gets no card: it is refused with rule `rules_unchecked`,
    before every other check. `other` is not a place but the owner's own: its card is kept when `_own_place` finds it
    names no outside place (checked once the address is known to be a usable one), and says so (`own_place`, with no
    outside rules summary). `own_places` and `own_page` are how the caller says what the owner's own places are.
    A channel with a launch gate gets a card only when `gate` says nothing is missing; with no gate to ask (`None`, a
    business that cannot be named) a gated channel is refused, so no caller can skip the check.
    An `outline` channel keeps facts, questions and the checklist and loses any title or body the model wrote; each
    of its items must be a short point, or the card is refused. A `draft` channel's title and body are what the owner
    pastes, so they may hold no invented experience and no internal note (`copy_rules`). Every card ends with the same checklist line, the
    owner's last check for asks this code cannot catch, in `lang`. Raises CardRefused with the rule that fired."""
    channel = channels.get(draft.channel)
    if channel is None:
        raise CardRefused(said("post.refuse.unknown_channel", channel=draft.channel), "unknown_channel")
    if channel.key != "other" and not channels.writable(channel):
        raise _rules_refusal(channel)
    if channel.requires and (lacking := gate(channel) if gate else list(channel.requires)):
        raise CardRefused(said("post.refuse.launch_gate", channel=channel.names,
                               missing=", ".join(launch_gate.gate_label(i, lang=lang) for i in lacking)), "launch_gate")
    if draft.where_url and (problem := channels.where_url_problem(channel, draft.where_url)):
        raise CardRefused(said("post.refuse.where_url", problem=problem), "where_url")
    own = channel.key == "other"
    if own:
        _own_place(draft, own_places, own_page)
    texts = _texts(draft.model_dump(mode="json", exclude={"channel", "where_url"}))
    if why := channels.forbidden_reason([*texts, unquote_plus(draft.where_url or "")]):
        raise CardRefused(why, "forbidden_ask")
    what = draft.what
    stripped: list[str] = []
    checklist = [*what.checklist, fixed_check(lang)]
    if channel.mode == "outline":
        stripped = [f for f in ("title", "body") if getattr(what, f)]
        if not (what.facts or what.checklist or what.expected_questions):
            raise CardRefused(said("post.refuse.outline_empty", channel=channel.names), "outline_empty")
        for where, item in _outline_items(what):
            if refused := _outline_problem(channel, where, item):
                raise refused
        body: dict[str, Any] = {"facts": what.facts, "checklist": checklist,
                                "expected_questions": [q.model_dump(mode="json") for q in what.expected_questions]}
    else:
        if not (what.title and what.title.strip() and what.body and what.body.strip()):
            raise CardRefused(said("post.refuse.draft_empty", channel=channel.names), "draft_empty")
        if found := copy_rules.public_copy_problem(what.title, what.body):
            raise CardRefused(said("post.refuse.draft", channel=channel.names, problem=found[0]), found[1])
        body = {"title": what.title, "body": what.body, "checklist": checklist}
    card = {"channel": channel.key, "channel_name": channel.names[lang], "mode": channel.mode, "where_url": draft.where_url,
            "rules_summary": "" if own else channel.rules[lang], "rules_source": channel.source_url,
            "rules_basis": channel.basis, "when": draft.when.model_dump(mode="json"), "why": draft.why, "what": body, "stripped": stripped,
            **({"own_place": True} if own else {})}
    return card, stripped


def screen(draft: AgentRequestDraft, gate: launch_gate.Gate | None, lang: Lang,
           own_places: Collection[str] = ()) -> tuple[dict, list[str]]:
    """The post card to store for a request, and the fields dropped from it. Every owner-visible text of a request
    that makes an owner task is checked for asks, and a request that must carry a post card is refused without
    one. `gate` is the launch gate of the request's business, and `own_places` the addresses of its saved places. A request
    of a post kind that carries no card is refused when its title names a place whose rules were not read. Raises
    CardRefused."""
    if kind_for(draft.action_kind) is None:
        return {}, []
    if why := channels.forbidden_reason(_request_texts(draft)):
        raise CardRefused(why, "forbidden_ask")
    if draft.post_card is None:
        if card_required(draft.action_kind):
            raise CardRefused(said("post.refuse.card_missing"), "post_card_missing")
        if draft.action_kind in POST_CARD_KINDS and (named := channels.place_in(draft.title)) and not channels.writable(named):
            raise _rules_refusal(named)
        return {}, []
    if draft.action_kind not in POST_CARD_KINDS:
        return {}, []
    card, stripped = build_post_card(draft.post_card, gate, lang, own_places,
                                     own_page=draft.action_kind == "external.landing_publish")
    return {"post_card": card}, stripped


_NON_WORD = re.compile(r"[\W_]+")


def post_key(card: dict) -> str:
    """What makes two post cards the same post: the first fact for an outline channel, the title for a draft one."""
    what = card.get("what") or {}
    facts = what.get("facts") or []
    return _NON_WORD.sub("", ((what.get("title") or (facts[0] if facts else "")) or "").lower())


# ------------------------------------------------------------------ what goes back to the author

def _joined(table: dict[Lang, tuple[str, ...]]) -> re.Pattern[str] | None:
    parts = [f for lang in LANGS for f in table[lang]]
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


_OWNER_FIX, _COMPANY_TEXT = _joined(handout_words.OWNER_FIX), _joined(handout_words.COMPANY_TEXT)
_FORMAL, _POLITE = _joined(handout_words.FORMAL), _joined(handout_words.POLITE)
_FIXED = tuple(w for lang in LANGS for w in handout_words.FIXED[lang])
_SENTENCES = re.compile(r"[.!?。！？\n]+")


def owner_fix_ask(line: str) -> bool:
    """Whether a card line asks the owner to change one value of the company's own text to another ("19:30을 20:00으로
    고친 뒤 올려요"). The company makes that fix itself. A value the owner decides ("가격을 9,900원으로 바꿔요") names
    no text of the company's, so it is not one."""
    return bool(_OWNER_FIX and _OWNER_FIX.search(line) and _COMPANY_TEXT and _COMPANY_TEXT.search(line))


def register_mixed(text: str) -> bool:
    """Whether a message ends some sentences in 합니다체 and others in 해요체 ("오랜만에 연락드립니다. 이준호예요."). Set
    phrases either register uses ("안녕하세요", "감사합니다") do not count."""
    ends = [x.strip(" ,~)\"'”’") for x in _SENTENCES.split(text)]
    ends = [x for x in ends if x and not x.endswith(_FIXED)]
    return (any(_FORMAL.search(x) for x in ends) and any(_POLITE.search(x) for x in ends)) if _FORMAL and _POLITE else False


_BLANK, _PAST_TALK = _joined(handout_words.OWNER_BLANK), _joined(handout_words.PAST_TALK)
_ONE_PERSON, _COMPANY_VOICE = _joined(handout_words.ONE_PERSON), _joined(handout_words.COMPANY_VOICE)
_SENTENCE_GAP = re.compile(r"(?<=[.!?。！？])\s+")


def blanks(text: str) -> list[str]:
    """The blanks a message leaves the owner to fill before sending ("[Your name]", "[hosting company]")."""
    return _BLANK.findall(text) if _BLANK else []


def without_blanks(text: str) -> str:
    """The message with no blank for the owner to fill: each sentence holding one is left out, and a line it empties
    goes with it ("It's [Your name].", the sign-off "[Your name]", "Where it's stored: [hosting company]."). The owner
    sends it from their own account, so who it is from is on it already."""
    if not blanks(text):
        return text
    lines = []
    for line in text.split("\n"):
        kept = " ".join(x for x in _SENTENCE_GAP.split(line) if not blanks(x)) if blanks(line) else line
        if kept.strip() or not blanks(line):
            lines.append(kept)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def stored_handout(handout: Handout) -> dict:
    """The handout as the to-do keeps it: a message without blanks for the owner to fill (`without_blanks`)."""
    out = handout.model_dump(mode="json")
    return {**out, "body": without_blanks(out["body"])} if handout.kind == "message" else out


def voice_mixed(text: str) -> bool:
    """Whether a message speaks as one person (I, my) and as a company (we, our), each at least twice: "I'm testing…"
    then "What we remove… we use Anthropic's Claude API". A single "could we talk?" is the two people, not a switch."""
    if not (_ONE_PERSON and _COMPANY_VOICE):
        return False
    return len(_ONE_PERSON.findall(text)) >= 2 and len(_COMPANY_VOICE.findall(text)) >= 2


def _asks_of(draft: AgentRequestDraft) -> list[str]:
    """The lines of a request that tell the owner what to do: the post card's checklist and the decision card's
    owner line, recommendation and options."""
    lines = list(draft.post_card.what.checklist) if draft.post_card else []
    if card := draft.card:
        lines += [card.owner_does, card.recommendation,
                  *(x for o in card.options for x in (o.summary, *o.risks))]
    return lines


def past_talk(text: str) -> list[str]:
    """Where a message says the owner and the person talked about this before ("전에 얘기했던", "as we discussed")."""
    return [m.group(0) for m in _PAST_TALK.finditer(text)] if _PAST_TALK else []


def author_problems(drafts: list[AgentRequestDraft], *, named: Callable[[str], list[str]] | None = None) -> list[str]:
    """What the author of a task's requests must fix before any reaches the owner, said to the agent: a line asking
    the owner to correct the company's own text, a message or post that mixes registers, a message that leaves the
    owner blanks to fill, switches between I and we, or claims an earlier talk, and one message for several of the
    business's people (`named` names them from its `to`), which reaches none of their to-dos. The work node sends these
    back to the author (`company_graph.work`)."""
    out = []
    for d in drafts:
        if fixes := [x for x in _asks_of(d) if owner_fix_ask(x)]:
            out.append(f"'{d.title}' asks the owner to correct the company's own text ({'; '.join(fixes)}). Make the fix "
                       "yourself and put the corrected text on the card; if the text belongs to another task's output, "
                       "leave the line out and propose a follow-up task to fix it. Never ask the owner to fix it.")
        texts = [d.handout.body] if d.handout and d.handout.kind == "message" else []
        texts += [d.post_card.what.body] if d.post_card and d.post_card.what.body else []
        if any(register_mixed(x) for x in texts):
            out.append(f"'{d.title}' mixes registers in one message (polite -yo endings and formal -mnida endings). "
                       "Keep one register from start to end: the polite -yo style, or the formal -mnida style "
                       "throughout for a formal business contact.")
        message = d.handout.body if d.handout and d.handout.kind == "message" else ""
        if found := blanks(message):
            out.append(f"'{d.title}' leaves the owner blanks to fill ({', '.join(dict.fromkeys(found))}). The owner "
                       "sends it from their own account, so sign off without a name, and write around what the "
                       "company does not know. The server cuts any sentence that keeps a blank.")
        if voice_mixed(message):
            out.append(f"'{d.title}' speaks as I and as we in one message. The owner sends it as one person: keep I from "
                       "start to end, and make the tool the subject where it acts ('the tool removes names').")
        if found := past_talk(message):
            out.append(f"'{d.title}' says the owner and the person talked before ({', '.join(dict.fromkeys(found))}). "
                       "Keep an earlier talk only when the owner's own words in the plan say it happened; otherwise "
                       "open with the person and the ask.")
        if message and named and len(several := named(d.handout.to or "")) > 1:
            out.append(f"'{d.title}' is one message for {', '.join(several)}. Each person has their own to-do, and a "
                       "message reaches it only on its own request: raise one request per person, its handout `to` "
                       "naming only that person and its body the words for them, even where the words are the same.")
    return out


# ------------------------------------------------------------------ creation

def _chosen(req: AgentRequest) -> tuple[str | None, str | None]:
    """The option the owner picked and the note they left, from the stored response."""
    resp = req.response or {}
    return ((resp.get("chosen_option") or "").strip() or None, (resp.get("note") or "").strip() or None)


def _option_summary(req: AgentRequest, chosen: str) -> str | None:
    """What the decision card said the chosen option does, when the card has that option."""
    options = (((req.action_params or {}).get("card") or {}).get("options") or [])
    key = chosen.strip().lower()
    return next((o.get("summary") for o in options if (o.get("label") or "").strip().lower() == key), None)


def _generic_card(req: AgentRequest) -> dict:
    """The card of a task with no post card. When the owner picked an option, the task is that option: the request's
    own summary still asks the question the owner just answered ("decide whether to ask…"), so it moves to the
    background."""
    chosen, note = _chosen(req)
    card = {"what": req.summary, "why": req.rationale, "request": f"REQ-{req.id}", "action_kind": req.action_kind,
            "estimated_cost_usd": req.estimated_cost_usd,
            **({"handout": handout} if (handout := (req.action_params or {}).get("handout")) else {})}
    if chosen:
        summary = _option_summary(req, chosen)
        card.update(what=f"{chosen}: {summary}" if summary else chosen, chosen_option=chosen, background=req.summary)
    if note:
        card["owner_note"] = note
    return card


_ASIDE = re.compile(r"\s*\([^()]*\)\s*$")


def _title(req: AgentRequest) -> str:
    """The action in plain words: the request's title without a trailing aside, the agent's "(upload route)". The
    option the owner picked is not added either: the card leads with it (`_generic_card`)."""
    bare = _ASIDE.sub("", req.title)
    return bare if len(bare.split()) >= 2 else req.title


def _with_owner_edit(card: dict | None, edit: dict | None) -> dict | None:
    """The post card with the owner's rewrite from the decision card as the text to copy. The agent's draft is kept
    beside it, so the task can say whose words these are."""
    if not card or not edit:
        return card
    what = card.get("what") or {}
    return {**card, "what": {**what, "title": edit.get("title") or what.get("title"), "body": edit["body"]},
            "edited_by_owner": True, "agent_draft": {"title": what.get("title"), "body": what.get("body")}}


def allowed(s: Session, business_id: int | None, kind: str, *, by_owner: bool = False) -> bool:
    """Whether a to-do of this kind may go in the owner's list now (gate.owner_work_allowed)."""
    return gate.owner_work_allowed(s, business_id, kind, by_owner=by_owner)


def _insert(s: Session, task: OwnerTask, *, by_owner: bool) -> OwnerTask | None:
    """The one place a to-do is made. The owner's own click always makes it. One the company makes on its own (the plan,
    a finder, the weekly promotion, an automatic approval) is made only when the running stage lists its kind: the owner
    read that on the stage card before Start. Otherwise no row, and an activity line says what was held and why."""
    if not allowed(s, task.business_id, task.kind, by_owner=by_owner):
        lang = app_settings.language()
        activity.record(s, verb="owner_task_held", entity="business", entity_id=task.business_id,
                        project_id=task.project_id, business_id=task.business_id, actor="system",
                        summary=msg("owner_task.event.held", lang, title=task.title[:120],
                                    kind=msg(f"owner_task.kind.{task.kind}", lang)),
                        data={"kind": task.kind, "title": task.title[:300], "request_id": task.request_id})
        return None
    s.add(task)
    s.flush()
    tracking.attach(s, task)
    return task


def create_for_request(s: Session, req: AgentRequest, *, by_owner: bool = False) -> OwnerTask | None:
    """The one task for an approved request whose action a person carries out. Asking again returns the same task.
    It follows the option the owner picked: its title and card name that option, and an option that says the action
    does not happen ("not posting now") makes no task, only an activity line saying so. A request that revises an
    approval whose task is still open takes that task over, so one matter keeps one task. The activity line is in the
    owner's language, read here: approvals reach this from every lane's job."""
    lang = app_settings.language()
    kind = task_kind(req)
    if kind is None:
        return None
    if (found := s.scalar(select(OwnerTask).where(OwnerTask.request_id == req.id))) is not None:
        return found
    chosen, note = _chosen(req)
    if declines(chosen):
        if (kept := revised_task(s, req)) is not None:
            # A no to a revision is a no to the change: the approval it revised, and its to-do, stand.
            activity.record(s, verb="owner_task_kept", entity="owner_task", entity_id=kept.id,
                            project_id=req.project_id, business_id=kept.business_id, actor="system",
                            summary=msg("owner_task.event.kept", lang, req=f"REQ-{req.id}", chosen=chosen[:60],
                                        ot=display_id(kept.id), title=kept.title[:120]),
                            data={"ref": display_id(kept.id), "request": f"REQ-{req.id}", "chosen_option": chosen})
            return None
        activity.record(s, verb="owner_task_not_needed", entity="request", entity_id=req.id,
                        project_id=req.project_id, actor="system",
                        summary=msg("owner_task.event.not_needed", lang, ref=f"REQ-{req.id}", title=req.title[:120],
                                    chosen=chosen[:60]),
                        data={"request": f"REQ-{req.id}", "chosen_option": chosen})
        return None
    p = s.get(Project, req.project_id) if req.project_id else None
    if (task := revised_task(s, req)) is None and (talk := talk_with(s, req)) is not None:
        return _carry_message(s, talk, req, lang)
    post = _with_owner_edit((req.action_params or {}).get("post_card"), (req.response or {}).get("post_edit"))
    if post:
        card = {**post, **({"chosen_option": chosen} if chosen else {}), **({"owner_note": note} if note else {})}
    else:
        card = _generic_card(req)
    if (req.estimated_cost_usd or 0) > 0:
        card["payment"] = _payment(req)
    if task is not None:
        return _take_over(s, task, req, kind, card, lang)
    task = _insert(s, OwnerTask(business_id=p.business_id if p else None, project_id=req.project_id, request_id=req.id,
                                kind=kind, title=_title(req)[:300], card=card, status=OPEN), by_owner=by_owner)
    if task is None:
        return None
    activity.record(s, verb="owner_task_created", entity="owner_task", entity_id=task.id, project_id=req.project_id,
                    business_id=task.business_id, actor="system",
                    summary=msg("owner_task.event.created", lang, req=f"REQ-{req.id}", ot=display_id(task.id),
                                title=task.title[:120]),
                    data={"ref": display_id(task.id), "request": f"REQ-{req.id}", "kind": kind})
    queue.notify(s, notices.owner_task(task.id, task.title, kind))
    return task


def _recipients(s: Session, req: AgentRequest) -> list[int] | None:
    """The ids of the business's people the request's message is to, by its `to`, or None when it carries no message
    to anyone."""
    handout = (req.action_params or {}).get("handout") or {}
    business_id = s.scalar(select(Project.business_id).where(Project.id == req.project_id)) if req.project_id else None
    if handout.get("kind") != "message" or not handout.get("to") or business_id is None:
        return None
    from justshipit.services import people
    return [p.id for p in people.named(s, business_id, handout["to"])]


def talk_with(s: Session, req: AgentRequest, *, lock: bool = True) -> OwnerTask | None:
    """The open to-do about the one person the request's message goes to, which no other request's message is on yet.
    One person keeps one to-do: the message goes on it, so "Talk to Priya" and "Ask message to Priya" are one."""
    if len(found := _recipients(s, req) or []) != 1:
        return None
    q = (select(OwnerTask).where(OwnerTask.kind.in_(PERSON_KINDS), OwnerTask.status == OPEN,
                                 OwnerTask.request_id.is_(None), OwnerTask.card["person_id"].astext == str(found[0]))
         .order_by(OwnerTask.id).limit(1))
    return s.scalar(q.with_for_update() if lock else q)


def _carry_message(s: Session, task: OwnerTask, req: AgentRequest, lang: Lang) -> OwnerTask:
    task.request_id, task.card = req.id, {**task.card, "handout": req.action_params["handout"]}
    activity.record(s, verb="owner_task_message", entity="owner_task", entity_id=task.id, project_id=req.project_id,
                    business_id=task.business_id, actor="system",
                    summary=msg("owner_task.event.message", lang, req=f"REQ-{req.id}", ot=display_id(task.id),
                                title=task.title[:120]),
                    data={"ref": display_id(task.id), "request": f"REQ-{req.id}"})
    return task


def revised_task(s: Session, req: AgentRequest, *, lock: bool = True) -> OwnerTask | None:
    """The open to-do of the approval this request revises: approving the revision moves it here, a no leaves it. A
    to-do about a person is that person's, so a revision whose message the people list reads as to anyone else leaves
    it as approved and goes where a new request would."""
    rid = (req.action_params or {}).get("revises")
    q = select(OwnerTask).where(OwnerTask.request_id == rid, OwnerTask.status == OPEN)
    task = s.scalar(q.with_for_update() if lock else q) if rid else None
    if task is None or task.kind not in PERSON_KINDS:
        return task
    to = _recipients(s, req)
    return None if to and to != [task.card.get("person_id")] else task


def _take_over(s: Session, task: OwnerTask, req: AgentRequest, kind: str, card: dict, lang: Lang) -> OwnerTask:
    """The revised approval's open task now carries the revision: its title and card are the revision's, and the
    revised request says what superseded it."""
    old = s.get(AgentRequest, task.request_id)
    old.response = {**(old.response or {}), "superseded_by": f"REQ-{req.id}"}
    if task.kind in PERSON_KINDS:   # a to-do about a person stays that, and carries the revised message
        task.request_id, task.card = req.id, {**task.card, **({"handout": card["handout"]} if "handout" in card else {})}
    else:
        # The ticks stay: a step whose words the revision kept is still done (todo_steps keys a line by its words).
        ticked = (task.card or {}).get(todo_steps.DONE_KEY)
        task.request_id, task.kind, task.title = req.id, kind, _title(req)[:300]
        task.card = {**card, todo_steps.DONE_KEY: ticked} if ticked else card
        tracking.attach(s, task)
    activity.record(s, verb="owner_task_moved", entity="owner_task", entity_id=task.id, project_id=req.project_id,
                    business_id=task.business_id, actor="system",
                    summary=msg("owner_task.event.moved", lang, ot=display_id(task.id), req=f"REQ-{req.id}",
                                old=f"REQ-{old.id}", title=task.title[:120]),
                    data={"ref": display_id(task.id), "request": f"REQ-{req.id}", "from_request": f"REQ-{old.id}"})
    return task


def create_post_task(s: Session, business_id: int, card: dict, title: str, *, due_at: datetime | None = None,
                     actor: str = "system") -> OwnerTask | None:
    """A post task the company made itself, from the weekly promotion, with no request behind it. The card is the one
    `build_post_card` returned, so it has passed the same checks as a card an agent proposed. The owner still posts it
    and pastes the link. None when the running stage does not list post to-dos."""
    task = _insert(s, OwnerTask(business_id=business_id, project_id=None, request_id=None, kind="post", title=title[:300],
                                card=card, status=OPEN, due_at=due_at), by_owner=actor == "owner")
    if task is None:
        return None
    activity.record(s, verb="owner_task_created", entity="owner_task", entity_id=task.id, project_id=None,
                    business_id=business_id, actor=actor,
                    summary=msg("owner_task.event.created_weekly", app_settings.language(), ot=display_id(task.id),
                                title=task.title[:120]),
                    data={"ref": display_id(task.id), "kind": "post", "channel": card.get("channel")})
    return task


# ------------------------------------------------------------------ moving a task

def _task(s: Session, task_id: int, *, lock: bool = False) -> OwnerTask:
    q = select(OwnerTask).where(OwnerTask.id == task_id)
    task = s.scalar(q.with_for_update() if lock else q)
    if task is None:
        raise OwnerTaskNotFound(said("owner_task.error.not_found", ref=display_id(task_id)))
    return task


def _move(task: OwnerTask, to: str, by: str) -> None:
    allowed = TRANSITIONS.get((task.status, to))
    if allowed is None:
        raise OwnerTaskError(said("owner_task.error.bad_move", now=said(f"owner_task.status.{task.status}"),
                                  to=said(f"owner_task.status.{to}")))
    if by not in allowed:
        raise OwnerTaskError(said("owner_task.error.owner_only"))
    task.status = to


@dataclass(frozen=True)
class ResultRule:
    """What a kind's result must hold before its to-do is done, how long its note may be, and whether the result is
    the owner's word the plan follows: the spec writer reads it whole and the CEO realigns on it. A post needs its
    link, or a note when the owner posted from a phone and has no link to paste: the note is then the record the
    company quotes."""
    needs: Literal["note", "link_or_note"] | None = None
    note_max: int = MAX_NOTE
    feeds_plan: bool = False


RESULT_RULES: dict[str, ResultRule] = {
    "post": ResultRule(needs="link_or_note"),
    CHECK: ResultRule(needs="note", note_max=20_000, feeds_plan=True),
}
_NEEDED = {"note": "owner_task.error.note_needed", "link_or_note": "owner_task.error.link_or_note_needed"}


def result_rule(kind: str) -> ResultRule:
    return RESULT_RULES.get(kind, ResultRule())


def _has_needed(rule: ResultRule, link: str | None, note: str | None) -> bool:
    return {None: True, "note": bool(note), "link_or_note": bool(link or note)}[rule.needs]


def _numbers(raw: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (raw or {}).items():
        name = str(key).strip()
        if not name or len(name) > 40:
            raise OwnerTaskError(said("owner_task.error.number_name"))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise OwnerTaskError(said("owner_task.error.not_number", name=name))
        out[name] = value
    if len(out) > 20:
        raise OwnerTaskError(said("owner_task.error.too_many_numbers"))
    return out


def done(s: Session, task_id: int, *, link: str | None = None, numbers: dict | None = None, note: str | None = None,
         by: str = "owner", outcome: Any = None) -> OwnerTask:
    """Mark a task done. A task about a person takes the `outcome` of the meeting (the stage reached, what they said,
    when to follow up), which moves the person on and schedules the next step; any other task refuses one."""
    from justshipit.services import people_tasks
    lang = app_settings.language()
    task = _task(s, task_id, lock=True)
    rule = result_rule(task.kind)
    link = (link or "").strip() or None
    note = (note or "").strip() or None
    if note and len(note) > rule.note_max:
        raise OwnerTaskError(said("owner_task.error.note_long", n=f"{rule.note_max:,}"))
    if link and len(link) > MAX_LINK:
        raise OwnerTaskError(said("owner_task.error.link_long", n=MAX_LINK))
    if not _has_needed(rule, link, note):
        raise OwnerTaskError(said(_NEEDED[rule.needs]))
    channel = channels.get((task.card or {}).get("channel") or "")
    if link and (problem := (channels.post_link_problem if task.kind == "post" else channels.link_problem)(channel, link)):
        raise OwnerTaskError(problem)
    result = {"link": link, "numbers": _numbers(numbers), "note": note}
    people_tasks.check_result(task, note, link, *result["numbers"], lang=lang)
    plan = people_tasks.plan_done(s, task, outcome, lang=lang)
    # The link the owner posted is the one the card showed just now, from the places as they stand, so that is what
    # the finished post keeps: a place added after the card was made is on it, and its tag can be counted.
    tracking.attach(s, task)
    _move(task, DONE, by)
    task.result, task.done_at = result, datetime.now(timezone.utc)
    if plan is not None:
        people_tasks.apply_done(s, task, plan, lang=lang)
    activity.record(s, verb="owner_task_done", entity="owner_task", entity_id=task.id, project_id=task.project_id,
                    business_id=task.business_id, actor=by,
                    summary=msg("owner_task.event.done_link" if link else "owner_task.event.done", lang,
                                ot=display_id(task.id), title=task.title[:120], **({"link": link} if link else {})),
                    data={"ref": display_id(task.id), "result": result})
    _plan_hears(s, task, lang)
    return task


def _plan_hears(s: Session, task: OwnerTask, lang: Lang) -> None:
    """A result the plan follows was given, skipped or cleared: a spec card waiting for the owner restates what it
    holds, and the CEO realigns. Keyed by the move, so every later change realigns again."""
    if not (result_rule(task.kind).feeds_plan and task.project_id):
        return
    from justshipit.engineering import owner_trace
    owner_trace.restate(s, task.project_id, lang)
    at = task.done_at or datetime.now(timezone.utc)
    queue.enqueue(s, "realign_project", {"project_id": task.project_id, "owner_task": task.id},
                  idempotency_key=f"realign:{display_id(task.id)}:{task.status}:{at:%Y%m%dT%H%M%S%f}", max_attempts=2)


def skip(s: Session, task_id: int, *, note: str | None, by: str = "owner") -> OwnerTask:
    from justshipit.services import people_tasks
    lang = app_settings.language()
    task = _task(s, task_id, lock=True)
    note = (note or "").strip()
    if not note:
        raise OwnerTaskError(said("owner_task.error.skip_reason"))
    if len(note) > MAX_NOTE:
        raise OwnerTaskError(said("owner_task.error.note_long", n=f"{MAX_NOTE:,}"))
    people_tasks.check_result(task, note, lang=lang)
    _move(task, SKIPPED, by)
    task.result, task.done_at = {"link": None, "numbers": {}, "note": note}, datetime.now(timezone.utc)
    activity.record(s, verb="owner_task_skipped", entity="owner_task", entity_id=task.id, project_id=task.project_id,
                    business_id=task.business_id, actor=by,
                    summary=msg("owner_task.event.skipped", lang, ot=display_id(task.id),
                                title=task.title[:120], note=note[:120]),
                    data={"ref": display_id(task.id), "note": note})
    _plan_hears(s, task, lang)
    return task


def reopen(s: Session, task_id: int, *, by: str = "owner") -> OwnerTask:
    """Back to OPEN, with the earlier result cleared so nothing reports it as current. The activity log keeps it."""
    lang = app_settings.language()
    task = _task(s, task_id, lock=True)
    before = task.result
    _move(task, OPEN, by)
    task.result, task.done_at = None, None
    activity.record(s, verb="owner_task_reopened", entity="owner_task", entity_id=task.id, project_id=task.project_id,
                    business_id=task.business_id, actor=by,
                    summary=msg("owner_task.event.reopened", lang, ot=display_id(task.id), title=task.title[:120]),
                    data={"ref": display_id(task.id), "earlier_result": before})
    _plan_hears(s, task, lang)
    return task


# A clock a little ahead of the server's still says "now", not a time to come.
SENT_SKEW = timedelta(minutes=5)


def sent_by_owner(s: Session, action_id: int, *, at: datetime | None = None, link: str | None = None,
                  note: str | None = None, by: str = "owner") -> OwnerTask:
    """The owner sent by hand what the company kept in the outbox. Nothing is sent here, and the gateway's row stays
    as it was. The record (`result.sent_by_owner`: the action and when) goes on the request's to-do: an open one is
    done with it, and when there is none one is made and done at once (kind other), so My to-dos and the outbox list
    both say the owner sent it. Saying so again for the same item returns the same to-do."""
    lang = app_settings.language()
    action = s.scalar(select(ActionExecution).where(ActionExecution.id == action_id).with_for_update())
    if action is None:
        raise OwnerTaskNotFound(said("owner_task.error.no_outbox_item"))
    if action.mode != "outbox" or action.status != "done":
        raise OwnerTaskError(said("owner_task.error.not_in_outbox"))
    now = datetime.now(UTC)
    at = (at.replace(tzinfo=UTC) if at.tzinfo is None else at) if at else now
    if at > now + SENT_SKEW:
        raise OwnerTaskError(said("owner_task.error.sent_later"))
    record = {"action_id": action.id, "at": at.isoformat()}
    task = s.scalar(select(OwnerTask).where(OwnerTask.request_id == action.request_id).with_for_update())
    if task is not None and task.status != OPEN:
        if ((task.result or {}).get("sent_by_owner") or {}).get("action_id") == action.id:
            return task
        raise OwnerTaskError(said("owner_task.error.sent_closed", ref=display_id(task.id)))
    if task is None:
        req = s.get(AgentRequest, action.request_id)
        p = s.get(Project, req.project_id) if req.project_id else None
        task = _insert(s, OwnerTask(business_id=p.business_id if p else None, project_id=req.project_id,
                                    request_id=req.id, kind="other", title=_title(req)[:300], card=_generic_card(req),
                                    status=OPEN), by_owner=True)
    done(s, task.id, link=link, note=note, by=by)
    task.result = {**task.result, "sent_by_owner": record}
    task.done_at = at
    activity.record(s, verb="sent_by_owner", entity="owner_task", entity_id=task.id, project_id=task.project_id,
                    business_id=task.business_id, actor=by,
                    summary=msg("owner_task.event.sent_by_owner", lang, ot=display_id(task.id), title=task.title[:120]),
                    data={"ref": display_id(task.id), "request": f"REQ-{action.request_id}", "action_id": action.id,
                          "connector": action.connector, "at": record["at"]})
    return task


def sent_records(s: Session, actions: list[ActionExecution]) -> dict[int, dict]:
    """For the outbox items the owner sent by hand, by action id: when, the link and note they left, and the to-do."""
    tasks = s.scalars(select(OwnerTask).where(OwnerTask.request_id.in_([a.request_id for a in actions]),
                                              OwnerTask.status == DONE)) if actions else []
    out: dict[int, dict] = {}
    for task in tasks:
        result = task.result or {}
        if (sent := result.get("sent_by_owner")) and isinstance(sent.get("action_id"), int):
            out[sent["action_id"]] = {"at": sent.get("at"), "link": result.get("link"), "note": result.get("note"),
                                      "task_id": task.id, "ref": display_id(task.id)}
    return out


def tick(s: Session, task_id: int, key: str, done: bool) -> OwnerTask:
    """Tick one of an open to-do's steps (todo_steps), or untick it. Only the console's owner routes call this."""
    task = _task(s, task_id, lock=True)
    if task.status != OPEN:
        raise OwnerTaskError(said("owner_task.error.step_closed"))
    try:
        task.card = todo_steps.tick(task.card or {}, todo_steps.for_task(task, app_settings.language()), key, done)
    except KeyError:
        raise OwnerTaskError(said("owner_task.error.no_step")) from None
    return task


# ------------------------------------------------------------------ reading

def view(task: OwnerTask, lang: Lang) -> dict:
    """The to-do as the console reads it, the server's words in `lang`. `kind_ko` and `status_ko` carry the same
    labels as `kind_label` and `status_label`, for readers not yet moved to those. The decision it came from is named by
    its title (`request_title`), the link the console shows, never by its code."""
    row = {c.name: getattr(task, c.name) for c in task.__table__.columns}
    kind, status = kind_label(shown_kind(task), lang), status_label(task.status, lang)
    rule = result_rule(task.kind)
    s = object_session(task)
    decided = (s.scalar(select(AgentRequest.title).where(AgentRequest.id == task.request_id))
               if s is not None and task.request_id else None)
    track = tracking.track_view(task, lang)
    card = readable_card(task.card, lang, task.title, task.status == OPEN)
    return {**row, "title": readable_title(task.title, lang), "card": {**card, "track": track} if card and track else card,
            "ref": display_id(task.id), "kind_label": kind, "status_label": status, "kind_ko": kind, "status_ko": status,
            "shown_kind": shown_kind(task), "request_title": owner_text.title(decided) if decided else None,
            "result_needs": rule.needs, "note_max": rule.note_max, "post_link": _post_link(task), "track": track,
            "fit_view": fit_view(task, lang), **_steps(task, lang)}


def fit_view(task: OwnerTask, lang: Lang) -> dict | None:
    """What a weekly post to-do rested on, as the owner reads it: None when the card records no pick (`card["fit"]`, which
    only the server writes). `guessed` names each line the pick leaned on a guess for, with the labels of the codes that
    matched, and `changed` says the customer description no longer holds one of the codes the pick used."""
    fit = (task.card or {}).get("fit")
    if not isinstance(fit, dict):
        return None
    codes = {line: [c for c in cs if vocab.is_code(line, c)]
             for line, cs in (fit.get("codes") or {}).items() if line in vocab.LINES}
    s = object_session(task)
    now = profile.of(s, task.business_id) if s is not None and task.business_id is not None else None
    changed = now is not None and any(c not in now.lines[line].codes for line, cs in codes.items() for c in cs)
    # A guess the owner has since confirmed needs no check any more.
    guessed = [{"line": line, "line_label": msg(f"profile.line.{line}", lang),
                "labels": [msg(f"profile.code.{line}.{c}", lang) for c in codes.get(line, [])]}
               for line in fit.get("guessed") or [] if line in vocab.LINES and (now is None or now.guessed(line))]
    return {"guessed": guessed, "changed": changed}


def _post_link(task: OwnerTask) -> dict | None:
    """The shape the post's link must have, so the form refuses what `done` would refuse; None when any page counts."""
    channel = channels.get((task.card or {}).get("channel") or "") if task.kind == "post" else None
    return {"patterns": list(channel.post_link), "example": channel.post_example} if channel and channel.post_link else None


def _steps(task: OwnerTask, lang: Lang) -> dict:
    steps = todo_steps.view(todo_steps.for_task(task, lang), task.card)
    return {"steps": steps, "steps_from": todo_steps.source(task.card) if steps else None}


# Stored tasks keep the words they were written with, so what the owner reads is cleaned here, for old rows too: brief
# line ids and flags out of the agent's reasons, and a person's place and a post's channel in the owner's language. The
# stored row is not rewritten.
_PLACE_TAIL = re.compile(r"\((?P<code>[a-z_]+)\)\s*$")
_REASONS = ("why", "background")


def readable_title(title: str, lang: Lang) -> str:
    from justshipit.services.people import PLACES
    title = owner_text.title(title)
    return _PLACE_TAIL.sub(lambda m: f"({PLACES[m['code']].names[lang]})" if m["code"] in PLACES else m[0], title)


def _no_draft_lines() -> set[str]:
    """The line person cards once carried, "the company doesn't draft it". A task may have drafted the message, so
    it was not always true, and the checklist already says to write in the owner's own words."""
    return {msg("people.card.no_draft", lang) for lang in LANGS}


def readable_card(card: dict | None, lang: Lang, title: str = "", open_card: bool = True) -> dict | None:
    """The card as the owner reads it. `open_card` says the card still waits for the owner, which decides whether an
    unchecked place's card says it was written before the rules check."""
    if not card:
        return card
    if card.get("note") in _no_draft_lines():
        card = {k: v for k, v in card.items() if k != "note"}
    out = {**card, **{k: owner_text.detail(card[k]) for k in _REASONS if isinstance(card.get(k), str)}}
    if isinstance(card.get("what"), str):
        out["what"] = owner_text.detail(card["what"])
    if isinstance(card.get("when"), dict) and isinstance(card["when"].get("reason"), str):
        out["when"] = {**card["when"], "reason": owner_text.detail(card["when"]["reason"])}
    return with_named_channel(out, lang, title, open_card)


def with_named_channel(card: dict, lang: Lang, title: str = "", open_card: bool = True) -> dict:
    """A post card with its place's name and rules from the registry, in `lang`: the card stored them in the language
    of the day it was written. One filed under a generic channel reads as the place its title or link names. Its mode
    and text stay as written. It also says whether the place's rules are checked as the registry stands now
    (`rules_state`, `rules_checked_on`, `rules_unchecked_text`), and an open card for a place that is unchecked says it
    was written before the check (`rules_written_before`), and so does an open draft for a checked place that asks to
    be written by the owner (`rules_written_before_why` is `self_written`). A card for the owner's own place has no
    outside rules (`rules_state` is `own`, and no rules summary). A card with no address filed under `other` is the
    owner's own too, unless its title names a place."""
    place = channels.get(card.get("channel") or "")
    if place is None:
        return card
    if place.generic and not card.get("own_place"):
        what = card.get("what") if isinstance(card.get("what"), dict) else {}
        place = channels.named(f"{title} {what.get('title') or ''}", card.get("where_url")) or place
    state = _rules_state(card, place, lang, open_card)
    return {**card, "channel": place.key, "channel_name": place.names[lang],
            "rules_summary": "" if state["rules_state"] == "own" else place.rules[lang],
            "rules_source": place.source_url, "rules_basis": place.basis, **state}


def _rules_state(card: dict, place: channels.Channel, lang: Lang, open_card: bool) -> dict[str, Any]:
    if card.get("own_place") or (place.key == "other" and not card.get("where_url")):
        return {"rules_state": "own", "rules_checked_on": None, "rules_unchecked_text": None}
    why = channels.unchecked_said(place)
    out: dict[str, Any] = {"rules_state": channels.state(place),
                           "rules_checked_on": place.checked_on.isoformat() if place.checked_on else None,
                           "rules_unchecked_text": say(why, lang) if why else None}
    if open_card and place.key != "other":
        if why:
            out["rules_written_before"] = msg("post.rules.written_before", lang)
        elif place.self_written and card.get("mode") == "draft":
            out["rules_written_before"] = msg("post.rules.written_before_self", lang)
            out["rules_written_before_why"] = "self_written"
    return out


def get(s: Session, task_id: int, *, lang: Lang) -> dict:
    return view(_task(s, task_id), lang)


def listing(s: Session, *, status: str | None = None, business_id: int | None = None, lang: Lang) -> dict:
    """The tasks, open ones first, with a count per status over the whole business so a header can say how many are
    open whichever tab is showing."""
    if status is not None and status not in (OPEN, DONE, SKIPPED):
        raise InvalidQuery(said("owner_task.error.bad_status", statuses=", ".join(STATUSES)))
    scope = [OwnerTask.business_id == business_id] if business_id is not None else []
    counts = dict(s.execute(select(OwnerTask.status, func.count()).where(*scope).group_by(OwnerTask.status)).all())
    q = select(OwnerTask).where(*scope, *([OwnerTask.status == status] if status else []))
    rows = s.scalars(q.order_by((OwnerTask.status != OPEN), OwnerTask.id.desc()))
    return {"items": [view(t, lang) for t in rows],
            "counts": {st: counts.get(st, 0) for st in (OPEN, DONE, SKIPPED)}, "open": counts.get(OPEN, 0)}


# ------------------------------------------------------------------ what the company may say it did

BRIEF_TASKS = 10
BRIEF_LINK = 500
BRIEF_NOTE = 200
BRIEF_TITLE = 80
BRIEF_NUMBERS = 6


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def result_text(result: dict | None, lang: Lang = DEFAULT) -> str:
    """What the owner filled in, clipped, so a task whose owner pasted a long note cannot grow every prompt."""
    result = result or {}
    parts = []
    if result.get("link"):
        parts.append(msg("owner_task.result.link", lang, link=_clip(result["link"], BRIEF_LINK)))
    if result.get("numbers"):
        shown = list(result["numbers"].items())
        more = (msg("owner_task.result.more", lang, n=len(shown) - BRIEF_NUMBERS)
                if len(shown) > BRIEF_NUMBERS else "")
        numbers = ", ".join(f"{_clip(str(k), 40)}={v:g}" for k, v in shown[:BRIEF_NUMBERS]) + more
        parts.append(msg("owner_task.result.numbers", lang, numbers=numbers))
    if result.get("note"):
        parts.append(msg("owner_task.result.note", lang, note=_clip(result["note"], BRIEF_NOTE)))
    return " / ".join(parts)


def _count_line(counts: dict[str, int], lang: Lang) -> str:
    return " · ".join(f"{status_label(st, lang)} {counts.get(st, 0)}" for st in STATUSES)


def _task_line(t: OwnerTask, lang: Lang, fence: bool = True) -> str:
    """One to-do. The result is the owner's own words, so a prompt carries it between fences; the owner's report does
    not need them."""
    head = (f"- [{display_id(t.id)} · {status_label(t.status, lang)}] {_clip(t.title, BRIEF_TITLE)}"
            + (f" (REQ-{t.request_id})" if t.request_id else ""))
    if t.status == OPEN:
        return f"{head}: {msg('owner_task.section.not_done', lang)}"
    if not (tail := result_text(t.result, lang)):
        return head
    return f"{head}:\n{fenced(f'{display_id(t.id)} result', tail)}" if fence else f"{head}: {tail}"


def status_lines(s: Session, project_id: int, lang: Lang, fence: bool = True) -> list[str]:
    """The count per status, then one line per recent approved action a person carries out: the task's status and
    result, or that no task tracks it (an approval from before owner tasks existed). Only the latest `BRIEF_TASKS`
    are listed."""
    counts = dict(s.execute(select(OwnerTask.status, func.count()).where(OwnerTask.project_id == project_id)
                            .group_by(OwnerTask.status)).all())
    # A message to a person rides on that person's to-do, which belongs to the business, not the project.
    tracked = select(OwnerTask.request_id).where(OwnerTask.request_id.is_not(None))
    old_q = select(AgentRequest).where(
        AgentRequest.project_id == project_id, AgentRequest.status.in_(("APPROVED", "AUTO_APPROVED")),
        AgentRequest.action_kind.in_(list(ACTION_TASK_KIND)), AgentRequest.id.not_in(tracked))
    old_total = s.scalar(select(func.count()).select_from(old_q.subquery()))
    about_people = _people_lines(s, project_id, lang, fence)
    if not sum(counts.values()) and not old_total:
        return about_people
    recent = list(s.scalars(select(OwnerTask).where(OwnerTask.project_id == project_id)
                            .order_by(OwnerTask.id.desc()).limit(BRIEF_TASKS)))[::-1]
    old = list(s.scalars(old_q.order_by(AgentRequest.id.desc()).limit(BRIEF_TASKS)))[::-1]
    lines = [msg("owner_task.section.count", lang, counts=_count_line(counts, lang))
             + (msg("owner_task.section.untracked_count", lang, n=old_total) if old_total else "")]
    if (hidden := sum(counts.values()) - len(recent)) > 0:
        lines.append(msg("owner_task.section.hidden", lang, n=hidden))
    lines += [_task_line(t, lang, fence) for t in recent]
    lines += [msg("owner_task.section.untracked", lang, id=r.id, title=_clip(r.title, BRIEF_TITLE)) for r in old]
    return lines + about_people


def _people_lines(s: Session, project_id: int, lang: Lang, fence: bool) -> list[str]:
    """The business's latest to-dos about people (talk to, follow up), open or not, so a review reads whether each
    person the plan names was reached rather than guessing from the decisions waiting."""
    business_id = s.scalar(select(Project.business_id).where(Project.id == project_id))
    if business_id is None:
        return []
    rows = list(s.scalars(select(OwnerTask).where(OwnerTask.business_id == business_id, OwnerTask.project_id.is_(None),
                                                  OwnerTask.kind.in_(PERSON_KINDS))
                          .order_by(OwnerTask.id.desc()).limit(BRIEF_TASKS)))[::-1]
    return [msg("owner_task.section.people", lang), *(_task_line(t, lang, fence) for t in rows)] if rows else []


def people_to_talk(s: Session, project_id: int) -> list[str]:
    """The names on the business's open to-dos about a person, oldest first, each once: the people a message the
    company drafts can reach (`talk_with`)."""
    business_id = s.scalar(select(Project.business_id).where(Project.id == project_id))
    if business_id is None:
        return []
    cards = s.scalars(select(OwnerTask.card).where(OwnerTask.business_id == business_id, OwnerTask.status == OPEN,
                                                   OwnerTask.kind.in_(PERSON_KINDS)).order_by(OwnerTask.id))
    return list(dict.fromkeys(n for c in cards if (n := ((c or {}).get("person") or {}).get("display_name"))))


def owner_words(result: dict | None) -> str:
    """The owner's result whole, for a reader that must not lose any of it: the note, then the link."""
    result = result or {}
    return "\n".join(x for x in (result.get("note"), result.get("link")) if x)


def plan_results(s: Session, project_id: int) -> list[OwnerTask]:
    """The project's to-dos whose result the plan follows, oldest first: done, or skipped with the owner's reason."""
    kinds = [k for k, rule in RESULT_RULES.items() if rule.feeds_plan]
    return list(s.scalars(select(OwnerTask).where(OwnerTask.project_id == project_id,
                                                  OwnerTask.status.in_((DONE, SKIPPED)),
                                                  OwnerTask.kind.in_(kinds)).order_by(OwnerTask.id)))


def open_checks(s: Session, project_id: int) -> list[str]:
    """The titles of the project's checks the owner has not made yet: a check card still waiting for approval and a
    check to-do still open. Read live, so a spec card can say what it builds on a guess."""
    actions = [a for a, kind in ACTION_TASK_KIND.items() if kind == CHECK]
    waiting = s.scalars(select(AgentRequest.title).where(
        AgentRequest.project_id == project_id, AgentRequest.status == "PENDING", AgentRequest.action_kind.in_(actions))
        .order_by(AgentRequest.id))
    todo = s.scalars(select(OwnerTask.title).where(OwnerTask.project_id == project_id, OwnerTask.status == OPEN,
                                                   OwnerTask.kind == CHECK).order_by(OwnerTask.id))
    return [owner_text.title(x) for x in [*waiting, *todo]]


def status_by_task(s: Session, project_id: int) -> dict[int, str]:
    """The owner-task status of each project task whose request made one, so a report shows 'owner's to-do: open' and
    not a plain DONE for work that is approved but not done."""
    rank = {OPEN: 0, DONE: 1, SKIPPED: 2}
    out: dict[int, str] = {}
    for task_id, status in s.execute(
            select(AgentRequest.task_id, OwnerTask.status).join(OwnerTask, OwnerTask.request_id == AgentRequest.id)
            .where(AgentRequest.project_id == project_id, AgentRequest.task_id.is_not(None))):
        if task_id not in out or rank[status] < rank[out[task_id]]:
            out[task_id] = status
    return out


def status_section(s: Session, project_id: int, *, fence: bool = True) -> str:
    """The text every prompt and the final report carry, empty when the project has nothing a person carries out. The
    report is the owner's, so it is in the owner's language, read here: every lane's job builds a prompt with it. The
    report passes `fence=False`, since it is not a prompt."""
    lang = app_settings.language()
    lines = status_lines(s, project_id, lang, fence)
    if not lines:
        return ""
    return "\n".join([msg("owner_task.section.title", lang), msg("owner_task.section.rule", lang), *lines])


def portfolio_section(s: Session) -> str:
    """The weekly review's view, in the owner's language: the count per status of each business, and the latest tasks
    that belong to no project, which no project brief shows."""
    lang = app_settings.language()
    rows = s.execute(select(OwnerTask.business_id, OwnerTask.status, func.count())
                     .group_by(OwnerTask.business_id, OwnerTask.status)).all()
    if not rows:
        return ""
    names = dict(s.execute(select(Business.id, Business.name)).all())
    per: dict[int | None, dict[str, int]] = {}
    for business_id, status, n in rows:
        per.setdefault(business_id, {})[status] = n
    lines = [f"- {_business_label(b, names, lang)}: {_count_line(c, lang)}"
             for b, c in sorted(per.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))]
    loose = list(s.scalars(select(OwnerTask).where(OwnerTask.project_id.is_(None), OwnerTask.kind.not_in(PEOPLE_KINDS))
                           .order_by(OwnerTask.id.desc()).limit(BRIEF_TASKS)))[::-1]
    if loose:
        lines += [msg("owner_task.section.loose", lang), *(_task_line(t, lang) for t in loose)]
    return "\n".join([msg("owner_task.section.title", lang), msg("owner_task.section.rule_portfolio", lang), *lines])


def _business_label(business_id: int | None, names: dict[int, str], lang: Lang) -> str:
    return (msg("owner_task.section.no_business", lang) if business_id is None
            else f"B-{business_id} {names.get(business_id, '')}".strip())
