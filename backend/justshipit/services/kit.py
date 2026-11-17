"""The First 10 kit (roadmap 0.6, docs/design/first-ten-kit.md): what the owner needs in hand to reach the first ten
people, written for the owner to copy. The company prepares it and never sends it.

The kit holds today's people (`people.today`), one message draft for each person who gets one, profile wording in three
sizes and invite wording in two. A person found in a public post and not yet contacted gets no draft: the owner answers
the post in their own words first (`wants_draft`). This module imports no connector, outbox or request module, makes no
decision card and no to-do, and stores no email address, phone number or messenger id: every text passes
`people.contact_info_problem`, and the table has no column for one.

The owner's click queues one `kit_prepare` job (`request`). The job makes one marketing call (`run`), checks every text
(`judge`), gives each refused one a single rewrite round with the reasons, and stores what is left, a refused text with
its reason and no body. A text is stored by replacing the live row of its slot in one transaction, so a slot holds one
live text. Whether a text is stale is worked out on every read (`read`), never stored.

What the owner reads is in the owner's language (`lang`, read once by the route or the job handler). A message is
written in the language of the person's own words when they have any.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from justshipit.agents.executor import last_call_id, run_agent
from justshipit.contracts.first_ten_kit import KitOut
from justshipit.db.models import KIT_PARTS, ActivityEvent, Business, Job, KitText, Person
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, NAMES, Lang, msg
from justshipit.i18n.match import kit as words
from justshipit.i18n.said import Refusal, said, say
from justshipit.services import (
    activity,
    app_settings,
    channels,
    copy_rules,
    owner_tasks,
    people,
    profile,
    queue,
    roadmap,
    tracking,
    tracking_count,
    usage,
)
from justshipit.services.fence import fenced
from justshipit.settings import settings

log = logging.getLogger(__name__)

PARTS = KIT_PARTS
SLOTS: dict[str, tuple[str, ...]] = {"message": ("p",), "profile": ("line", "short", "about"), "invite": ("known", "problem")}
LIMITS: dict[tuple[str, str], int] = {("message", "p"): 600, ("profile", "line"): 80, ("profile", "short"): 160,
                                      ("profile", "about"): 400, ("invite", "known"): 500, ("invite", "problem"): 500}
# The texts that carry the owner's own place, and what a message may carry once the owner and the person have talked.
LINK_SLOTS = frozenset({("profile", "about"), ("invite", "known"), ("invite", "problem")})
LINK_FROM_STAGE = "talked"
# The first-10 count the kit reports, from the one list the Numbers tab reads.
MILESTONE = tracking_count.MILESTONES[0]
# A stored text carries no link tag: nothing reads a tag back from a private message. `links_for` is given no tag, so
# every address is the plain one; "other" is the channel with no rule of its own.
PLACE_CHANNEL = "other"
PUBLIC_SOURCES = ("public_problem", "similar_tool", "participation")
RECENT_QUOTES = 5
REFUSALS = ("no_draft", "empty", "too_long", "contact", "foreign_link", "no_link", "blanks", "mixed_register",
            "mixed_voice", "past_talk", "invented_experience", "internal_note", "asks_votes", "several_people",
            "asks_spread", "unsaid_quote", "forgotten")


class KitError(Refusal):
    """A refusal the console shows, worded by the route in the owner's language: 400."""


class KitNotFound(KitError):
    """The business or the person is not there: 404."""


class KitConflict(KitError):
    """The kit cannot be written in the state things are in (a write is running, there is no plan): 409."""


# ------------------------------------------------------------------ who gets a message draft

def wants_draft(source: str, stage: str) -> bool:
    """Whether a person gets a message draft: anyone the owner knows or who wrote in, and anyone the owner has contacted
    already (a follow-up). A stranger found in a public post, not yet contacted, gets none: the owner answers the post
    first, since a cold private message to someone found on a forum is what the roadmap's "never" list is closest to."""
    return not (source in PUBLIC_SOURCES and stage == "found")


def _reached(stage: str, at: str) -> bool:
    return people.LADDER.index(stage) >= people.LADDER.index(at) if stage in people.LADDER else False


def _eligible(person: Person) -> bool:
    return person.archived_at is None and person.stage != people.DROPPED and wants_draft(person.source, person.stage)


# ------------------------------------------------------------------ what a text is built from

@dataclass(frozen=True)
class Want:
    """One text to write: a slot of a part, and for a message the person it is for."""
    part: str
    slot: str
    person_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.part} {self.slot}" + (f" {people.ref(self.person_id)}" if self.person_id is not None else "")


@dataclass(frozen=True)
class PersonFacts:
    id: int
    source: str
    stage: str
    why: str
    next_action: str | None
    next_action_at: datetime | None
    quotes: tuple[str, ...]    # the person's latest words, the only ones the message may rest on
    quotes_n: int              # all their quotes, so a new one makes the message stale
    lang: Lang
    handle: str = ""           # as the owner's list holds it: the text may name it, and the contact check leaves it out

    @property
    def ref(self) -> str:
        return people.ref(self.id)

    def seen(self) -> dict[str, Any]:
        return _seen(self.stage, self.quotes_n, self.next_action_at)


