"""The one model call that writes the customer description, and the three things that start it.

The CEO reads the owner's answers, the plan's lines and the description as it stands, and proposes values. The server
decides what they are: a value is `said` only when its quote is the owner's own words in an answer or a `said` plan line
and is about the value (the kickoff's rule, `owner_quote.problem`), and everything else is a `guess`. Nothing is ever
`confirmed` here, and a line that holds a said or confirmed value is never read or touched: `store` refuses it, and the
owner's edit wins a race through `if_live`. A line the material does not settle stays as it is, or becomes the unknown
marker when nobody ever wrote it, so "asked, and not known" differs from "never asked".

Triggers: an answer to the customer question (`enqueue_for_answer`), and a plan approval or `ship profile backfill`
(`enqueue_for_plan`). Their job keys make each of them a no-op the second time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.agents import executor
from justshipit.contracts.profile import ProfileOut, ProfileValueOut
from justshipit.db.models import Business, BusinessBrief, CustomerProfileAnswer, Job
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, Lang, msg
from justshipit.i18n.match.profile_quote import GENERIC, NEGATIONS
from justshipit.services import owner_quote, queue, roadmap
from justshipit.services import profile as profile_service
from justshipit.services.fence import fenced
from justshipit.services.profile import core, store, vocab
from justshipit.services.profile.core import Profile, Value
from justshipit.services.profile.vocab import Line
from justshipit.settings import settings

log = logging.getLogger(__name__)

KIND = "profile_infer"   # enqueue sites write the kind out, since tests/gate_sites.py reads it from the source
PURPOSE = "customer_profile"
Cause = Literal["kickoff_answer", "plan", "plan_changed", "backfill"]
_HELD = ("said", "confirmed")
_ACTIVE = ("queued", "gated", "running")


def _key(business_id: int, what: str, ident: int) -> str:
    return f"profile:{business_id}:{what}:{ident}"


def enqueue_for_answer(s: Session, business_id: int, answer_id: int) -> Job | None:
    payload = {"business_id": business_id, "trigger": "kickoff_answer", "answer_id": answer_id}
    return queue.enqueue(s, "profile_infer", payload,
                         idempotency_key=_key(business_id, "answer", answer_id), max_attempts=2)


def enqueue_for_plan(s: Session, brief: BusinessBrief, *, cause: Literal["plan", "plan_changed", "backfill"],
                     retry_of: int | None = None) -> Job | None:
    """`retry_of` names a reading of the same plan that failed or was closed, so the new one needs a key of its own."""
    payload = {"business_id": brief.business_id, "trigger": cause, "brief_id": brief.id}
    key = _key(brief.business_id, "plan", brief.id) + (f":retry:{retry_of}" if retry_of else "")
    return queue.enqueue(s, "profile_infer", payload, idempotency_key=key, max_attempts=2)


def backfill(s: Session, *, business_id: int | None = None, dry_run: bool = False) -> list[tuple[int, str]]:
    """Every business that is not archived, has an approved plan and has never had a description row, read once from its
    plan as guesses: (id, name) of each one queued, or only listed under `dry_run`. A business with any row, live or
    retired, is the owner's or already read, and one whose plan already has its job is already on its way, so both are
    left alone."""
    q = select(Business).where(Business.status != "ARCHIVED").order_by(Business.id)
    if business_id is not None:
        q = q.where(Business.id == business_id)
    found = []
    for business in s.scalars(q):
        brief = roadmap.approved_brief(s, business.id)
        if brief is None or store.has_rows(s, business.id):
            continue
        earlier = s.scalar(select(Job).where(Job.idempotency_key.startswith(_key(business.id, "plan", brief.id)))
                           .order_by(Job.id.desc()).limit(1))
        if earlier is not None and earlier.status != "failed":
            continue
        if not dry_run:
            enqueue_for_plan(s, brief, cause="backfill", retry_of=earlier.id if earlier else None)
        found.append((business.id, business.name))
    return found


def reading(s: Session, business_id: int) -> bool:
    """Whether a description job for the business is queued, waiting for a usage window, or running."""
    return s.scalar(select(Job.id).where(Job.kind == KIND, Job.business_id == business_id,
                                         Job.status.in_(_ACTIVE)).limit(1)) is not None


def _writable(prof: Profile) -> tuple[Line, ...]:
    """The lines the company may write: those with no said or confirmed value."""
    return tuple(line for line in vocab.LINES if not any(v.provenance in _HELD for v in prof.lines[line].values))


def on_approved(s: Session, brief: BusinessBrief) -> None:
    """A plan was approved. A business with no description gets one from it. One that has a description gets the guessed
    and unknown lines read again when the plan's watched lines are not those of any plan its rows were written against.
    Lines the owner said or confirmed only get the stale note (`profile.stale_lines`), so nothing runs when none is
    left to write."""
    business = s.get(Business, brief.business_id)
    if business is None or business.status == "ARCHIVED":
        return
    rows = store.live_rows(s, brief.business_id)
    if not rows:
        enqueue_for_plan(s, brief, cause="plan")
        return
    if not _writable(core.assemble(brief.business_id, rows)):
        return
    written_against = {v.brief_id for v in rows if v.brief_id is not None}
    now = profile_service.watched_lines(brief.sections)
    plans = {b.id: profile_service.watched_lines(b.sections)
             for b in s.scalars(select(BusinessBrief).where(BusinessBrief.id.in_(written_against)))}
    if all(plans.get(b) != now for b in written_against):
        enqueue_for_plan(s, brief, cause="plan_changed")


# ------------------------------------------------------------------ the job

@dataclass(frozen=True)
class _Material:
    name: str
    has_repo: bool
    brief_id: int | None
    plan: tuple[tuple[str, str, str], ...]          # (section, provenance, text)
    said_words: tuple[tuple[str, str], ...]         # (source, normalized text) of the owner's own words
    answers: tuple[tuple[int, str], ...]
    prof: Profile
    live_ids: dict[Line, frozenset[int]]
    rejected: tuple[Value, ...]
    writable: tuple[Line, ...]


def _material(s: Session, business_id: int, answer_id: int | None) -> _Material | None:
    business = s.get(Business, business_id)
    if business is None or business.status == "ARCHIVED":
        return None
    brief = roadmap.current_brief(s, business_id)
    by_key = roadmap.lines_by_key(brief.sections) if brief else {}
    plan = tuple((key, x["provenance"], x["text"]) for key in core.WATCHED_SECTIONS for x in by_key.get(key, [])
                 if x["provenance"] in ("said", "confirmed", "guess"))
    q = select(CustomerProfileAnswer).where(CustomerProfileAnswer.business_id == business_id,
                                            CustomerProfileAnswer.text.is_not(None))
    if answer_id is not None:
        q = q.where(CustomerProfileAnswer.id <= answer_id)
    answers = tuple((a.id, a.text) for a in s.scalars(q.order_by(CustomerProfileAnswer.id)))
    said = [(f"answer:{i}", owner_quote.norm(text)) for i, text in answers]
    if brief is not None:
        for _, provenance, text in plan:
            if provenance == "said":
                said.append((f"plan:{brief.id}", owner_quote.norm(text)))
        for key in core.WATCHED_SECTIONS:
            said += [(f"plan:{brief.id}", owner_quote.norm(x["quote"])) for x in by_key.get(key, [])
                     if x["provenance"] == "said" and x.get("quote")]
    prof = profile_service.of(s, business_id)
    # Rows name only an approved plan. A draft keeps its id while the kickoff rewrites it, so rows stamped with it would
    # never look stale; unstamped, the first approval reads the guessed and unknown lines again.
    stamp = brief.id if brief is not None and brief.approved_at is not None else None
    return _Material(business.name, bool(business.github_repo), stamp, plan,
                     tuple((src, text) for src, text in said if text), answers, prof,
                     {line: frozenset(v.row_id for v in prof.lines[line].values) for line in vocab.LINES},
                     tuple(store.rejected(s, business_id)), _writable(prof))


@lru_cache
def _system() -> str:
    return (settings().prompts_dir / "customer_profile.md").read_text(encoding="utf-8")


def _vocabulary() -> str:
    return "\n".join(f"- {line}: " + "; ".join(f"{code} ({msg(f'profile.code.{line}.{code}', 'en')})"
                                              for code in vocab.live_codes(line)) for line in vocab.LINES)


def _what(v: Value) -> str:
    return v.code or v.sentence or ""


def _prompt(m: _Material, cause: Cause) -> str:
    parts = ["## The business", fenced("Business name", m.name)]
    if m.has_repo:
        parts.append("A code repository is linked to this business. That alone is not proof that the buyers are "
                     "developers. If nothing else says so, mark such a value as a guess and say nothing more.")
    parts += ["## The vocabulary (the codes a value may use, per line)", _vocabulary(),
              "## Lines you may write", ", ".join(m.writable)]
    if m.answers:
        parts += ["## The owner's answers about who the first customer is and how they will be met (oldest first)",
                  fenced("The owner's answers", "\n\n".join(f"[answer {i}] {text}" for i, text in m.answers))]
    if m.plan:
        parts += ["## Lines of the plan (said: the owner's words. confirmed: the owner agreed. guess: the company's)",
                  fenced("Plan lines", "\n".join(f"[{key}] ({provenance}) {text}" for key, provenance, text in m.plan))]
    parts += ["## The description now", profile_service.prompt_block(m.prof, "en")]
    if m.rejected:
        parts += ["## Values the owner turned down (do not offer them again)",
                  fenced("Turned down", "\n".join(f"{v.line}: {_what(v)}" for v in m.rejected))]
    parts += [f"## Why you are asked\n{_WHY[cause]}", "Write the values from the above."]
    return "\n\n".join(parts)


_WHY: dict[str, str] = {
    "kickoff_answer": "The owner just answered. Read the answer first, and refine what the description says now.",
    "plan": "The owner approved a plan and the description has no values yet. Every value you write from the plan "
            "alone is a guess.",
    "plan_changed": "The owner approved a changed plan. Refresh the lines you may write from it.",
    "backfill": "The business already has an approved plan and no description. Every value you write from the plan "
                "alone is a guess.",
}
_SPACES = re.compile(r"\s+")
_HANGUL = re.compile(r"[\uac00-\ud7a3]")
_NEGATED = re.compile("|".join([*(rf"(?<![a-z']){re.escape(w)}(?![a-z'])" for w in NEGATIONS["en"]),
                                *map(re.escape, NEGATIONS["ko"])]), re.IGNORECASE)


def same_words(a: str, b: str) -> bool:
    """Two sentences that differ only in spacing and case."""
    return _SPACES.sub(" ", a).strip().casefold() == _SPACES.sub(" ", b).strip().casefold()


def _label(line: Line, code: str) -> str:
    """What a code says in every language, so a quote in either one can share a word with it."""
    return " / ".join(msg(f"profile.code.{line}.{code}", lang) for lang in LANGS)


def _same_word(q: str, w: str) -> bool:
    """One word, or the same Korean word with a particle or ending of at most two characters after it. A word inside
    another is not the word: "buys" in Korean sits inside "the company" plus a particle, and means nothing there."""
    if q == w:
        return True
    short, long_ = sorted((q, w), key=len)
    return (bool(_HANGUL.search(short)) and len(short) >= 2 and long_.startswith(short)
            and len(long_) - len(short) <= 2)


def _names(quote: str, label: str) -> bool:
    """Whether the quote names the value in so many words, and does not deny it."""
    if _NEGATED.search(quote):
        return False
    words = {w for w in owner_quote.tokens(owner_quote.norm(label)) if w not in GENERIC}
    return any(_same_word(q, w) for q in owner_quote.tokens(quote) for w in words)


def _source(cause: Cause, m: _Material, answer_id: int | None) -> str:
    if cause == "kickoff_answer" and answer_id is not None:
        return f"answer:{answer_id}"
    return f"{'backfill' if cause == 'backfill' else 'plan'}:{m.brief_id}"


def _backing(v: ProfileValueOut, m: _Material) -> tuple[str, str] | None:
    """(source, normalized quote) when the quote is the owner's own words and is about the value, else None."""
    quote = owner_quote.norm(v.owner_quote or "")
    if not quote:
        return None
    hit = next(((src, text) for src, text in reversed(m.said_words) if quote in text), None)
    if hit is None:
        return None
    text = _label(v.line, v.code) if v.code else v.sentence or ""
    if owner_quote.problem(quote, text, gate=False, said=hit[1]) or not _names(quote, text):
        return None
    return hit[0], quote


