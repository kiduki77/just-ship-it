"""The kickoff conversation (시작 대화): the owner and the CEO fill in the business's brief by question and playback.

A session belongs to one business and moves along `TRANSITIONS`. The CEO speaks in turns, one model call each,
posted by the `kickoff_turn` job; the owner answers through the console. The brief is the `BusinessBrief` draft:
the CEO adds lines as `said` or `guess`. A `said` line must carry a quote this module finds in the owner's own
words (an answer, the opening idea or the pasted material), whatever section the owner was answering, and the quote
must be about the line (`owner_quote.problem`). Without that it is kept as a guess and the downgrade is recorded on
the turn. A quote from an answer to a "짐작이라도" ask is a guess in any section. A quote from a chip the owner picked
makes a `confirmed` line, in the section that turn asked about. An older quote never replaces a newer owner line.
Only this module writes `confirmed` for the owner's agreement to a proposal and `unknown` (the owner said 모르겠어요). A turn is posted in one transaction after the model call,
and `(session, n)` is unique, so a retried or reclaimed job posts it once. A "맞아요" confirms only the lines listed
in the turn's `promote`, with or without words added. Added words are the owner's, so the CEO takes one more turn
(at the closing, one more closing playback) to fold them in. The CEO holds the line once (`holds_line`): the words the
owner turned a step down with are no plan line while that proposal waits, nor after the owner says yes to it.
"""
from __future__ import annotations

import copy
import logging
import re
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, NamedTuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from justshipit.agents.executor import AgentFailed, run_agent
from justshipit.contracts.kickoff import BriefPatchLine, KickoffTurnOut
from justshipit.contracts.product import ExistingProduct
from justshipit.db.models import (
    KICKOFF_LIVE,
    Business,
    BusinessBrief,
    Document,
    Job,
    KickoffSession,
    KickoffTurn,
)
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, NAMES, Lang, msg
from justshipit.i18n.match import plain
from justshipit.i18n.match.kickoff import LINE_TAGS, SCREEN_CHIPS
from justshipit.i18n.said import say
from justshipit.services import (
    activity,
    app_settings,
    documents,
    existing_product,
    idea_review,
    owner_quote,
    queue,
    roadmap,
    usage,
)
from justshipit.services.control import ControlError, NotFound
from justshipit.services.fence import fenced
from justshipit.services.profile import infer as profile_infer
from justshipit.services.profile import store as profile_store
from justshipit.services.roadmap import BRIEF_BY_KEY, BRIEF_SECTIONS, section_state
from justshipit.services.strengths import Strength, known
from justshipit.services.usage import MustWait
from justshipit.settings import settings

log = logging.getLogger(__name__)

AGREE = "yes"
MAX_MATERIAL_CHARS = 20000
PLAYBACK_DUE_AFTER = 4
TARGET_TURNS = 12
TURN_CAP = 25
CONTINUE_EXTRA = 5
# A file holds far more text than an answer, so two common words are found in it by chance. A quote from a file must
# be a sentence's worth of the owner's words and share more than one word with the line.
FILE_QUOTE_MIN_CHARS = 20
FILE_QUOTE_MIN_TOKENS = 4
FILE_QUOTE_SHARED = 2
# What the CEO reads of the files each turn, all files together. Quotes are still matched against all of their text.
KICKOFF_FILE_CHARS = 40_000
# The opening question the server asks, not the CEO: what the owner is good at, while they have never said. It is turn
# 0, before the CEO's first, so it burns none of the CEO's turns, and the CEO's first turn waits for its answer, so
# every question the CEO asks already fits the owner (`strengths.prompt_section`). The answer goes where Settings >
# About you keeps it.
STRENGTHS_TURN = 0
NONE_YET = "none"


def _chip_key(chip: str) -> str:
    return plain(chip).casefold().strip(" .!?…")


_SCREEN_CHIPS = {_chip_key(c) for chips in SCREEN_CHIPS.values() for c in chips}
_TAIL = re.compile(r"\s+·\s+([^·]{1,40})$")
_TAG = re.compile("|".join(f"(?:{p})" for tags in LINE_TAGS.values() for p in tags), re.IGNORECASE)


def _untagged(text: str) -> str:
    """The line without the "· a guess" tag an older prompt had the CEO append. Where a line came from is its
    provenance alone: a tag in the text would still say "a guess" after the owner confirmed it."""
    text = text.strip()
    while (m := _TAIL.search(text)) and _TAG.fullmatch(m.group(1).strip()):
        text = text[:m.start()].rstrip()
    return text


class Status(StrEnum):
    OPEN = "OPEN"
    WAITING_OWNER = "WAITING_OWNER"
    PAUSED = "PAUSED"
    DONE = "DONE"
    ABANDONED = "ABANDONED"


# OPEN: the CEO is preparing a turn. WAITING_OWNER: a turn is posted and unanswered. PAUSED: the owner chose
# "later", or the CEO could not answer (see `note`). Moves to OPEN enqueue the next turn. ABANDONED only moves
# back to PAUSED, by `reopen`. A DONE conversation whose gate sections are unsettled goes back to OPEN or
# WAITING_OWNER, by `continue_on`.
TRANSITIONS: dict[Status, set[Status]] = {
    Status.OPEN: {Status.WAITING_OWNER, Status.PAUSED, Status.ABANDONED},
    Status.WAITING_OWNER: {Status.OPEN, Status.PAUSED, Status.DONE, Status.ABANDONED},
    Status.PAUSED: {Status.OPEN, Status.WAITING_OWNER, Status.DONE, Status.ABANDONED},
    Status.DONE: {Status.OPEN, Status.WAITING_OWNER},
    Status.ABANDONED: {Status.PAUSED},
}
LIVE = {Status.OPEN, Status.WAITING_OWNER, Status.PAUSED}
_WHY_NOT: dict[Status, str] = {
    Status.OPEN: "kickoff.why_not.open",
    Status.WAITING_OWNER: "kickoff.why_not.waiting_owner",
    Status.PAUSED: "kickoff.why_not.paused",
    Status.DONE: "kickoff.why_not.done",
    Status.ABANDONED: "kickoff.why_not.abandoned",
}


def _now() -> datetime:
    return datetime.now(UTC)


def source(session_id: int, n: int | str) -> str:
    return f"kickoff:{session_id}:{n}"


def _turn_of(src: str | None) -> int | None:
    """The turn a line was written in, from its `source`. Material, an attached file and the opening idea have none
    that counts."""
    if not (src or "").startswith("kickoff:"):
        return None
    tail = (src or "").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() and int(tail) > 0 else None


def closing_question(lang: Lang) -> str:
    """The question the closing turn always ends with. The server writes it, so it is in the owner's language."""
    return msg("kickoff.closing.question", lang)