@dataclass(frozen=True)
class Facts:
    business_id: int
    name: str
    one_liner: str
    plan: str
    about_owner: str
    who: str                       # the customer description, fenced, for the prompt
    fingerprint: tuple[str, ...]   # what the description said when the text was written
    guess: bool
    link: str | None
    persons: tuple[PersonFacts, ...]
    wants: tuple[Want, ...]
    lang: Lang

    def person(self, person_id: int | None) -> PersonFacts | None:
        return next((p for p in self.persons if p.id == person_id), None)


def _seen(stage: str, quotes_n: int, next_action_at: datetime | None) -> dict[str, Any]:
    return {"stage": stage, "quotes_n": quotes_n,
            "next_action_at": next_action_at.astimezone(UTC).isoformat() if next_action_at else None}


_HANGUL = re.compile(f"[{chr(0xAC00)}-{chr(0xD7A3)}]")


def message_lang(quotes: tuple[str, ...] | list[str], owner_lang: Lang) -> Lang:
    """The language of a person's own words when they have any, else the owner's: Korean when any quote holds Hangul."""
    if not quotes:
        return owner_lang
    return "ko" if any(_HANGUL.search(q) for q in quotes) else "en"


def own_link(business: Business) -> str | None:
    """The owner's own place, plain: the first address `tracking.links_for` gives with no tag, or None when the owner
    has no place yet."""
    links = tracking.links_for(business, PLACE_CHANNEL)
    return links[0]["url"] if links else None


def fingerprint(prof: profile.Profile) -> tuple[str, ...]:
    """What the customer description says, as a few short strings: each known value with how sure the company is of it.
    A text is stale when this differs from what it was written from."""
    out = []
    for line, view in prof.lines.items():
        for v in view.values:
            if v.provenance != "unknown":
                what = v.code or "s-" + hashlib.sha1((v.sentence or "").encode()).hexdigest()[:8]
                out.append(f"{line}:{what}:{v.provenance}")
    return tuple(sorted(out))


def _guessed(prof: profile.Profile) -> bool:
    return any(prof.guessed(line) for line in prof.lines)


def _plan_lines(s: Session, business_id: int, keys: tuple[str, ...]) -> str:
    """The approved plan's lines of these sections that the owner said or confirmed, as the promotion prompt reads them."""
    brief = roadmap.approved_brief(s, business_id)
    if brief is None:
        return ""
    return "\n".join(f"- {sec['title']}: {line['text']}" for sec in brief.sections if sec["key"] in keys
                     for line in sec["lines"] if line["provenance"] in ("said", "confirmed"))


def _person_facts(person: Person, owner_lang: Lang) -> PersonFacts:
    texts = tuple(q["text"] for q in (person.quotes or [])[-RECENT_QUOTES:] if isinstance(q, dict) and q.get("text"))
    return PersonFacts(person.id, person.source, person.stage, person.why or "", person.next_action,
                       person.next_action_at, texts, len(person.quotes or []), message_lang(texts, owner_lang),
                       person.handle or "")


def _wants(parts: list[str], person_ids: list[int]) -> tuple[Want, ...]:
    return (*(Want("message", "p", i) for i in person_ids if "message" in parts),
            *(Want("profile", slot) for slot in SLOTS["profile"] if "profile" in parts),
            *(Want("invite", slot) for slot in SLOTS["invite"] if "invite" in parts))


def _gather(s: Session, business_id: int, parts: list[str], person_ids: list[int], lang: Lang) -> Facts | None:
    """What this write rests on, read once: the business's plan and description, its place, and the people it writes
    to who are still there to write to. None when there is no business or no plan to write from."""
    business = s.get(Business, business_id)
    if business is None or business.status == "ARCHIVED" or roadmap.approved_brief(s, business_id) is None:
        return None
    prof = profile.of(s, business_id)
    chosen = []
    for pid in person_ids:
        person = s.get(Person, pid)
        if person is not None and person.business_id == business_id and _eligible(person):
            chosen.append(_person_facts(person, lang))
    return Facts(business_id, business.name, business.one_liner, _plan_lines(s, business_id, ("one_liner", "customer_problem", "scope")),
                 _plan_lines(s, business_id, ("background",)), profile.prompt_block(prof, lang), fingerprint(prof),
                 _guessed(prof), own_link(business), tuple(chosen), _wants(parts, [p.id for p in chosen]), lang)


# ------------------------------------------------------------------ the text checks

@dataclass(frozen=True)
class Problem:
    """Why a text is refused: the code the owner reads a sentence for (`kit.refused.<code>`), and the same said to the
    model in English, for the rewrite round."""
    code: str
    detail: str


# A text ends an address at a space, a bracket, a dash or a quotation mark, and at Hangul, where a Korean particle
# ("https://myshop.com에서") begins.
_END = r"\s<>\"'“”‘’「」『』()\[\]—–" + chr(0xAC00) + "-" + chr(0xD7A3) + chr(0x3131) + "-" + chr(0x318E)
# An address is one with a scheme or `www.`, or any `name.tld` where the tld is 2 to 24 letters, all lower or all upper
# case, followed by `/` or the end of the word: "it.Then", a sentence run into the next, is not one.
_URL = re.compile(rf"(?:https?://|www\.)[^{_END}]+|(?<![\w@.-])(?:[a-z0-9-]+\.)+(?-i:[a-z]{{2,24}}|[A-Z]{{2,24}})"
                  rf"(?![A-Za-z0-9_@-])(?:/[^{_END}]*)?", re.IGNORECASE)
