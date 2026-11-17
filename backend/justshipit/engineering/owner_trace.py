"""What the owner said or approved, traced through a spec. 원두재고's '주문했어요' was in DEC-3, which the owner
approved, yet the spec writer saw only DEC-3's first 400 characters and the feature vanished without a word. Every
source here reaches the spec writer whole, and the spec must say what it did with each one: built (which
requirement), dropped (why), or not the product's to do. A source the spec says nothing about is `missed`, and a
drop or a miss is on the spec's approval card."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts.handoff import OwnerTraceOut, SpecStatus, TraceDisposition
from justshipit.contracts.owner_text import one_line
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import AgentRequest, Decision, Project, Spec
from justshipit.i18n import LANGS, Lang, catalog, msg
from justshipit.i18n.match import verdicts as verdict_words
from justshipit.services import matters, owner_tasks, plan_screen, requests, roadmap
from justshipit.services.fence import fenced

log = logging.getLogger(__name__)

# The brief sections that say what the product is or must not be. The owner's time, the budget, the people list and
# the like shape the company's work, not the product.
PRODUCT_SECTIONS = ("one_liner", "customer_problem", "alternative_value", "path", "goal_deadline", "red_lines", "scope",
                    "owner_words")
_OWNER_SAID = ("said", "confirmed")  # a guess (짐작) or an unknown is not the owner's word


@dataclass(frozen=True)
class OwnerSource:
    ref: str    # the id the spec writer cites, the same in every language: l-b166de, D-1, REQ-40, DEC-3
    where: str  # what it is, for the spec writer
    text: str   # whole, never clipped
    origin: str = ""  # where it came from, in the owner's words, for the card ("Your plan")
    gist: str = ""    # what it says, for the card, when `text` carries the spec writer's notes
    label: str = ""   # the id as a card stored it before ids were the same in every language: 지시 #1, answer REQ-40
    binding: bool = True  # the owner's own words; a decision still waiting on him is the CEO's, which no rule may cite
    screen: str = ""    # a note on one planned screen of a spec card: that screen's id (the ref is REQ-41@report)
    drawn_at: str = ""  # the commit of the draft that note is about

    @property
    def shown(self) -> str:
        return self.label or self.ref

    @property
    def of(self) -> str:
        """Which wording of the source a trace row judged: a plan line an owner's answer amended keeps its id."""
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]


def owner_sources(s: Session, p: Project, lang: Lang) -> list[OwnerSource]:
    """Every source's id is the same in every language, since the spec writer cites it back (Alex's REQ-65 cited
    "REQ-53" for "answer REQ-53"); its origin is in the owner's language, since the spec card shows it."""
    out: list[OwnerSource] = []
    brief = roadmap.approved_brief(s, p.business_id) if p.business_id is not None else None
    for sec in roadmap.normalize_sections(brief.sections) if brief else []:
        if sec["key"] in PRODUCT_SECTIONS:
            out += [OwnerSource(x["id"], f"the plan · {sec['title']}", x["text"], msg("build.trace.from_plan", lang))
                    for x in sec["lines"] if x.get("provenance") in _OWNER_SAID and x.get("text", "").strip()]
    for q, d in plan_screen.directives(s, p):
        elsewhere = "" if q.id == p.id else f"(P-{q.id})"
        out.append(OwnerSource(f"D-{d['n']}{elsewhere}", "the owner's directive", d["text"],
                               msg("build.trace.from_directive", lang, n=d["n"]),
                               label=msg("build.trace.directive", lang, n=d["n"]) + elsewhere))
    said: dict[str, tuple[str, list[str]]] = {}
    for a in (p.context or {}).get("owner_answers", []):
        if a.get("by") == requests.EXPIRY:
            continue  # a default nobody answered binds the team, but the owner never said it
        answer = a.get("answer") if (a.get("answer") or "").strip().lower() not in _BARE_VERDICTS else None
        if words := " ".join(x for x in (answer, a.get("note")) if x).strip():
            said.setdefault(a["request"], (a["title"], [f"{a['title']} → {a['decision']}"]))[1].append(words)
    out += [OwnerSource(ref, "the owner's answer or note", "\n".join(lines), msg("build.trace.from_answer", lang),
                        f"{asked}: {' / '.join(lines[1:])}", label=msg("build.trace.answer", lang, ref=ref))
            for ref, (asked, lines) in said.items()]
    for a in (p.context or {}).get("owner_answers", []):
        out += [OwnerSource(f"{a['request']}@{n['screen']}", f"the owner's note on the planned screen '{n['name']}'",
                            n["note"], msg("build.trace.from_screen_note", lang, screen=n["name"]), n["note"],
                            screen=n["screen"], drawn_at=a.get("commit", "")) for n in a.get("screens", [])]
    out += [OwnerSource(owner_tasks.display_id(t.id), f"{_CHECK_WHERE[t.status]}: {t.title}",
                        owner_tasks.owner_words(t.result), msg(_CHECK_ORIGIN[t.status], lang))
            for t in owner_tasks.plan_results(s, p.id)]
    # A decision is the owner's only through its approval card: one the CEO took alone (no card) is not, and one the
    # owner turned down is not. The card's status is read, since a row decided before rows followed their card still
    # says PENDING_APPROVAL.
    for d, status in s.execute(select(Decision, AgentRequest.status)
                               .join(AgentRequest, AgentRequest.id == Decision.approval_request_id)
                               .where(Decision.project_id == p.id).order_by(Decision.id)):
        if where := _DECISION_STATE.get(status):
            out.append(OwnerSource(f"DEC-{d.id}", where, f"{d.title}\n{d.rationale}",
                                   msg("build.trace.from_decision", lang), binding=status != "PENDING"))
    return out