def _move(sess: KickoffSession, to: Status, lang: Lang, *, end_reason: str | None = None) -> None:
    cur = Status(sess.status)
    if to == cur:
        return
    if to not in TRANSITIONS[cur]:
        raise ControlError(msg(_WHY_NOT[cur], lang))
    sess.status = to.value
    if to in (Status.DONE, Status.ABANDONED):
        sess.ended_at, sess.end_reason = _now(), end_reason


def _session(s: Session, sid: int, lang: Lang, *, lock: bool = True) -> KickoffSession:
    q = select(KickoffSession).where(KickoffSession.id == sid)
    sess = s.scalar(q.with_for_update() if lock else q)
    if sess is None:
        raise NotFound(msg("kickoff.not_found", lang, id=sid))
    return sess


def _turns(s: Session, sid: int) -> list[KickoffTurn]:
    return list(s.scalars(select(KickoffTurn).where(KickoffTurn.session_id == sid).order_by(KickoffTurn.n)))


def _project_manager_turns(turns: list[KickoffTurn]) -> list[KickoffTurn]:
    """The CEO's turns: the plan's conversation. The strengths question is the server's and fills no plan line."""
    return [t for t in turns if t.n != STRENGTHS_TURN]


# ------------------------------------------------------------------ the brief draft

def _draft(s: Session, sess: KickoffSession, lang: Lang) -> BusinessBrief:
    brief = roadmap.draft_brief(s, sess.business_id, lock=True)
    if brief is None:
        raise ControlError(msg("kickoff.no_draft", lang))
    return brief


def _section(sections: list[dict], key: str) -> dict:
    found = next((x for x in sections if x["key"] == key), None)
    if found is None:
        found = {"key": key, "title": BRIEF_BY_KEY[key].title, "lines": []}
        sections.append(found)
    return found


def _line(text: str, provenance: str, src: str) -> dict:
    return {"id": f"l-{secrets.token_hex(3)}", "text": text.strip(), "provenance": provenance, "source": src}


def _unknown_id(key: str) -> str:
    return f"a-unk-{key}"


def _set_unknown(sections: list[dict], assumptions: list[dict], key: str, src: str, lang: Lang) -> None:
    """The section stays open as a research bet: a 모름 line and a high-importance assumption with no evidence."""
    spec = BRIEF_BY_KEY[key]
    sec = _section(sections, key)
    if section_state(sec["lines"]) in ("settled", "unknown"):
        return
    sec["lines"] = [x for x in sec["lines"] if x["provenance"] != "guess"] + [
        _line(msg("kickoff.unknown.line", lang), "unknown", src)]
    assumptions[:] = [a for a in assumptions if a["id"] != _unknown_id(key)] + [
        {"id": _unknown_id(key), "text": msg("kickoff.unknown.assumption", lang, title=spec.text("title", lang)),
         "importance": "high" if spec.required else "mid", "evidence": msg("kickoff.unknown.no_evidence", lang),
         "status": "open", "bet_id": None}]


def _guess_ask_due(turns: list[KickoffTurn]) -> str | None:
    """The required section the owner just answered 모르겠어요 for the first time, so the CEO asks once for a guess."""
    if not turns or turns[-1].owner_choice != "unknown":
        return None
    last = turns[-1]
    required = last.section in BRIEF_BY_KEY and BRIEF_BY_KEY[last.section].required
    asked = any(t.n < last.n and t.section == last.section and t.owner_choice == "unknown" for t in turns)
    return last.section if required and not asked else None


def _guess_reply_turns(turns: list[KickoffTurn]) -> list[KickoffTurn]:
    """The answered turns whose reply is the answer to a "짐작이라도" ask, whatever section the reply fills."""
    return [t for i, t in enumerate(turns)
            if t.answered_at is not None and t.owner_choice != "unknown" and _guess_ask_due(turns[:i]) is not None]


def _guess_replies(turns: list[KickoffTurn]) -> set[int]:
    """The turns that build on a reply to a "짐작이라도" ask. A guess they write is not offered for confirmation:
    the owner guessed, and the design keeps a guess a guess."""
    return {t.n + 1 for t in _guess_reply_turns(turns)}


def _guess_reply_sources(turns: list[KickoffTurn]) -> set[str]:
    """The sources of those replies. What the owner said there is a guess in every section and at every later turn."""
    return {source(t.session_id, t.n) for t in _guess_reply_turns(turns)}


class OwnerWords(NamedTuple):
    """Something the owner wrote, normalized. `kind` is idea, material, answer or chip. A chip is the CEO's text the
    owner picked, and `section` is the section its turn asked about."""
    src: str
    text: str
    kind: str
    section: str | None = None


def owner_words(sess: KickoffSession, business: Business, turns: list[KickoffTurn],
                docs: list[Document] = ()) -> list[OwnerWords]:
    """Everything the owner wrote in this session, oldest first: the opening idea, the pasted material, each page of
    each attached file, each answer and each chip the owner picked. Nothing else the CEO wrote is here. A turn's chip
    comes before its answer, so the owner's own text wins a tie."""
    words = [OwnerWords(source(sess.id, 0), business.one_liner, "idea")]
    if sess.material:
        words.append(OwnerWords(source(sess.id, "material"), sess.material, "material"))
    words += [OwnerWords(src, text, "document") for src, text in documents.words(list(docs))]
    for t in turns:
        src = source(sess.id, t.n)
        if t.owner_choice and t.owner_choice not in (AGREE, "unknown"):
            picked = t.owner_choice in t.chips
            words.append(OwnerWords(src, t.owner_choice, "chip" if picked else "answer", t.section if picked else None))
        words.append(OwnerWords(src, t.owner_text or "", "answer"))
    normalized = [w._replace(text=owner_quote.norm(w.text)) for w in words]
    return [w for w in normalized if w.text]


def find_quote(quote: str, words: list[OwnerWords]) -> tuple[OwnerWords, str] | None:
    """The owner's words holding `quote` and the normalized quote, or None when the owner never wrote it. The
    newest match wins."""
    q = owner_quote.norm(quote)
    return next(((w, q) for w in reversed(words) if q and q in w.text), None)


def _turn_no(src: str) -> int:
    """How late a source is in the conversation. The opening idea and the pasted material come before turn 1."""
    return _turn_of(src) or 0


class Verdict(NamedTuple):
    """What the server makes of a line the CEO marked `said`. `source` and `quote` are the owner's words behind a
    line that stays the owner's. `reason`, a code the console words for the owner, and `why`, the record in words,
    say what the server changed. Both are None when the line stays `said`."""
    provenance: str
    source: str | None = None
    quote: str | None = None
    reason: str | None = None
    why: str | None = None