# What has the shape of an address and is a file name in a sentence about code ("node.js", "config.yaml").
_FILE_NAMES = frozenset("js ts jsx tsx py rb go rs sh md txt json yaml yml toml ini cfg log html htm css csv pdf png jpg "
                        "jpeg gif svg zip exe doc docx xls xlsx ppt pptx java cpp".split())
_QUOTED = re.compile(r'"([^"\n]{4,})"|“([^”\n]{4,})”|「([^」\n]{2,})」|『([^』\n]{2,})』'
                     r"|‘((?:[^’\n]|(?<=[A-Za-z])’(?=[a-z])){4,})’"
                     r"|(?<![A-Za-z0-9])'((?:[^'\n]|(?<=[A-Za-z])'(?=[a-z])){4,})'(?![A-Za-z0-9])")
_MANY = re.compile("|".join(w for lang in LANGS for w in words.ADDRESSED_TO_MANY[lang]), re.IGNORECASE)
_PASS_ON = re.compile("|".join(w for lang in LANGS for w in words.PASS_ON[lang]), re.IGNORECASE)
_BRACKET = re.compile(r"\[[^\]\n]{2,}\]")
_APPS = "|".join(w for lang in LANGS for w in words.MESSENGERS[lang])
_ID_WORDS = "|".join(w for lang in LANGS for w in words.ID_WORDS[lang])
# An app's name and, a few characters on, an id: an `@handle`, a word that follows "id" (at least 5 letters, or 3 with a
# digit, dot or underscore, so "id is the same" is no id), or a token that holds both letters and digits or underscores.
_MESSENGER_ID = re.compile(
    rf"(?<![A-Za-z])(?:{_APPS})(?![A-Za-z])[^\n]{{0,14}}?(?:@[\w.]{{3,}}"
    rf"|(?<![A-Za-z])(?:{_ID_WORDS})(?![A-Za-z])\s*(?:is|are|:|=)?\s*"
    rf"(?:[A-Za-z]{{5,}}|(?=[A-Za-z0-9_.-]*[\d_.])[A-Za-z0-9_.-]{{3,}})"
    rf"|(?<![\w.-])(?=[A-Za-z0-9_.-]*[A-Za-z])(?=[A-Za-z0-9_.-]*[\d_])[A-Za-z0-9_.-]{{4,}})", re.IGNORECASE)


def _tidy(url: str) -> str:
    return url.rstrip(".,;:!?)]}>")


def _norm(url: str | None) -> str | None:
    """An address as two spellings of it compare: no scheme, no `www.`, no trailing slash, lower case."""
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", _tidy(url).lower()).rstrip("/") if url else None


def _file_name(url: str) -> bool:
    return "/" not in url and "." in url and url.rsplit(".", 1)[1].lower() in _FILE_NAMES and not url.startswith(("http", "www."))


def links_in(body: str) -> list[str]:
    found = (_tidy(m.group()) for m in _URL.finditer(body))
    return [u for u in found if not _file_name(u)]


def _without_handle(body: str, person: PersonFacts | None) -> str:
    """The text with the person's own handle taken out, `@jane.doe` or `jane.doe`: a handle with a dot reads as an
    address or an email, and a text that greets the person by it is the normal case."""
    handle = (person.handle if person else "").strip().lstrip("@")
    if len(handle) < 2 or "@" in handle:
        return body
    return re.sub(rf"(?<![\w.%+/-])@?{re.escape(handle)}(?![A-Za-z0-9_@])(?!\.[A-Za-z0-9_])", "", body, flags=re.IGNORECASE)


def _squash(text: str) -> str:
    return " ".join(text.casefold().split())


def _unsaid_quotes(body: str, said_texts: list[str], own: tuple[str, ...] = ()) -> list[str]:
    """Words the text puts in quotation marks that none of the person's quotes or the reason they are on the list holds,
    and that are not one of `own`, the business's own name for its product."""
    hay = _squash(" ".join(said_texts))
    known = {_squash(n) for n in own if n}
    found = (next(g for g in m.groups() if g) for m in _QUOTED.finditer(body))
    return [q for q in found if _squash(q) not in hay and _squash(q) not in known]


def link_for(link: str | None, want: Want, person: PersonFacts | None) -> str | None:
    """The one address this text may carry, or None when it may carry none. Invite wording and the about-size profile
    carry the owner's place; a message may once the owner and the person have talked; nothing else carries a link."""
    if want.part == "message":
        return link if person is not None and _reached(person.stage, LINK_FROM_STAGE) else None
    return link if (want.part, want.slot) in LINK_SLOTS else None