# The click itself says no ask: the verdicts' codes, the console's words for them, and every option the server puts on
# a card, in every language.
_SERVER_OPTIONS = ("build.option.", "work.option.", "operations.option.")
_BARE_VERDICTS = {"", "approve", "reject", "defer", *(w for lang in LANGS for w in verdict_words.BARE[lang]),
                  *(msg(k, lang).lower() for k in catalog() if k.startswith(_SERVER_OPTIONS) or k == "factory.activate.no"
                    for lang in LANGS)}
_CHECK_WHERE = {owner_tasks.DONE: "what the owner found by hand",
                owner_tasks.SKIPPED: "a check the owner skipped, and why"}
_CHECK_ORIGIN = {owner_tasks.DONE: "build.trace.from_check", owner_tasks.SKIPPED: "build.trace.from_skipped_check"}
_DECISION_STATE = {"APPROVED": "a decision the owner approved", "AUTO_APPROVED": "a decision the owner approved",
                   "PENDING": "a decision waiting for the owner's approval"}


_SCREEN_NOTES = ("An item that is the owner's note on a planned screen is included only by changing a screen: put "
                 "the keys of the screens you changed for it in its entry's screens. The server compares those "
                 "screens with the draft the note was written on, and a screen it finds unchanged does not count.\n")


def prompt_section(sources: list[OwnerSource]) -> str:
    if not sources:
        return ""
    body = "\n\n".join(f"### [{x.ref}] {x.where}\n{x.text}" for x in sources)
    return ("## What the owner decided (each in full; put every item in owner_trace)\n"
            "Below is what the owner wrote or approved. Give each item at least one owner_trace entry. When one item "
            "holds several asks (one decision, several features), give each ask its own entry. When the spec builds it, "
            "write included and its requirement ids; when this version leaves it out, dropped and a reason the owner "
            "reads; when it is not something the product does (a budget, a schedule, the owner's own work, a research "
            "method), not_product and a short reason. Never leave out a feature the owner decided without a reason. "
            "What you leave out, and what you say nothing about, shows on the spec's approval card as it is.\n"
            + (_SCREEN_NOTES if any(x.screen for x in sources) else "")
            + fenced("what the owner decided", body))