def _judge(p: BriefPatchLine, text: str, words: list[OwnerWords], guess_sources: set[str]) -> Verdict:
    section, hit = p.section, find_quote(p.owner_quote, words)
    if hit is None:
        return Verdict("guess", reason="no_quote",
                       why="the quote is not in the owner's words, so the line is a guess, not said")
    w, q = hit
    if problem := owner_quote.problem(q, text, gate=BRIEF_BY_KEY[section].gate, said=w.text, gloss=p.quote_gloss):
        return Verdict("guess", reason="not_backing", why=f"{say(problem, 'en')}; the line is a guess, not said")
    if w.kind == "document" and not _file_quote_backs(q, text, p.quote_gloss):
        return Verdict("guess", reason="not_backing",
                       why=f"a quote from a file needs at least {FILE_QUOTE_MIN_CHARS} characters, "
                           f"{FILE_QUOTE_MIN_TOKENS} words and {FILE_QUOTE_SHARED} words shared with the line; the line "
                           "is a guess, not said")
    if w.src in guess_sources:
        return Verdict("guess", reason="guess_reply",
                       why="the quote answers an ask for a guess, so the line is a guess, not said")
    if w.kind == "chip":
        if w.section != section:
            return Verdict("guess", reason="chip_elsewhere",
                           why="a picked chip is the Project Manager's text, so outside the section its turn asked about it is a "
                               "guess")
        return Verdict("confirmed", w.src, q, reason="chip",
                       why="a picked chip is the Project Manager's text, so the line is confirmed, not said")
    return Verdict("said", w.src, q)


def _file_quote_backs(quote: str, text: str, gloss: str) -> bool:
    q = owner_quote.tokens(quote) | owner_quote.tokens(owner_quote.norm(gloss))
    return (owner_quote.length(quote) >= FILE_QUOTE_MIN_CHARS and len(owner_quote.tokens(quote)) >= FILE_QUOTE_MIN_TOKENS
            and len(q & owner_quote.tokens(owner_quote.norm(text))) >= FILE_QUOTE_SHARED)


# What rule 16 holds the line on: a test with real people (how it starts) and a narrower who-it-is-for. Other words in
# the same answer, a red line or the owner's time, are the owner's and go in as said.
REFUSABLE = ("path", "customer_problem")


def _apply_patch(sections: list[dict], assumptions: list[dict], patch: list[BriefPatchLine], src: str, *,
                 words: list[OwnerWords], guess_sources: set[str], set_aside: frozenset[str] = frozenset()) -> list[dict]:
    """Add or replace the CEO's lines and return what the server changed in them. A `said` line stays `said` only
    when its `owner_quote` is in the owner's words, is about the line, and is not the answer to a "짐작이라도" ask;
    its source is then the turn the quote came from. A quote from a chip the owner picked gives `confirmed` in the
    section that turn asked about. Anything else is stored as a guess. A replacement may take a guess or a 모름 line,
    or, for a line with an owner's quote, an owner line of the same section when that quote is from a later turn.
    The owner's own lines stay otherwise. A `path` or `customer_problem` line resting on the owner's words from a turn in `set_aside` is left out.
    """
    notes: list[dict] = []
    for p in patch:
        text = _untagged(p.text)
        if not text:
            continue
        if p.section == "owner_words":
            notes.append({"what": "skipped", "section": p.section, "text": text,
                          "why": "only the server writes the owner's own words"})
            continue
        prov, line_src, quote = "guess", src, None
        if p.provenance == "said":
            verdict = _judge(p, text, words, guess_sources)
            prov, line_src, quote = verdict.provenance, verdict.source or src, verdict.quote
            if line_src in set_aside and p.section in REFUSABLE:
                notes.append({"what": "held", "section": p.section, "text": text,
                              "why": "the owner turned a step down with these words, and the Project Manager's one proposal back "
                                     "is waiting for an answer or the owner took the step, so they are no plan line"})
                continue
            if verdict.why:
                notes.append({"what": "downgraded", "section": p.section, "text": text, "to": prov,
                              "reason": verdict.reason, "why": verdict.why})
        sec = _section(sections, p.section)
        if p.replaces_line_id:
            old = next((x for x in sec["lines"] if x["id"] == p.replaces_line_id), None)
            owner_made = prov in ("said", "confirmed")
            if old is not None and old["provenance"] in ("guess", "unknown"):
                sec["lines"] = [x for x in sec["lines"] if x is not old]
            elif old is not None and owner_made and old["provenance"] in ("said", "confirmed"):
                if _turn_no(line_src) > _turn_no(old["source"]):
                    sec["lines"] = [x for x in sec["lines"] if x is not old]
                else:
                    prov, line_src, quote = "guess", src, None
                    notes.append({"what": "kept", "line_id": p.replaces_line_id, "section": p.section, "text": text,
                                  "why": "the quote is older than the owner's line it would replace, so it was added "
                                         "as a guess instead"})
            else:
                notes.append({"what": "kept", "line_id": p.replaces_line_id, "section": p.section, "text": text,
                              "why": "not a guess or unknown line of the same section, so it was left as it was"})
        sec["lines"] = [x for x in sec["lines"] if x["provenance"] not in ("unknown", "blank")]
        line = _line(text, prov, line_src)
        if quote:
            line["quote"] = quote
        if p.section == "known_people" and (person := " ".join(p.person.split())):
            line["person"] = person
        if target := roadmap.checked_target(p.section, text, p.target):
            line["target"] = target
        sec["lines"].append(line)
        assumptions[:] = [a for a in assumptions if a["id"] != _unknown_id(p.section)]
    return notes


def required_remaining(sections: list[dict]) -> list[str]:
    """Required sections the owner has not settled, as `roadmap.settles` reads them."""
    by_key = roadmap.lines_by_key(sections)
    return [spec.key for spec in BRIEF_SECTIONS
            if spec.required and not roadmap.settles(spec, section_state(by_key.get(spec.key, [])))]


def _promotable(sections: list[dict], turns: list[KickoffTurn], n: int, *, closing: bool) -> list[dict]:
    """The guess lines a "맞아요" to turn `n` confirms: a proposal's own lines, or at the closing every guess in the
    brief, a line from an earlier proposal the owner corrected included. The closing lists them all to the owner
    above 맞아요, so a line the correction did not touch is confirmed there and one it did is the owner's to fix
    there. Left out: a guess built on a reply to a "짐작이라도" ask."""
    tainted = _guess_replies(turns)
    out = []
    for sec in sections:
        for line in sec["lines"]:
            made = _turn_of(line["source"])
            if line["provenance"] != "guess" or made is None or made in tainted:
                continue
            if closing or made == n:
                out.append({"id": line["id"], "section": sec["key"], "title": sec["title"], "text": line["text"]})
    return out


def _held_refusals(sess: KickoffSession, turns: list[KickoffTurn], n: int, out: KickoffTurnOut,
                   closing: bool) -> tuple[frozenset[str], str | None]:
    """The turns whose words stay out of the plan, and the one this turn holds, if it holds the line. The CEO holds
    the line once in a chat (rule 16), on a proposal right after the owner turned a step down: those words wait for
    the owner's answer to it, and a yes to it sets them aside for good. A no leaves them to the CEO, who plans the
    owner's way."""
    held = next((note for t in turns for note in t.patch_notes or [] if note.get("what") == "holds_line"), None)
    if held is not None:
        took_step = any(t.owner_choice == AGREE for t in turns if source(sess.id, t.n) == held["turn"])
        return frozenset({held["refusal"]} if took_step else ()), None
    if out.holds_line and out.kind == "proposal" and not closing and n > 1:
        return frozenset({source(sess.id, n - 1)}), source(sess.id, n - 1)
    return frozenset(), None