def judge(s: Session, facts: Facts, want: Want, body: str) -> list[Problem]:
    """Every reason this text may not be kept, empty when it may. The checks are the ones a card passes, reused: contact
    details (`people.contact_info_problem`, and the kit's own messenger-id check), the handout author's checks
    (`owner_tasks`: blanks, one register, one voice, no earlier talk that did not happen), public copy (`copy_rules`) and
    no ask for votes (`channels.forbidden_reason`), and the kit's own: the length limit, no link but the owner's, no
    bracket, one person, and no quotation the person did not write. The person's own handle is left out of the link and
    contact checks, since a message that greets them by it is the normal case."""
    if not body:
        return [Problem("empty", "The text is empty.")]
    person, limit = facts.person(want.person_id), LIMITS[(want.part, want.slot)]
    allowed = link_for(facts.link, want, person)
    clean = _without_handle(body, person)
    out: list[Problem] = []
    if len(body) > limit:
        out.append(Problem("too_long", f"It has {len(body)} characters. The limit is {limit}."))
    found = links_in(clean)
    if foreign := [u for u in found if _norm(u) != _norm(allowed)]:
        out.append(Problem("foreign_link", "It holds a link that is not the owner's own place: " + ", ".join(foreign[:2]) +
                           (". Write no link." if allowed is None else f". The only link you may write is {allowed}.")))
    elif allowed and (want.part, want.slot) in LINK_SLOTS and not found:
        out.append(Problem("no_link", f"It leaves out the owner's link. Write it once, exactly as {allowed}."))
    bare = _URL.sub(lambda m: "" if _norm(_tidy(m.group())) == _norm(allowed) else m.group(), clean)
    if kind := people.contact_info_problem(bare) or ("messenger" if _MESSENGER_ID.search(bare) else None):
        out.append(Problem("contact", f"It holds what reads as contact details ({kind}). Remove them: the owner "
                                      "reaches the person where they already are."))
    if left := list(dict.fromkeys([*owner_tasks.blanks(body), *_BRACKET.findall(body)])):
        out.append(Problem("blanks", f"It leaves blanks or placeholders in brackets ({', '.join(left)}). The owner "
                                     "sends it as it is, so write no bracket and write around what you do not know."))
    if owner_tasks.register_mixed(body):
        out.append(Problem("mixed_register", "It mixes polite (-yo) and formal (-mnida) endings. Keep one register."))
    if owner_tasks.voice_mixed(body):
        out.append(Problem("mixed_voice", "It speaks as I and as we in one text. The owner is one person: keep I."))
    if want.part == "message":
        out += _message_problems(s, facts, want, person, body)
    elif sentence := copy_rules.invented_experience(body):
        out.append(Problem("invented_experience", msg("kit.reason.invented", "en", found=sentence)))
    elif problem := copy_rules.public_copy_problem(body):
        out.append(Problem(problem[1], say(problem[0], "en")))
    if ask := channels.forbidden_reason([body]):
        out.append(Problem("asks_votes", say(ask, "en")))
    if spread := _PASS_ON.search(body):
        out.append(Problem("asks_spread", f"It asks the reader to pass the text on to several people ('{spread.group()}'). "
                                          "Write to this one person and ask nothing of the people they know."))
    return out


_SENTENCES = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_IN_THEIR_POST = re.compile("|".join(w for lang in LANGS for w in words.IN_THEIR_POST[lang]) or "(?!)")
_WITH_THE_OWNER = re.compile("|".join(w for lang in LANGS for w in words.WITH_THE_OWNER[lang]) or "(?!)")


def _past_talk(body: str) -> list[str]:
    """Where the text says the owner and this person talked before. A sentence that places the words in the person's own
    post ("글에서 이야기했던 송장 문제") is about what they wrote, not a talk with the owner, unless it also says the two
    of them ("우리", "함께")."""
    out: list[str] = []
    for sentence in _SENTENCES.split(body):
        if (talk := owner_tasks.past_talk(sentence)) and (_WITH_THE_OWNER.search(sentence) or not _IN_THEIR_POST.search(sentence)):
            out += talk
    return out


def _hangul(word: str) -> bool:
    return all(0xAC00 <= ord(c) <= 0xD7A3 for c in word)


def _distinct(word: str) -> bool:
    """A name's single word that a text holding it can be taken as naming the person: four letters or more (three in
    Hangul) and not an ordinary word that a handle can also be."""
    return not word.isdigit() and word.casefold() not in words.COMMON_WORDS and len(word) >= (3 if _hangul(word) else 4)


def _name_hit(person: Person, body: str) -> str | None:
    """The words of the text that name this person, or None: their `@handle`, their whole name or handle when it is two
    words or more, or a one-word name that is distinct (`_distinct`). A handle that is a common word is not a name
    unless it is written with its `@`."""
    end = r"(?![A-Za-z0-9_])"
    if person.handle and (m := re.search(rf"(?<![\w@.%+-])@{re.escape(person.handle.lstrip('@'))}{end}(?!\.[A-Za-z0-9_])",
                                         body, re.IGNORECASE)):
        return m.group()
    for name in dict.fromkeys(n for n in (person.display_name, person.handle) if n):
        parts = re.findall(r"\w+", unicodedata.normalize("NFKC", name))
        if len(parts) == 1 and not _distinct(parts[0]):
            continue
        if parts and (m := re.search(r"(?<![\w@])" + r"\W+".join(re.escape(w) for w in parts) + (end if parts[-1].isascii() else ""),
                                     body, re.IGNORECASE)):
            return m.group()
    return None