def settle(sources: list[OwnerSource], trace: list[OwnerTraceOut], requirement_ids: set[str], lang: Lang, *,
           changed: Mapping[str, Collection[str]] | None = None) -> list[dict]:
    """The spec's trace as the server reads it, in the sources' order. An entry for an id the list did not have is
    dropped; one that claims a requirement the spec lacks, or a drop without a reason, did not account for its
    source and stays on the card as missed, beside the source's other asks; a source with no entry at all is missed.
    An ask the spec never listed, from a source it listed another ask of, is beyond what the server can see: the
    CPO's acceptance reads owner-trace.md against the build.

    A note on a planned screen is included only by a screen that changed. `changed` gives, for each such source, the
    ids of the screens that differ from the draft the note was written on. The row keeps the screens the writer cited
    that are among them, and a claim with none left is missed."""
    from justshipit.engineering.wireflow import slug  # wireflow reads questions, which read this module
    changed = changed or {}
    known = {x.ref: x for x in sources}
    rows: list[dict] = []
    for t in trace:
        if t.source not in known:
            log.warning("spec owner_trace cites %r, none of the owner sources %s", t.source, sorted(known))
            continue
        reqs = [r for r in t.requirement_ids if r in requirement_ids]
        disposition, reason = t.disposition, t.reason.strip()
        x = known[t.source]
        on = [i for i in dict.fromkeys(slug(k) for k in t.screens) if i in changed.get(x.ref, ())] if x.screen else []
        if disposition == TraceDisposition.INCLUDED and x.screen:
            if not on:
                disposition, reason = TraceDisposition.MISSED, msg("build.trace.screen_unchanged", lang)
        elif disposition == TraceDisposition.INCLUDED and not reqs:
            disposition, reason = TraceDisposition.MISSED, msg("build.trace.claimed_missing", lang)
        elif disposition in (TraceDisposition.DROPPED, TraceDisposition.NOT_PRODUCT) and not reason:
            disposition, reason = TraceDisposition.MISSED, msg("build.trace.no_reason", lang)
        included = disposition == TraceDisposition.INCLUDED
        rows.append({"source": t.source, "from": x.origin, "of": x.of,
                     "says": one_line(t.says, 120) or one_line(x.gist or x.text, 120),
                     "disposition": disposition.value, "requirement_ids": reqs if included else [], "reason": reason,
                     **_on_screen(x, on if included else [])})
    rows += [_missed(x, msg("build.trace.unsaid", lang))
             for x in sources if not any(r["source"] == x.ref for r in rows)]
    order = {x.ref: i for i, x in enumerate(sources)}
    return sorted(rows, key=lambda r: order[r["source"]])


def written_in(sources: list[OwnerSource], refs: set[str]) -> list[dict]:
    """Rows for the owner's answers to the spec's own questions (engineering/questions.py), which the server wrote into
    the spec: included, under no requirement of their own."""
    return [{"source": x.ref, "from": x.origin, "of": x.of, "says": one_line(x.gist or x.text, 120),
             "disposition": TraceDisposition.INCLUDED.value, "requirement_ids": [], "reason": ""}
            for x in sources if x.ref in refs]


def _on_screen(x: OwnerSource, screens: list[str]) -> dict:
    """What a row about a note on a planned screen adds: the screen the note is on, and the screens that changed for
    it. The spec card reads where each note landed from these (wireflow.for_card)."""
    return {"screen": x.screen, "screens": screens} if x.screen else {}


def _missed(x: OwnerSource, reason: str) -> dict:
    return {"source": x.ref, "from": x.origin, "of": x.of, "says": one_line(x.gist or x.text, 120),
            "disposition": TraceDisposition.MISSED.value, "requirement_ids": [], "reason": reason, **_on_screen(x, [])}


def _live_card(s: Session, project_id: int) -> tuple[Spec | None, AgentRequest | None]:
    spec = matters.live_spec(s, project_id)
    return spec, s.get(AgentRequest, spec.approval_request_id) if spec and spec.approval_request_id else None


def restate(s: Session, project_id: int, lang: Lang) -> None:
    """The product's spec card, while it waits for the owner, gains what the owner decided after the spec was written,
    as missed, and its first line counts it. Nothing re-drafts a spec in review (an ask folded onto its card waits
    for the next version), so the owner reads at the top of the card what this version does not hold before
    approving it: Daniel's REQ-62 said it held everything while DEC-4's conditions, 31 seconds younger, were in no
    spec. Asking again adds nothing twice."""
    spec, card = _live_card(s, project_id)
    if (card is None or spec.status != SpecStatus.IN_REVIEW.value or card.status != RequestStatus.PENDING.value
            or "owner_trace" not in (card.action_params or {})):
        return
    rows = card.action_params["owner_trace"]

    def judged(r: dict, x: OwnerSource) -> bool:
        # A card written before the ids were the same in every language stored the label as the source, and one
        # written before rows kept the wording's digest has no `of`.
        return r["source"] in (x.ref, x.shown) and r.get("of", x.of) == x.of
    sources = owner_sources(s, s.get(Project, spec.project_id), lang)
    known = {x.ref: x for x in sources}
    rows = [r for r in (_withdrawn(r, known, lang) for r in rows) if r is not None]
    late = [x for x in sources if not any(judged(r, x) for r in rows)]
    stale = {x.ref for x in late} | {x.shown for x in late}
    rows = [*(r for r in rows if r["source"] not in stale), *(_missed(x, msg("build.trace.late", lang)) for x in late)]
    if rows == card.action_params["owner_trace"]:
        return
    card.action_params = {**card.action_params, "owner_trace": rows}
    card.summary = "\n".join([card_line(rows, lang), *card.summary.split("\n")[1:]])