def _promote(sections: list[dict], line_ids: set[str]) -> None:
    for sec in sections:
        for line in sec["lines"]:
            if line["id"] in line_ids and line["provenance"] == "guess":
                line["provenance"] = "confirmed"


# ------------------------------------------------------------------ opening, answering, pausing

def _enqueue_turn(s: Session, sess: KickoffSession, n: int, close: bool = False) -> None:
    """One job per (session, n). A resume after the first job failed gets a key of its own."""
    base = f"kickoff:{sess.id}:{n}"
    earlier = s.scalar(select(func.count()).select_from(Job).where(
        or_(Job.idempotency_key == base, Job.idempotency_key.like(f"{base}:%")))) or 0
    queue.enqueue(s, "kickoff_turn", {"session_id": sess.id, "n": n, "close": close},
                  idempotency_key=base if not earlier else f"{base}:{earlier}")


def open_session(s: Session, *, name: str, one_liner: str, material: str | None = None,
                 document_ids: list[int] | None = None, lang: Lang, product: ExistingProduct | None = None) -> dict:
    """Open a business and its kickoff. With `product` (one the owner already has, cloned to products/) the business
    holds it from the start, the CPO reads the code first, and the chat's first turn waits for that report. The
    idea panel, which judges a new idea, does not run."""
    one_liner = one_liner.strip()
    if not one_liner and product is None:
        raise ControlError(msg("kickoff.need_idea", lang))
    material = (material or "").strip() or None
    if material and len(material) > MAX_MATERIAL_CHARS:
        raise ControlError(msg("kickoff.material_too_long", lang, max=f"{MAX_MATERIAL_CHARS:,}"))
    if product is not None and (other := existing_product.pinned_by(s, product.repo)) is not None:
        raise ControlError(msg("product.import.in_use", lang, id=other, repo=product.repo))
    business = roadmap.create_business(s, name=name, one_liner=one_liner, lang=lang)
    sess = KickoffSession(business_id=business.id, status=Status.OPEN.value, material=material)
    s.add(sess)
    s.flush()
    documents.attach(s, document_ids or [], business_id=business.id, session_id=sess.id, lang=lang)
    sections = roadmap.empty_sections()
    if one_liner:
        _section(sections, "one_liner")["lines"].append(_line(one_liner[:1000], "said", source(sess.id, 0)))
    if product is not None:
        business.product = product.model_dump(mode="json")
        _section(sections, "background")["lines"].append(_line(
            msg("product.brief.imported", lang, branch=product.base_branch, commit=product.commit, repo=product.repo), "said", source(sess.id, 0)))
    roadmap.new_brief_version(s, business.id, sections=sections, assumptions=[], author="project_manager", lang=lang)
    asks = app_settings.reader()[1] is None
    if asks:
        s.add(KickoffTurn(session_id=sess.id, n=STRENGTHS_TURN, kind="question", section=None, chips=[],
                          ceo_text=msg("kickoff.strengths.question", lang), promote=[], patch_notes=[]))
        _move(sess, Status.WAITING_OWNER, lang)
    if product is None:
        if not asks:
            _enqueue_turn(s, sess, 1)
        idea_review.start(s, business.id)
    else:
        _enqueue_report(s, business.id)
    activity.record(s, verb="kickoff_started", entity="business", entity_id=business.id, project_id=None,
                    business_id=business.id,
                    summary=msg("kickoff.activity.started", lang, id=business.id, name=business.name[:120]))
    return view(s, sess.id, lang=lang)


def _enqueue_report(s: Session, business_id: int) -> None:
    """The CPO's reading of the product. One job per reading: the first keeps the business's key, a reading the owner
    asks for again gets a key of its own."""
    base = f"product_report:{business_id}"
    earlier = s.scalar(select(func.count()).select_from(Job).where(
        or_(Job.idempotency_key == base, Job.idempotency_key.like(f"{base}:%")))) or 0
    queue.enqueue(s, "product_report", {"business_id": business_id},
                  idempotency_key=base if not earlier else f"{base}:{earlier}", max_attempts=2)


def read_again(s: Session, sid: int, *, lang: Lang) -> dict:
    """The owner's "read the code again", after a reading that failed: the reading starts over with a new job. Only a
    failed reading can start again, so a second click, which finds it reading, enqueues nothing."""
    sess = _session(s, sid, lang)
    business = s.get(Business, sess.business_id, with_for_update=True)
    product = existing_product.existing(business)
    if product is None or product.report_state != "failed":
        raise ControlError(msg("product.read_again.not_failed", lang))
    business.product = product.model_copy(update={"report_failed": False}).model_dump(mode="json")
    _enqueue_report(s, business.id)
    activity.record(s, verb="product_read_again", entity="business", entity_id=business.id, project_id=None,
                    business_id=business.id,
                    summary=msg("product.activity.read_again", lang, repo=product.repo))
    return view(s, sid, lang=lang)


def report_ready(s: Session, business_id: int, *, state_line: str | None) -> None:
    """The CPO's report on the product the business brought is written, or could not be: note where the product
    stands in the plan as the CPO's guess, then start the chat. A report that lands after the chat started (the owner
    had the code read again) only updates the plan: the chat goes on as it is."""
    sess = s.scalar(select(KickoffSession).where(KickoffSession.business_id == business_id,
                                                 KickoffSession.status.in_([x.value for x in LIVE]))
                    .order_by(KickoffSession.id.desc()).limit(1).with_for_update())
    if sess is None:
        return
    if state_line and (brief := roadmap.draft_brief(s, business_id, lock=True)) is not None:
        sections = copy.deepcopy(roadmap.normalize_sections(brief.sections))
        _section(sections, "background")["lines"].append(_line(state_line, "guess", source(sess.id, 0)))
        brief.sections = sections
    started = s.scalar(select(Job.id).where(Job.kind == "kickoff_turn",
                                            Job.payload["session_id"].as_integer() == sess.id).limit(1)) is not None
    if sess.status == Status.OPEN.value and sess.turns_used == 0 and not started:
        _enqueue_turn(s, sess, 1)