def _message_problems(s: Session, facts: Facts, want: Want, person: PersonFacts | None, body: str) -> list[Problem]:
    out = []
    if person is not None and not _reached(person.stage, "talked") and (talk := _past_talk(body)):
        out.append(Problem("past_talk", f"It says the owner and this person talked before ({', '.join(dict.fromkeys(talk))}). "
                                        "They have not talked yet."))
    if note := copy_rules.internal_note(body):
        out.append(Problem("internal_note", say(note, "en")))
    named = [hit for p in s.scalars(select(Person).where(Person.business_id == facts.business_id, Person.archived_at.is_(None),
                                                         Person.id != (want.person_id or 0)).order_by(Person.id))
             if (hit := _name_hit(p, body))]
    many = _MANY.search(body)
    if named or many:
        why = (f"it names someone else ({', '.join(repr(h) for h in dict.fromkeys(named))})" if named else
               f"it speaks to more than one person ('{many.group().strip()}')")
        out.append(Problem("several_people", f"A message is for one person and names no one else: {why}. Remove that, and "
                                             "write to this person alone."))
    if person is not None and (quoted := _unsaid_quotes(body, [*person.quotes, person.why], (facts.name,))):
        out.append(Problem("unsaid_quote", "It puts words in quotation marks that this person never wrote: "
                           + "; ".join(quoted[:2]) + ". Quote only their own words, or do not quote."))
    return out


# ------------------------------------------------------------------ the write

@dataclass(frozen=True)
class Result:
    body: str | None
    problem: Problem | None
    call_id: str | None


@dataclass
class Report:
    """What one write did: the texts stored, the texts refused with their code, and which of those refused ones left a
    good live text where it was (`kept`)."""
    written: list[Want]
    refused: dict[Want, str]
    kept: list[Want] = field(default_factory=list)


def _system() -> str:
    return (settings().prompts_dir / "first_ten_kit.md").read_text(encoding="utf-8")


def _prompt(f: Facts, wants: list[Want]) -> str:
    """The input for the marketing agent for these texts. Everything the owner or an outsider wrote reaches it fenced as
    data, so none of it can pass for the prompt's instructions. A person's block holds only what a message may rest on,
    and only the people these texts are for have one: the call for profile and invite wording sees no one's words."""
    asked = []
    for w in wants:
        person = f.person(w.person_id)
        language = NAMES[person.lang if person else f.lang]
        link = link_for(f.link, w, person)
        carries = ("may carry the owner's link" if link and (w.part, w.slot) not in LINK_SLOTS else
                   "must carry the owner's link" if link else "carries no link")
        asked.append(f"### {w.key}\nAt most {LIMITS[(w.part, w.slot)]} characters. Written in {language}. This text {carries}.")
    blocks = []
    for p in (p for p in f.persons if p.id in {w.person_id for w in wants}):
        said_lines = "\n".join(f"- {q}" for q in p.quotes) or "(they have written nothing yet)"
        blocks.append(f"### {p.ref}\nSource: {people.source_label(p.source, 'en')}. Stage: {people.stage_label(p.stage, 'en')}.\n"
                      + fenced(f"{p.ref} words", f"Their words:\n{said_lines}\nWhy this person: {p.why}\n"
                               f"Next step: {p.next_action or 'none'}"))
    return "\n\n".join([
        "## The business\n" + fenced("business", f"B-{f.business_id} {f.name}: {f.one_liner}"),
        "## From the plan\n" + fenced("plan", f.plan or "(nothing the owner has said or confirmed yet)"),
        "## What the owner said about themselves\n" + (fenced("owner", f.about_owner) if f.about_owner else
                                                      "Nothing. Write nothing about the owner as a person."),
        "## Who the customers are\n" + f.who,
        "## The owner's place\n" + (f"The only link you may write: {f.link}" if f.link else "The owner has no place yet. Write no link."),
        *(["## The people, one message each\n" + "\n\n".join(blocks)] if blocks else []),
        "## The texts to write\nWrite exactly these, one for each line, and no other.\n\n" + "\n\n".join(asked)])


def _again_prompt(prompt: str, failed: dict[Want, list[Problem]]) -> str:
    """The input once more, for the texts the server would not keep, each with why. A reason quotes the text's own
    words, which came from the business's material, so it is fenced as data."""
    lines = [f"### {w.key}\n" + fenced(f"refused {w.key}", "\n".join(f"- {p.detail}" for p in why))
             for w, why in failed.items()]
    return "\n\n".join([prompt, "## Write these again\nThe server did not keep your first text for each line below, for the "
                        "reason given. Write each of them again so that the reason no longer holds, and no text for any "
                        "other line. Rewrite the whole text, not only the part named: every rule applies again.\n\n"
                        + "\n\n".join(lines)])


_REF = re.compile(r"^PPL-(\d+)$")


def _ask(business_id: int, prompt: str, wants: list[Want]) -> tuple[dict[Want, str], str | None]:
    """The model's texts for these wants and the id of its call. The call holds no transaction."""
    out = run_agent("marketing", "kit", prompt, KitOut, project_id=None, business_id=business_id,
                    system_override=_system())
    call_id, wanted, got = last_call_id(), set(wants), {}
    for d in out.texts:
        ref = _REF.match((d.person or "").strip())
        key = Want(d.part, d.slot.strip(), int(ref.group(1)) if ref else None)
        if key in wanted and key not in got:
            got[key] = d.body.strip()
    return got, call_id


def _job_id() -> int | None:
    lease = queue.current_lease.get()
    return lease.job_id if lease is not None else None