_LATE = {msg("build.trace.late", lang) for lang in LANGS}
_TASK_REF = owner_tasks.display_id(0).removesuffix("0")


def _withdrawn(r: dict, known: dict[str, OwnerSource], lang: Lang) -> dict | None:
    """A row about a check result the owner cleared by reopening it. One the card added after the spec goes, since
    the spec never read it. One the spec was written from stays as missed, since the spec builds on what the owner
    took back, and keeps what the spec did, so the same result given again restores it."""
    if not r["source"].startswith(_TASK_REF):
        return r
    x = known.get(r["source"])
    if x is None:
        if "was" in r:
            return r
        return None if r["reason"] in _LATE else {
            **r, "disposition": TraceDisposition.MISSED.value, "requirement_ids": [],
            "reason": msg("build.trace.withdrawn", lang),
            "was": {k: r[k] for k in ("disposition", "requirement_ids", "reason")}}
    if "was" in r:
        return {**{k: v for k, v in r.items() if k != "was"}, **r["was"]} if r.get("of") == x.of else None
    return r


_REPORT_SAYS = {TraceDisposition.INCLUDED: "included", TraceDisposition.DROPPED: "left out of this version",
                TraceDisposition.MISSED: "not covered", TraceDisposition.NOT_PRODUCT: "not the product's to do"}


def report_section(s: Session, project_id: int) -> str:
    """What the product's current spec did with each thing the owner decided, for the final report: Daniel's report
    said all four of DEC-4's changes were added while none was in the spec."""
    spec, card = _live_card(s, project_id)
    rows = (card.action_params or {}).get("owner_trace") if card is not None else None
    if not rows:
        return ""
    lines = "\n".join(f"- [{r['source']}] {_REPORT_SAYS[TraceDisposition(r['disposition'])]}"
                      + (f" ({', '.join(r['requirement_ids'])})" if r["requirement_ids"] else "")
                      + f": {r['says']}" + (f" (reason: {r['reason']})" if r["reason"] else "") for r in rows)
    return (f"## What the spec did with what the owner decided ({spec.spec_key}, {spec.status})\n{lines}\n"
            "Say a change was added to the spec only for what this list marks included. For the rest, say plainly "
            "that the spec does not hold it yet, and why when the list gives a reason.")


_HEADINGS = ((TraceDisposition.DROPPED, "Left out of this version"),
             (TraceDisposition.MISSED, "Not covered by the spec (to check)"),
             (TraceDisposition.INCLUDED, "Included"), (TraceDisposition.NOT_PRODUCT, "Not the product's to do"))


def markdown(key: str, rows: list[dict]) -> str:
    """owner-trace.md, which the engineer and the CPO's acceptance check read: what the spec did with each thing the
    owner wrote or approved."""
    out = [f"# {key} Trace of what the owner decided", "",
           ("What this spec did with each thing the owner wrote or approved. The engineer and the CPO's acceptance "
            "check confirm that everything under Included was really built.")]
    for disposition, title in _HEADINGS:
        if picked := [r for r in rows if r["disposition"] == disposition.value]:
            out += ["", f"## {title}"]
            out += [f"- [{r['source']}] {r['says']}"
                    + (f" → {', '.join(r['requirement_ids'])}" if r["requirement_ids"] else "")
                    + (f" (reason: {r['reason']})" if r["reason"] else "") for r in picked]
    return "\n".join(out) + "\n"


def counts(rows: list[dict]) -> dict[str, int]:
    return {d.value: sum(r["disposition"] == d.value for r in rows) for d in TraceDisposition}


def card_line(rows: list[dict], lang: Lang) -> str:
    """The spec card's first line about the owner's own asks."""
    if not rows:
        return msg("build.trace.none", lang)
    n = counts(rows)
    asked = n["included"] + n["dropped"] + n["missed"]
    if not asked:
        return msg("build.trace.no_features", lang)
    if not n["dropped"] and not n["missed"]:
        return msg("build.trace.all_in", lang, n=asked)
    gaps = [msg(f"build.trace.{kind}", lang, n=n[kind]) for kind in ("dropped", "missed") if n[kind]]
    return msg("build.trace.gaps", lang, n=asked, gaps=msg("build.trace.and", lang).join(gaps))