def answer(s: Session, sid: int, *, action: str, text: str | None = None, choice: str | None = None,
           strengths: list[Strength] | None = None, lang: Lang) -> dict:
    sess = _session(s, sid, lang)
    status = Status(sess.status)
    if status != Status.WAITING_OWNER:
        raise ControlError(msg(_WHY_NOT[status], lang))
    if action == "later":
        _move(sess, Status.PAUSED, lang)
        return view(s, sid, lang=lang)
    if (opening := _turns(s, sid)[-1]).n == STRENGTHS_TURN:
        return _answer_strengths(s, sess, opening, action, strengths, lang)
    text, choice = (text or "").strip(), (choice or "").strip()
    if action == "answer" and not (text or choice or documents.attached_during(s, sid, sess.turns_used)):
        raise ControlError(msg("kickoff.answer.empty", lang))
    if action not in ("answer", "unknown"):
        raise ControlError(msg("kickoff.answer.bad_action", lang, action=action))
    turn = _turns(s, sid)[-1]
    if action == "unknown":
        turn.owner_text, turn.owner_choice = None, "unknown"
    else:
        turn.owner_text, turn.owner_choice = text or None, choice[:200] or None
    turn.answered_at = _now()
    brief = _draft(s, sess, lang)
    sections, assumptions = copy.deepcopy(roadmap.normalize_sections(brief.sections)), copy.deepcopy(brief.assumptions)
    src = source(sid, turn.n)
    if turn.owner_text:
        said = msg("kickoff.closing.answer", lang, text=turn.owner_text) if turn.closing else turn.owner_text
        _section(sections, "owner_words")["lines"].append(_line(said, "said", src))
    if action == "unknown":
        _record_unknown(sections, assumptions, turn, _turns(s, sid), lang)
    elif turn.owner_choice == AGREE and turn.kind in ("playback", "proposal"):
        _promote(sections, {x["id"] for x in turn.promote})
    brief.sections, brief.assumptions = sections, assumptions
    close_again = False
    if turn.closing:
        unsure, agreed = turn.owner_choice == "unknown", turn.owner_choice == AGREE
        retried = any(t.closing and t.owner_choice == "unknown" for t in _turns(s, sid) if t.n < turn.n)
        if agreed and (not turn.owner_text or sess.turns_used >= sess.max_turns):
            return _ended(s, sess, "owner_confirmed", lang)
        if unsure and (retried or sess.turns_used >= sess.max_turns):
            return _ended(s, sess, "owner_unsure", lang)
        if sess.turns_used >= sess.max_turns:
            return _ended(s, sess, "max_turns", lang)
        close_again = unsure or agreed
    _move(sess, Status.OPEN, lang)
    _enqueue_turn(s, sess, sess.turns_used + 1, close=close_again)
    return view(s, sid, lang=lang)


def _answer_strengths(s: Session, sess: KickoffSession, turn: KickoffTurn, action: str,
                      picked: list[Strength] | None, lang: Lang) -> dict:
    """The owner's answer to the opening question. Picked areas, or none of them yet, are saved as Settings > About
    you saves them; "I don't know" skips the question and leaves them unanswered. Either way the CEO's first turn
    starts, unless the product the business brought is still being read: that report starts it."""
    if action not in ("answer", "unknown"):
        raise ControlError(msg("kickoff.answer.bad_action", lang, action=action))
    if action == "answer":
        if picked is None:
            raise ControlError(msg("kickoff.strengths.empty", lang))
        if changed := app_settings.save(s, "owner", {"strengths": picked}, actor="owner", lang=lang):
            activity.record(s, verb="settings", entity="settings", entity_id=None, project_id=None,
                            summary=msg("console.event.settings", lang, section="owner",
                                        fields=", ".join(c["key"] for c in changed)),
                            data={"section": "owner", "changes": changed})
        turn.owner_choice = ",".join(known(picked)) or NONE_YET
    else:
        turn.owner_choice = "unknown"
    turn.answered_at = _now()
    _move(sess, Status.OPEN, lang)
    product = existing_product.existing(s.get(Business, sess.business_id))
    if product is None or product.report_state != "reading":
        _enqueue_turn(s, sess, 1)
    return view(s, sess.id, lang=lang)


def _ended(s: Session, sess: KickoffSession, reason: str, lang: Lang) -> dict:
    _end(s, sess, reason, lang)
    return view(s, sess.id, lang=lang)


def _record_unknown(sections: list[dict], assumptions: list[dict], turn: KickoffTurn, turns: list[KickoffTurn],
                    lang: Lang) -> None:
    """모르겠어요 on a required section is asked once more as "짐작이라도"; the second one is recorded."""
    key = turn.section
    if key not in BRIEF_BY_KEY or key == "owner_words" or turn.closing:
        return
    asked_before = any(t.n < turn.n and t.section == key and t.owner_choice == "unknown" for t in turns)
    if BRIEF_BY_KEY[key].required and not asked_before:
        return
    _set_unknown(sections, assumptions, key, source(turn.session_id, turn.n), lang)


def _end(s: Session, sess: KickoffSession, reason: str, lang: Lang) -> None:
    _move(sess, Status.DONE, lang, end_reason=reason)
    activity.record(s, verb="kickoff_done", entity="business", entity_id=sess.business_id, project_id=None,
                    business_id=sess.business_id,
                    summary=msg("kickoff.activity.done", lang, id=sess.business_id, n=sess.turns_used))


def resume(s: Session, sid: int, *, lang: Lang) -> dict:
    sess = _session(s, sid, lang)
    if Status(sess.status) != Status.PAUSED:
        raise ControlError(msg("kickoff.resume.not_paused", lang))
    turns = _turns(s, sid)
    sess.note = None
    if turns and turns[-1].answered_at is None:
        _move(sess, Status.WAITING_OWNER, lang)
    else:
        _move(sess, Status.OPEN, lang)
        _enqueue_turn(s, sess, sess.turns_used + 1)
    return view(s, sid, lang=lang)


def finish(s: Session, sid: int, *, lang: Lang) -> dict:
    """End early on the owner's word. The CEO still closes with a playback of the whole brief, unless the
    conversation already reached its closing turn."""
    sess = _session(s, sid, lang)
    status = Status(sess.status)
    if status not in (Status.WAITING_OWNER, Status.PAUSED):
        raise ControlError(msg(_WHY_NOT[status], lang))
    turns = _turns(s, sid)
    if (turns and turns[-1].closing) or sess.turns_used >= sess.max_turns:
        _end(s, sess, "owner_finished", lang)
    else:
        _move(sess, Status.OPEN, lang)
        sess.note = None
        _enqueue_turn(s, sess, sess.turns_used + 1, close=True)
    return view(s, sid, lang=lang)


def _customer_question(s: Session, sess: KickoffSession, lang: Lang) -> dict | None:
    """The CEO's last question, once the conversation is done and the owner has not yet answered or skipped it. It is
    server text, not a turn: it spends no CEO turn and changes no turn count."""
    if sess.status != Status.DONE.value or profile_store.has_answer(s, sess.business_id):
        return None
    return {"text": msg("kickoff.profile.question", lang)}