def _basis(f: Facts, want: Want) -> dict[str, Any]:
    person = f.person(want.person_id)
    return {"quotes": list(person.quotes) if person else [], "profile": list(f.fingerprint),
            "link": link_for(f.link, want, person), "guess": f.guess, "person_seen": person.seen() if person else None}


def _store(s: Session, f: Facts, results: dict[Want, Result], job_id: int | None, now: datetime) -> Report:
    """Replace the live text of every slot written, in one transaction: the old row is marked replaced and a new one
    added, so a slot never holds two live texts. A refused result never takes the place of a live text that has a body:
    it is stored beside it as a row already replaced, which the read model shows as the failed attempt, and the live
    text stays live. A person the owner forgot while the model wrote gets nothing.

    Locks, in the order `people.archive` takes them (a person, then the kit rows that name them): the business row
    first, so two writes never interleave, then the person of each message, so a person forgotten while this runs is
    seen as forgotten here or has their new row scrubbed, never left with their words in a live row, then the slot's live
    row."""
    s.scalar(select(Business.id).where(Business.id == f.business_id).with_for_update())
    report = Report([], {})
    for want, result in results.items():
        if want.person_id is not None:
            person = s.scalar(select(Person).where(Person.id == want.person_id).with_for_update()
                              .execution_options(populate_existing=True))
            if person is None or person.archived_at is not None:
                continue
        live = s.scalar(select(KitText).where(
            KitText.business_id == f.business_id, KitText.part == want.part, KitText.slot == want.slot,
            KitText.person_id.is_(None) if want.person_id is None else KitText.person_id == want.person_id,
            KitText.replaced_at.is_(None)).with_for_update())
        kept = result.body is None and live is not None and live.body is not None
        if live is not None and not kept:
            live.replaced_at = now
            s.flush()
        person = f.person(want.person_id)
        s.add(KitText(business_id=f.business_id, part=want.part, slot=want.slot, person_id=want.person_id,
                      lang=person.lang if person else f.lang, body=result.body,
                      refused=result.problem.code if result.problem else None, basis=_basis(f, want), job_id=job_id,
                      call_id=result.call_id, made_at=now, replaced_at=now if kept else None))
        if result.body is not None:
            report.written.append(want)
        else:
            report.refused[want] = result.problem.code
            if kept:
                report.kept.append(want)
    return report


def _write(business_id: int, facts: Facts, wants: list[Want]) -> dict[Want, Result]:
    """One call for these texts and, for each the checks refuse, one more asking for it again with the reasons. A text
    still refused after that is a result with its first reason and no body."""
    prompt = _prompt(facts, wants)
    results: dict[Want, Result] = {}
    todo = list(wants)
    problems: dict[Want, list[Problem]] = {}
    for round_ in (1, 2):
        got, call_id = _ask(business_id, prompt if round_ == 1 else _again_prompt(prompt, problems), todo)
        with session_scope() as s:
            for want in todo:
                found = judge(s, facts, want, got[want]) if want in got else [Problem("no_draft", "You wrote no text for this line.")]
                if found:
                    problems[want] = found
                    results[want] = Result(None, found[0], call_id)
                else:
                    problems.pop(want, None)
                    results[want] = Result(got[want], None, call_id)
        todo = list(problems)
        if not todo:
            break
    return results


def run(business_id: int, parts: list[str], person_ids: list[int], *, lang: Lang, now: datetime | None = None) -> Report:
    """One write of the kit, with what the owner reads in `lang`. The wording of the profile and the invites is one call
    and the messages another, so the first never sees a person's words. A model call holds no transaction, and a
    `MustWait` out of one leaves nothing written, so the job simply runs again later. A text the checks refuse gets one
    rewrite round, and what is still refused is stored with its reason, or noted beside the live text it did not replace."""
    now = now or datetime.now(UTC)
    with session_scope() as s:
        facts = _gather(s, business_id, parts, person_ids, lang)
    if facts is None or not facts.wants:
        return Report([], {})
    results: dict[Want, Result] = {}
    for group in ([w for w in facts.wants if w.part != "message"], [w for w in facts.wants if w.part == "message"]):
        if group:
            results |= _write(business_id, facts, group)
    results = {w: results[w] for w in facts.wants}
    with session_scope() as s, activity.as_actor("marketing"):
        report = _store(s, facts, results, _job_id(), now)
        if report.written or report.refused:
            activity.record(s, verb="kit_prepared", entity="business", entity_id=business_id, project_id=None,
                            business_id=business_id, actor="marketing",
                            summary=msg("kit.activity.prepared", lang, id=business_id, written=len(report.written),
                                        refused=len(report.refused)),
                            data={"written": [w.key for w in report.written],
                                  "refused": {w.key: c for w, c in report.refused.items()},
                                  "kept": [w.key for w in report.kept]})
    return report


# ------------------------------------------------------------------ the owner's click

def _business(s: Session, business_id: int, *, lock: bool = False) -> Business:
    q = select(Business).where(Business.id == business_id)
    business = s.scalar(q.with_for_update() if lock else q)
    if business is None:
        raise KitNotFound(said("kit.error.not_found", id=business_id))
    return business


def writing(s: Session, business_id: int) -> bool:
    """Whether a `kit_prepare` job for the business is queued, parked or running."""
    return s.scalar(select(Job.id).where(Job.kind == "kit_prepare", Job.business_id == business_id, queue.in_flight())
                    .limit(1)) is not None