def _accepted(items: list[ProfileValueOut], line: Line, m: _Material, cause: Cause, answer_id: int | None
              ) -> list[store.NewValue]:
    turned_down = [v for v in m.rejected if v.line == line]
    out: list[store.NewValue] = []
    codes: set[str] = set()
    sentenced = False
    for v in items:
        if v.code is not None:
            if v.code not in vocab.live_codes(line) or v.code in codes or any(r.code == v.code for r in turned_down):
                continue
            codes.add(v.code)
        elif v.sentence is None or not v.sentence.strip() or sentenced \
                or any(r.sentence and same_words(r.sentence, v.sentence) for r in turned_down):
            continue
        else:
            sentenced = True
        said = None if cause == "backfill" else _backing(v, m)
        out.append(store.NewValue(v.code, None if v.code else v.sentence.strip(), "said" if said else "guess",
                                  said[0] if said else _source(cause, m, answer_id), said[1] if said else None))
    return out


def run(business_id: int, *, trigger: Cause, answer_id: int | None = None, brief_id: int | None = None,
        lang: Lang) -> None:
    """Read the material, make the one call, and write each line the company may write. The call holds no transaction.
    The newest plan is read whichever plan the job was enqueued for, so a plan approved meanwhile is not missed."""
    with session_scope() as s:
        m = _material(s, business_id, answer_id)
        if m is None or not m.writable:
            log.info("profile B-%s: nothing the company may write (job for plan %s)", business_id, brief_id)
            return
        prompt = _prompt(m, trigger) if m.answers or m.plan else None
    # With nothing to read the call is skipped, and each line nobody wrote becomes the unknown marker.
    out = (executor.run_agent("project_manager", PURPOSE, prompt, ProfileOut, project_id=None, business_id=business_id,
                              system_override=_system()) if prompt else ProfileOut(values=[]))
    call_id = executor.last_call_id() if prompt else None
    with session_scope() as s:
        for line in m.writable:
            new = _accepted([v for v in out.values if v.line == line], line, m, trigger, answer_id)
            if not new and m.live_ids[line]:
                continue   # the material says nothing new about a line that already says something
            try:
                store.set_line(s, business_id, line, new, cause=trigger, written_by="project_manager", brief_id=m.brief_id,
                               if_live=m.live_ids[line], call_id=call_id, lang=lang)
            except store.ProfileError as exc:   # the owner edited the line while the model read: theirs wins
                log.info("profile B-%s %s skipped: %s", business_id, line, exc)