def answer_customer(s: Session, sid: int, *, text: str | None, lang: Lang) -> dict:
    """The owner's answer to the customer question, or its skip (nothing written). The words go to the customer
    description's inference; a skip leaves it to the plan."""
    sess = _session(s, sid, lang)
    if Status(sess.status) != Status.DONE:
        raise ControlError(msg("kickoff.customer.not_asked", lang))
    if profile_store.has_answer(s, sess.business_id):
        raise ControlError(msg("kickoff.customer.answered", lang))
    try:
        row = profile_store.add_answer(s, sess.business_id, text, asked_in="kickoff", kickoff_session_id=sid)
    except profile_store.ProfileError as exc:
        raise ControlError(exc.words(lang)) from exc
    if row.text is not None:
        profile_infer.enqueue_for_answer(s, sess.business_id, row.id)
    return view(s, sid, lang=lang)


def abandon(s: Session, sid: int, *, lang: Lang) -> dict:
    sess = _session(s, sid, lang)
    _move(sess, Status.ABANDONED, lang, end_reason="owner_abandoned")
    return view(s, sid, lang=lang)


def _refuse_if_live(s: Session, sess: KickoffSession, lang: Lang) -> None:
    """A business talks in one conversation at a time."""
    if live := s.scalar(select(KickoffSession.id).where(KickoffSession.business_id == sess.business_id,
                                                       KickoffSession.status.in_(KICKOFF_LIVE))):
        raise ControlError(msg("kickoff.already_live", lang, live=live))


def reopen(s: Session, sid: int, *, lang: Lang) -> dict:
    """Take back an abandoned conversation. It comes back paused, and 이어하기 continues it. Its half-filled draft
    can be approved only after it ends again."""
    sess = _session(s, sid, lang)
    if Status(sess.status) != Status.ABANDONED:
        raise ControlError(msg("kickoff.reopen.not_abandoned", lang))
    _refuse_if_live(s, sess, lang)
    _move(sess, Status.PAUSED, lang)
    sess.ended_at = sess.end_reason = None
    sess.note = msg("kickoff.note.reopened", lang)
    return view(s, sid, lang=lang)


def _why_not_continue(s: Session, sess: KickoffSession, lang: Lang) -> str | None:
    """Why a conversation cannot be continued, or None. Only a finished one whose draft is still open for edits and
    has a gate section nobody settled, and that still has a turn left under the cap."""
    status = Status(sess.status)
    if status != Status.DONE:
        return msg("kickoff.continue.not_done", lang, why=msg(_WHY_NOT[status], lang))
    if sess.id != s.scalar(select(func.max(KickoffSession.id)).where(KickoffSession.business_id == sess.business_id)):
        return msg("kickoff.continue.not_latest", lang)
    brief = roadmap.draft_brief(s, sess.business_id)
    if brief is None:
        return msg("kickoff.continue.approved", lang)
    if not roadmap.unsettled_gates(brief.sections):
        return msg("kickoff.continue.settled", lang)
    if sess.turns_used >= TURN_CAP:
        return msg("kickoff.continue.cap", lang, cap=TURN_CAP)
    return None


def continue_on(s: Session, sid: int, *, lang: Lang) -> dict:
    """이어서 정하기: reopen a finished conversation whose gate sections (대상 고객과 문제, 목표와 기한) are still not
    the owner's own, with up to `CONTINUE_EXTRA` more turns and never more than `TURN_CAP` in all. The CEO's next turn
    asks for those sections."""
    sess = _session(s, sid, lang)
    if reason := _why_not_continue(s, sess, lang):
        raise ControlError(reason)
    _refuse_if_live(s, sess, lang)
    sess.max_turns = min(TURN_CAP, sess.max_turns + CONTINUE_EXTRA)
    sess.ended_at = sess.end_reason = sess.note = None
    turns = _turns(s, sid)
    if turns and turns[-1].answered_at is None:
        _move(sess, Status.WAITING_OWNER, lang)
    else:
        _move(sess, Status.OPEN, lang)
        _enqueue_turn(s, sess, sess.turns_used + 1)
    activity.record(s, verb="kickoff_continued", entity="business", entity_id=sess.business_id, project_id=None,
                    business_id=sess.business_id,
                    summary=msg("kickoff.activity.continued", lang, id=sess.business_id, n=sess.turns_used))
    return view(s, sid, lang=lang)


# ------------------------------------------------------------------ one CEO turn
# The user message below is the CEO's input, not owner text, so it is English whatever the owner reads.

@lru_cache
def _system(lang: Lang) -> str:
    """The CEO's kickoff prompt, naming the language the turn is written in and the question that closes it."""
    text = (settings().prompts_dir / "kickoff.md").read_text(encoding="utf-8")
    return text.replace("{output_language}", NAMES[lang]).replace("{closing_question}", closing_question(lang))


_STATE = {"settled": "settled", "guess": "only guesses", "unknown": "unknown", "empty": "empty"}


def _render_brief(sections: list[dict]) -> str:
    out = []
    for sec in roadmap.normalize_sections(sections):
        lines = sec["lines"]
        spec = BRIEF_BY_KEY.get(sec["key"])
        if spec is None:
            out.append(f"### {roadmap.LEGACY_TITLE}: {sec['title']} (for reference only; new plans have no such section)")
        else:
            kind = "required" if spec.required else "optional, may stay empty"
            out.append(f"### {sec['title']} [{spec.key}] ({kind}, {_STATE[section_state(lines)]}) Holds: {spec.holds}")
        out += roadmap.prompt_lines(lines)
    return "\n".join(out)


def _owner_said(t: KickoffTurn) -> str:
    if t.owner_choice == "unknown":
        return "(pressed \"I don't know\")"
    if t.owner_choice == AGREE:
        return f"(pressed \"That's right\") {t.owner_text or ''}".strip()
    said = " ".join(x for x in (t.owner_text, f"(picked: {t.owner_choice})" if t.owner_choice else None) if x)
    return said or ("(sent only the files attached during this turn)" if t.answered_at else "")


def _render_talk(turns: list[KickoffTurn]) -> str:
    out = []
    for t in turns:
        title = BRIEF_BY_KEY[t.section].title if t.section in BRIEF_BY_KEY else "the whole plan"
        out.append(f"[{t.n}] Project Manager ({t.kind}, section: {title}{', closing' if t.closing else ''}): {t.ceo_text}")
        out.append(f"    Owner: {_owner_said(t) or '(not answered yet)'}")
    return "\n".join(out) or "(no conversation yet)"


def _titles(keys: list[str]) -> str:
    return ", ".join(f"'{BRIEF_BY_KEY[k].title}'" for k in keys)