def request(s: Session, business_id: int, *, parts: list[str] | None = None, person_id: int | None = None) -> dict[str, Any]:
    """The owner asks the company to write the kit, or some of it: every part by default, or `parts`; a `person_id`
    writes that one person's message alone. Needs a plan, and answers `KitConflict` while a write is queued or running,
    so two clicks queue one job. The business row is locked while that is read, so two clicks that arrive together
    still queue one. Raises `KitError`."""
    business = _business(s, business_id, lock=True)
    asked = list(PARTS) if parts is None and person_id is None else ["message"] if parts is None else list(parts)
    if unknown := [p for p in asked if p not in PARTS]:
        raise KitError(said("kit.error.bad_part", part=unknown[0], options=", ".join(PARTS)))
    parts = [p for p in PARTS if p in asked]
    if person_id is not None and "message" not in parts:
        raise KitError(said("kit.error.person_needs_message"))
    if business.status == "ARCHIVED" or roadmap.approved_brief(s, business_id) is None:
        raise KitConflict(said("kit.error.no_plan"))
    if writing(s, business_id):
        raise KitConflict(said("kit.error.busy"))
    ids: list[int] = []
    if "message" in parts:
        ids = [_one(s, business_id, person_id)] if person_id is not None else [
            p["id"] for p in people.today(s, business_id, lang="en") if wants_draft(p["source"], p["stage"])]
    if not _wants(parts, ids):
        raise KitConflict(said("kit.error.nothing"))
    job = queue.enqueue(s, "kit_prepare", {"business_id": business_id, "parts": parts, "person_ids": ids}, max_attempts=2,
                        by_owner=True)
    return {"queued": job is not None, "job_id": job.id if job else None, "parts": parts, "person_ids": ids}


def _one(s: Session, business_id: int, person_id: int) -> int:
    """The person a message is asked for, who must be on this business's list, still there, and one who gets a draft."""
    person = s.get(Person, person_id)
    if person is None or person.business_id != business_id:
        raise KitNotFound(said("kit.error.person_not_found", id=person_id))
    if person.archived_at is not None or person.stage == people.DROPPED:
        raise KitError(said("kit.error.person_gone", id=person_id))
    if not wants_draft(person.source, person.stage):
        raise KitError(said("kit.error.public_first", id=person_id))
    return person.id


# ------------------------------------------------------------------ forgetting a person

def forget_person(s: Session, person_id: int) -> None:
    """The scrub of `people.archive`: every text written for the person, replaced ones too, loses its words and its
    quotes and says it was forgotten. The rows stay, as the person's own do."""
    for row in s.scalars(select(KitText).where(KitText.person_id == person_id)):
        row.body, row.refused, row.basis = None, "forgotten", {**row.basis, "quotes": []}


# ------------------------------------------------------------------ the read model

def _live(s: Session, business_id: int) -> dict[tuple[str, str, int | None], KitText]:
    return {(r.part, r.slot, r.person_id): r for r in s.scalars(select(KitText).where(
        KitText.business_id == business_id, KitText.replaced_at.is_(None)))}


def _tries(s: Session, business_id: int) -> dict[tuple[str, str, int | None], KitText]:
    """The latest refused attempt of each slot that did not take its live text's place (`_store` stores it already
    replaced), or one a later success replaced: `_view` tells them apart by when each was made."""
    q = select(KitText).where(KitText.business_id == business_id, KitText.replaced_at.is_not(None),
                              KitText.refused.is_not(None), KitText.refused != "forgotten").order_by(KitText.made_at, KitText.id)
    return {(r.part, r.slot, r.person_id): r for r in s.scalars(q)}


def _view(row: KitText | None, *, stale: bool, lang: Lang, tried: KitText | None = None) -> dict[str, Any] | None:
    """A text as the console reads it. `last_refused` is set when a text with a body stands and a later attempt to write
    it again was refused: what was refused, why in the owner's language, and when."""
    if row is None:
        return None
    basis = row.basis or {}
    failed = tried if tried is not None and row.body is not None and tried.made_at > row.made_at else None
    return {"id": row.id, "body": row.body, "refused": row.refused,
            "refused_text": msg(f"kit.refused.{row.refused}", lang) if row.refused else None, "lang": row.lang,
            "chars": len(row.body) if row.body else 0, "limit": LIMITS[(row.part, row.slot)], "stale": stale,
            "guess": bool(basis.get("guess")), "made_at": row.made_at,
            "basis_view": {"quotes": basis.get("quotes") or [], "link": basis.get("link")},
            "last_refused": {"refused": failed.refused, "refused_text": msg(f"kit.refused.{failed.refused}", lang),
                             "at": failed.made_at} if failed else None}


def _person_stale(row: KitText | None, person: dict[str, Any]) -> bool:
    if row is None or row.refused == "forgotten":
        return False
    return (row.basis or {}).get("person_seen") != _seen(person["stage"], len(person["quotes"] or []), person["next_action_at"])


def _place_stale(row: KitText | None, fp: tuple[str, ...], link: str | None) -> bool:
    """A profile or invite text is stale once the customer description differs from what it was written from, or, for a
    text that carries the owner's place, the place does."""
    if row is None:
        return False
    basis = row.basis or {}
    return (list(fp) != (basis.get("profile") or [])
            or ((row.part, row.slot) in LINK_SLOTS and _norm(link) != _norm(basis.get("link"))))