def _prompt(sess: KickoffSession, business: Business, sections: list[dict], turns: list[KickoffTurn], n: int,
            closing: bool, lang: Lang, panel: str | None = None, docs: list[Document] = (),
            product: ExistingProduct | None = None) -> str:
    last_check = max((t.n for t in turns if t.kind in ("playback", "proposal")), default=0)
    since = (n - 1) - last_check
    progress = [f"This is turn {n} of at most {sess.max_turns}. Turns left, this one included: {sess.max_turns - n + 1}.",
                f"Turns since the last playback or proposal: {since}." if last_check else
                f"There has been no playback or proposal yet. Turns so far: {n - 1}."]
    if closing:
        progress.append("**This is the closing turn.** Make it a playback of the whole plan and set done to true. End "
                        f"the text with this exact question: \"{closing_question(lang)}\". Say plainly which sections "
                        "are not settled.")
    elif since >= PLAYBACK_DUE_AFTER:
        progress.append("Make this turn a playback or a proposal.")
    if (ask := _guess_ask_due(turns)) is not None:
        progress.append(f"The owner said \"I don't know\" for the first time on the required section "
                        f"{_titles([ask])}. Make this turn a question on that section that asks once for a guess: "
                        "\"Even a guess is fine. If you had to pick?\"")
    held = next((t for t in turns if any(x.get("what") == "holds_line" for x in t.patch_notes or [])), None)
    if held is not None and held.n == n - 1 and held.owner_text:
        progress.append(f"The owner's answer to turn [{held.n}] answers your proposal that held the line (rule 16). "
                        "Any part of it that still turns the step or the narrower who-it's-for down, even beside a "
                        "yes (\"fine, this week, but in the end make it for everyone\"), is a no: write that part on "
                        "this turn as one `top_risks` `guess` line with its first sign and the response. Only a yes "
                        "with no such part needs none.")
    if n > TARGET_TURNS and not closing:
        progress.append(f"The conversation is past its target of {TARGET_TURNS} turns. Ask straight away for the "
                        "required sections still open, one per turn, and do not ask about optional sections.")
    if stuck := _stuck_gates(sections):
        progress.append(f"The owner said \"I don't know\" twice on {_titles(stuck)}. Approval needs the owner to state "
                        "or confirm these sections. Do not repeat the same question: ask about another section first, "
                        "or propose the most likely draft from the conversation so far and ask whether it is right "
                        "(the draft goes into brief_patch as guess).")
    if not closing and any(t.closing for t in turns) and (open_gates := roadmap.unsettled_gates(sections)):
        progress.append(f"The owner closed the conversation, but {_titles(open_gates)} are not settled yet, so the "
                        "plan cannot be approved and the conversation continues. From this turn on, ask for these "
                        "sections one per turn and ask about nothing else. If asking alone does not settle them, "
                        "propose a draft from what the conversation showed and ask whether it is right.")
    if closing:
        if gates := [k for k in required_remaining(sections) if BRIEF_BY_KEY[k].gate]:
            progress.append(f"In the closing text, propose the most likely draft for {_titles(gates)} and put it into "
                            "brief_patch as guess. Approval needs the owner to confirm it.")
        if empties := empty_optional(sections, lang):
            progress.append("The server ends the text with a sentence naming the empty optional sections. Do not "
                            "push the owner to fill them: " + ", ".join(empties))
    remaining = required_remaining(sections)
    first_words = f"Business name: {business.name}\nOne line: {business.one_liner}\n" + (
        f"Pasted notes:\n{sess.material}" if sess.material else "Pasted notes: none")
    parts = [
        "## The owner's first words", fenced("The owner's first words", first_words),
    ]
    if docs:
        parts += ["## The owner's files (the owner's own words, like the pasted notes)",
                  fenced("The owner's files", documents.prompt_text(list(docs), chars=KICKOFF_FILE_CHARS))]
    parts += [
        "## Progress", "\n".join(f"- {x}" for x in progress),
        "## Plan draft", fenced("Plan draft", _render_brief(sections)),
        "## Required sections not settled yet",
        "\n".join(f"- {BRIEF_BY_KEY[k].title} [{k}]" for k in remaining) or "None. You can close.",
        "## The conversation so far", fenced("The conversation so far", _render_talk(turns)),
    ]
    if panel:
        parts += ["## What the idea panel found (reference, not a blocker)", fenced("Idea panel", panel)]
    if product is not None:
        # The CPO's reading of the code, not the owner's words: it is outside owner_words, so it never backs a said line.
        parts += ["## The product as it is today (the CPO's report from reading the code)",
                  fenced("Current-state report", existing_product.render(product))]
    parts.append("Write this turn from the above.")
    return "\n\n".join(parts)


def _stuck_gates(sections: list[dict]) -> list[str]:
    """Gate sections the owner answered 모르겠어요 twice: recorded unknown, yet approval still needs a line of theirs."""
    by_key = roadmap.lines_by_key(sections)
    return [spec.key for spec in BRIEF_SECTIONS if spec.gate and section_state(by_key.get(spec.key, [])) == "unknown"]


def empty_optional(sections: list[dict], lang: Lang) -> list[str]:
    """Titles of the optional sections nobody has touched, in `lang`. The closing turn names them and asks for
    nothing."""
    by_key = roadmap.lines_by_key(sections)
    goal = roadmap.goal_count(by_key.get("goal_deadline", []), lang)
    return [spec.text("title", lang, goal) for spec in BRIEF_SECTIONS
            if not spec.required and not spec.quiet and section_state(by_key.get(spec.key, [])) == "empty"]


def run_turn(session_id: int, n: int, close: bool = False, *, attempt: int = 1, max_attempts: int = 1) -> None:
    """Post turn `n`. A rerun that finds the turn posted, or the session paused or ended, does nothing. The model
    call holds no transaction, and a `MustWait` out of it leaves the session as it was, so a wait burns no turn."""
    lang = app_settings.language()
    with session_scope() as s:
        sess = _session(s, session_id, lang, lock=False)
        if sess.status != Status.OPEN.value or sess.turns_used != n - 1:
            return
        business = s.get(Business, sess.business_id)
        brief = roadmap.draft_brief(s, sess.business_id)
        if brief is None:
            raise ControlError(msg("kickoff.no_draft", lang))
        closing = close or n >= sess.max_turns
        prompt = _prompt(sess, business, brief.sections, _project_manager_turns(_turns(s, session_id)), n, closing, lang,
                         panel=idea_review.project_manager_summary(s, sess.business_id), docs=documents.active(s, sess.business_id),
                         product=existing_product.existing(business))
    try:
        out = run_agent("project_manager", "kickoff", prompt, KickoffTurnOut, project_id=None, business_id=business.id,
                        system_override=_system(lang))
        if not out.text.strip():
            raise AgentFailed("kickoff: the Project Manager returned an empty turn")
    except MustWait as wait:
        _note_wait(session_id, n, wait, lang)
        raise
    except Exception as exc:
        if attempt >= max_attempts:
            _stall(session_id, n, exc, lang)
        raise
    with session_scope() as s:
        sess = _session(s, session_id, lang)
        if sess.status != Status.OPEN.value or sess.turns_used != n - 1:
            return
        _post(s, sess, n, out, closing, lang)


def _stall(session_id: int, n: int, exc: BaseException, lang: Lang) -> None:
    with session_scope() as s:
        sess = _session(s, session_id, lang)
        if sess.status == Status.OPEN.value and sess.turns_used == n - 1:
            _move(sess, Status.PAUSED, lang)
            sess.note = msg("kickoff.note.stalled", lang)
            log.error("kickoff %s turn %s stalled: %s", session_id, n, exc)


def _note_wait(session_id: int, n: int, wait: MustWait, lang: Lang) -> None:
    """The turn waits for a usage window: say why and until when, in the owner's timezone."""
    with session_scope() as s:
        sess = _session(s, session_id, lang)
        if sess.status == Status.OPEN.value and sess.turns_used == n - 1:
            when = usage.local_time(wait.until, app_settings.effective().usage.timezone, lang=lang)
            sess.note = msg("kickoff.note.usage_wait", lang, reason=msg(f"usage.reason.{wait.reason}", lang), when=when)


def _optional_note(sections: list[dict], lang: Lang) -> str:
    empties = empty_optional(sections, lang)
    return msg("kickoff.closing.empty_optional", lang, sections=", ".join(empties)) if empties else ""


def _post(s: Session, sess: KickoffSession, n: int, out: KickoffTurnOut, closing: bool, lang: Lang) -> None:
    brief = _draft(s, sess, lang)
    sections, assumptions = copy.deepcopy(roadmap.normalize_sections(brief.sections)), copy.deepcopy(brief.assumptions)
    turns = _project_manager_turns(_turns(s, sess.id))
    src = source(sess.id, n)
    words = owner_words(sess, s.get(Business, sess.business_id), turns, documents.active(s, sess.business_id))
    set_aside, refusal = _held_refusals(sess, turns, n, out, closing)
    notes = _apply_patch(sections, assumptions, out.brief_patch, src, words=words,
                         guess_sources=_guess_reply_sources(turns), set_aside=set_aside)
    if refusal:
        notes.insert(0, {"what": "holds_line", "turn": src, "refusal": refusal,
                         "why": "the Project Manager holds the line once: the owner's words before this proposal wait for the "
                                "answer to it"})
    remaining = required_remaining(sections)
    if out.done and remaining and not closing:
        log.info("kickoff %s turn %s: done refused, %s still open", sess.id, n, remaining)
    closing = closing or (out.done and not remaining)
    text = out.text.strip()
    if closing:
        for key in remaining:
            if section_state(_section(sections, key)["lines"]) == "empty":
                _set_unknown(sections, assumptions, key, src, lang)
        body = text
        for each in LANGS:
            body = body.replace(closing_question(each), "")
        text = "\n\n".join(x for x in (body.strip(), _optional_note(sections, lang), closing_question(lang)) if x)
    chips = [] if closing else list(dict.fromkeys(
        c.strip() for c in out.chips if c.strip() and _chip_key(c) not in _SCREEN_CHIPS))[:5]
    brief.sections, brief.assumptions = sections, assumptions
    kind = "playback" if closing else out.kind
    promote = _promotable(sections, turns, n, closing=closing) if kind != "question" else []
    s.add(KickoffTurn(session_id=sess.id, n=n, kind=kind, ceo_text=text, chips=chips,
                      section=None if closing else out.section, closing=closing, promote=promote, patch_notes=notes))
    sess.turns_used = n
    sess.note = None
    _move(sess, Status.WAITING_OWNER, lang)


# ------------------------------------------------------------------ read model

def coverage(sections: list[dict], lang: Lang) -> dict[str, Any]:
    remaining = required_remaining(sections)
    total = sum(1 for spec in BRIEF_SECTIONS if spec.required)
    return {"required_total": total, "required_settled": total - len(remaining), "remaining": remaining,
            "percent": round((total - len(remaining)) / total * 100), "empty_optional": empty_optional(sections, lang)}


def approval(sections: list[dict], lang: Lang) -> dict[str, Any]:
    """What the owner sees before approving: the gate sections that block it, and each other unsettled required
    section with the default the company applies instead (비어 있는 칸과 적용될 기본값)."""
    blockers = roadmap.unsettled_gates(sections)
    return {"can_approve": not blockers, "blockers": blockers,
            "blocked_by": [BRIEF_BY_KEY[k].text("title", lang) for k in blockers],
            "empty_defaults": roadmap.empty_defaults(sections, lang=lang)}


def _turn_view(t: KickoffTurn, lang: Lang, goal: str | None) -> dict:
    """A turn as stored, with its confirm lines titled in `lang`. Each line holds the title it was stored with, in
    the language of that day."""
    row = {c.name: getattr(t, c.name) for c in t.__table__.columns if c.name != "session_id"}
    if t.n == STRENGTHS_TURN:
        row["asks"] = "strengths"
    row["promote"] = [{**x, "title": BRIEF_BY_KEY[x["section"]].text("title", lang, goal)}
                      if x.get("section") in BRIEF_BY_KEY else x for x in t.promote or []]
    return row


def view(s: Session, sid: int, *, lang: Lang) -> dict:
    sess = _session(s, sid, lang, lock=False)
    turns = _turns(s, sid)
    brief = roadmap.draft_brief(s, sess.business_id) or roadmap.current_brief(s, sess.business_id)
    normalized = roadmap.normalize_sections(brief.sections) if brief else []
    sections = documents.annotate(s, roadmap.section_views(brief.sections, lang=lang)) if brief else []
    open_turn = turns[-1] if turns and turns[-1].answered_at is None else None
    goal = roadmap.goal_count(roadmap.lines_by_key(normalized).get("goal_deadline", []), lang)
    return {
        "session": {"id": sess.id, "business_id": sess.business_id, "status": sess.status, "turns_used": sess.turns_used,
                    "max_turns": sess.max_turns, "turns_left": sess.max_turns - sess.turns_used, "note": sess.note,
                    "can_continue": _why_not_continue(s, sess, lang) is None, "material": sess.material,
                    "created_at": sess.created_at, "ended_at": sess.ended_at, "end_reason": sess.end_reason},
        "turns": [_turn_view(t, lang, goal) for t in turns],
        "documents": documents.in_session(s, sess.id),
        "open_turn": open_turn.n if open_turn and sess.status == Status.WAITING_OWNER.value else None,
        "brief": {"id": brief.id, "version": brief.version, "approved_at": brief.approved_at,
                  "sections": sections, "assumptions": brief.assumptions} if brief else None,
        "coverage": coverage(normalized, lang),
        "approval": approval(normalized, lang),
        "customer_question": _customer_question(s, sess, lang),
        "product": existing_product.view(existing_product.existing(s.get(Business, sess.business_id)), lang),
    }


def live_session_id(s: Session, business_id: int) -> int | None:
    """The business's live session, or else its latest."""
    live = s.scalar(select(KickoffSession.id).where(KickoffSession.business_id == business_id,
                                                    KickoffSession.status.in_([x.value for x in LIVE])))
    return live or s.scalar(select(KickoffSession.id).where(KickoffSession.business_id == business_id)
                            .order_by(KickoffSession.id.desc()).limit(1))