def can_write(s: Session, business: Business) -> bool:
    """Whether the owner may ask for the kit now: no write is running, the business is not archived and a plan is approved."""
    return not writing(s, business.id) and business.status != "ARCHIVED" and roadmap.approved_brief(s, business.id) is not None


def prepared(rows: dict[tuple[str, str, int | None], KitText]) -> bool:
    return any(r.refused != "forgotten" for r in rows.values())


def _job_state(s: Session, business_id: int, lang: Lang) -> dict[str, Any] | None:
    """The business's latest `kit_prepare` job as the owner reads it: its status; for a job that failed or was cancelled,
    a sentence from the catalog (the row's own error text is for the log, not for the owner); and for one parked on a
    usage limit, until when and the sentence that says so, as an idea waiting on one does."""
    job = s.scalar(select(Job).where(Job.kind == "kit_prepare", Job.business_id == business_id).order_by(Job.id.desc()).limit(1))
    if job is None:
        return None
    parked = job.status == "queued" and job.wait_reason is not None and job.run_after > datetime.now(UTC)
    until = usage.local_time(job.run_after, app_settings.effective().usage.timezone, lang) if parked else None
    return {"status": job.status,
            "error_text": msg(f"kit.job.{job.status}", lang) if job.status in ("failed", "cancelled") else None,
            "waiting_until": job.run_after if parked else None,
            "waiting_text": msg("kit.job.waiting", lang, reason=msg(f"usage.reason.{job.wait_reason}", lang), until=until) if parked else None}


def _prepared_at(s: Session, business_id: int) -> datetime | None:
    """When a write last stored a text, from the `kit_prepared` activity lines that wrote one: what the console reads to say
    the kit was updated, which it must not say for a write that stored nothing."""
    return s.scalar(select(func.max(ActivityEvent.at)).where(
        ActivityEvent.verb == "kit_prepared", ActivityEvent.business_id == business_id,
        func.jsonb_array_length(ActivityEvent.data["written"]) > 0))


def read(s: Session, business_id: int, *, lang: Lang, now: datetime | None = None) -> dict[str, Any]:
    """The kit as the console reads it: the milestone, up to three people to reach today each with a draft or why there
    is none, the profile and invite wording, the owner's place, whether a write is running and how the last one went
    (`job`), and when a write last stored a text (`prepared_at`). A text is `stale` when the person, the customer
    description or the owner's place changed since it was written. Raises `KitNotFound`."""
    business = _business(s, business_id)
    prof = profile.of(s, business_id)
    fp, link = fingerprint(prof), own_link(business)
    rows, tries = _live(s, business_id), _tries(s, business_id)
    today = []
    for person in people.today(s, business_id, lang=lang, now=now):
        row = rows.get(("message", "p", person["id"]))
        why_not = None if row is not None else ("public_first" if not wants_draft(person["source"], person["stage"])
                                                else "not_written")
        today.append({"person": person, "reason": person["reason"], "reason_text": msg(f"kit.today.{person['reason']}", lang),
                      "draft": _view(row, stale=_person_stale(row, person), lang=lang,
                                   tried=tries.get(("message", "p", person["id"]))),
                      "no_draft_reason": why_not, "no_draft_text": msg(f"kit.no_draft.{why_not}", lang) if why_not else None})
    milestone = next(m for m in tracking.standing_for(s, business_id, lang=lang, now=now)["milestones"] if m["n"] == MILESTONE)
    busy = writing(s, business_id)
    return {
        "business_id": business_id,
        "milestone": {"n": milestone["n"], "have": milestone["have"], "estimated": milestone["estimated"]},
        "today": today,
        "profile": {slot: _view(rows.get(("profile", slot, None)), stale=_place_stale(rows.get(("profile", slot, None)), fp, link),
                                lang=lang, tried=tries.get(("profile", slot, None))) for slot in SLOTS["profile"]},
        "invite": {slot: _view(rows.get(("invite", slot, None)), stale=_place_stale(rows.get(("invite", slot, None)), fp, link),
                               lang=lang, tried=tries.get(("invite", slot, None))) for slot in SLOTS["invite"]},
        "link": {"url": link, "state": "ready" if link else "no_place"},
        "owner_said": bool(_plan_lines(s, business_id, ("background",))),
        "prepared": prepared(rows),
        "prepared_at": _prepared_at(s, business_id),
        "writing": busy,
        "can_write": can_write(s, business),
        "job": _job_state(s, business_id, lang),
    }


def reach_today(s: Session, business_id: int, *, lang: Lang, now: datetime | None = None) -> list[dict[str, Any]]:
    """The people the Today screen's kit item is about: today's people, or none when one of them already has an open
    to-do of their own, which the owner will see in the list anyway, or when the kit can neither be written (no approved
    plan, a write running) nor has anything prepared to read: the line would open a section with no way forward."""
    business = _business(s, business_id)
    if not can_write(s, business) and not prepared(_live(s, business_id)):
        return []
    picked = people.today(s, business_id, lang=lang, now=now)
    return [] if any(p["open_tasks"] for p in picked) else picked
