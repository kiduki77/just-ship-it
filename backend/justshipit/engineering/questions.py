"""What the owner decides about a spec before it is approved: each [NEEDS CLARIFICATION] the spec writer marked, and
each change it proposes to the product's constitution, as one decision card with the question first and a recommended
answer. The clarify step follows GitHub Spec Kit's /clarify (MIT, Copyright GitHub, Inc.): at most three questions, a
recommended answer with its reason, and the answers written into the spec as `- Q: ... → A: ...`.

The spec waits in CLARIFICATION_REQUIRED without its approval card. Each closed card queues `spec_questions`; once none
is open, specs.on_questions_answered writes the answers in and raises the spec card. A card the owner put off or
turned down settles too, so the spec never waits on it: a clarification takes the recommended answer, marked as
assumed on the spec card, and a constitution change is not made. A note on such a card stays a note beside the
assumed answer; only a pick, or the owner's own answer, decides. A constitution change states both sides and
recommends neither: the rules are the owner's.
"""
from __future__ import annotations

import re
from datetime import date

import yaml
from sqlalchemy import select

from justshipit.contracts.handoff import SPEC_QUESTION_KEY, ClarifyOut, PrincipleChangeOut
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import (
    AgentRequestDraft, CardOption, DecisionCard, HumanDecision, RequestStatus, RequestType, RiskLevel,
)
from justshipit.db.models import AgentRequest, Spec
from justshipit.engineering import constitution
from justshipit.i18n import Lang, msg
from justshipit.services import registry, requests

FILE = "questions.yaml"  # in a spec that asked any
KEY = SPEC_QUESTION_KEY
MAX = 3                   # Spec Kit's limit on [NEEDS CLARIFICATION] markers
# A marker as the spec writer put it, or as YAML folded it: "[NEEDS CLARIFICATION Q1: where will it live?]".
_MARKER = re.compile(r"\[NEEDS\s+CLARIFICATION(?:\s+(Q\d+))?\s*:?\s*([^\]]*)\]")


def drafted(clarifications: list[ClarifyOut], changes: list[PrincipleChangeOut], rules: dict | None,
            lang: Lang) -> list[dict]:
    """The questions a draft asks the owner: its clarifications, then its constitution changes, at most MAX of each."""
    qs = [{"id": f"Q{n}", "kind": "clarify", "question": x.question, "matters": x.why_it_matters,
           "options": [o.model_dump() for o in x.options], "why": x.why_recommended}
          for n, x in enumerate(clarifications[:MAX], 1) if x.options]
    kept = [c for x in changes[:MAX] if (c := constitution.change_of(x, rules)) is not None]
    for n, c in enumerate(kept, 1):
        keep = c["old"] or msg("build.amend.no_rule", lang)
        qs.append({"id": f"A{n}", "kind": "amend", "question": msg("build.amend.question", lang),
                   "matters": msg("build.amend.matters", lang, old=keep, rule=c["rule"]), "change": c,
                   "options": [{"answer": msg("build.amend.change", lang), "means": c["rule"]},
                               {"answer": msg("build.amend.keep", lang), "means": keep}],
                   "why": msg("build.amend.why", lang)})
    return qs


def ask(s, spec: Spec, qs: list[dict], lang: Lang) -> None:
    """One decision card per question, bound to the spec so only its own flow closes it, and never expiring: it grants
    nothing, and the spec waits on it."""
    for q in qs:
        card = DecisionCard(
            headline=q["question"], owner_does=msg("build.clarify.owner_does", lang), why=q["why"], metrics=[],
            options=[CardOption(label=o["answer"], verdict=HumanDecision.ANSWER, summary=o["means"], evidence_ids=[],
                                risks=[], recommended=i == 0 and q["kind"] == "clarify")
                     for i, o in enumerate(q["options"])],
            recommendation=f"{q['matters']} {q['why']}" if q["kind"] == "clarify" else "", reversible=True)
        req = requests.create(s, AgentRequestDraft(
            type=RequestType.QUESTION, title=msg("build.clarify.title", lang, title=spec.title, question=q["question"]),
            summary="\n\n".join([q["question"], q["matters"], *(f"- {o['answer']}: {o['means']}" for o in q["options"])]),
            rationale=msg("build.clarify.rationale", lang, title=spec.title), options=[], recommended_option=None,
            action_kind=None, estimated_cost_usd=None, risk_level=RiskLevel.LOW, card=card,
        ), from_agent="product", project_id=spec.project_id, origin=ServerOrigin(OriginKind.SPEC_QUESTION, spec.id),
            system=True, dedupe=False, action_params={"spec_id": spec.id, KEY: q["id"]})
        q["request_id"] = req.id


def answered(q: dict, card: AgentRequest, lang: Lang) -> dict:
    """The owner's answer to one question, from its closed card: the option he picked, in its whole words (the card's
    label is clipped), or his own answer, with any note he added. A card he put off or turned down decides nothing:
    a clarification takes the recommended answer and a constitution change is not made, each marked assumed, and his
    note is kept beside it as a note. A rule change he picked with a note is made with the note, in his words.

    `change` is the constitution change made, or None."""
    resp = card.response or {}
    note = (resp.get("note") or "").strip()
    labels = [o.get("label") for o in ((card.action_params or {}).get("card") or {}).get("options", [])]
    chosen = (resp.get("chosen_option") or "").strip()
    picked = next((i for i, o in enumerate(q["options"]) if chosen and chosen in (o["answer"], *labels[i:i + 1])), None)
    own = (resp.get("answer") or "").strip()
    own = "" if own in (chosen, *labels) else own
    said = card.status == RequestStatus.ANSWERED.value and (picked is not None or bool(own or chosen))
    row = {"id": q["id"], "kind": q["kind"], "question": q["question"]}
    if not said:
        put_off = note and resp.get("responded_by") != "system"
        return {**row, "answer": q["options"][1 if q["kind"] == "amend" else 0]["answer"], "assumed": True,
                **({"note": note} if put_off else {}), **({"change": None} if q["kind"] == "amend" else {})}
    words = own or (q["options"][picked]["answer"] if picked is not None else chosen)
    answer = " · ".join(x for x in (words, note) if x)
    if q["kind"] == "clarify":
        return {**row, "answer": answer, "assumed": False}
    made = picked == 0 and not own
    change = {**q["change"], "rule": f"{q['change']['rule']} ({note})" if note else q["change"]["rule"]} if made else None
    return {**row, "answer": answer, "assumed": False, "change": change}


def written_in(text: str, answers: list[dict], lang: Lang) -> str:
    """The spec text with each marker replaced by the owner's answer, where the spec writer put it. A marker no
    question asked (past the limit, unnumbered, or with no answers to pick from) never reaches the engineer as one: it
    says the point is open, which the engineer's intake asks about."""
    by_id = {a["id"]: a for a in answers}

    def put(m: re.Match) -> str:
        a = by_id.get(m.group(1))
        if a is None:
            return "[" + msg("build.clarify.open_inline", lang, what=" ".join(m.group(2).split())) + "]"
        return "[" + msg("build.clarify.assumed_inline" if a["assumed"] else "build.clarify.decided_inline", lang,
                         answer=a["answer"]) + "]"
    return _MARKER.sub(put, text)


def unmarked(text: str) -> tuple[str, list[str]]:
    """The text without its markers, and the ids of the questions they named, in order."""
    ids = list(dict.fromkeys(m.group(1) for m in _MARKER.finditer(text) if m.group(1)))
    return re.sub(r"[ \t]{2,}", " ", _MARKER.sub("", text)).strip(), ids


def written_in_yaml(text: str, answers: list[dict], lang: Lang) -> str:
    """The same for a YAML file, through its values: a marker may sit across a folded line."""
    def walk(x):
        if isinstance(x, str):
            return written_in(x, answers, lang)
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x
    return yaml.safe_dump(walk(yaml.safe_load(text)), allow_unicode=True, sort_keys=False)


def section(answers: list[dict], lang: Lang) -> str:
    """The prd's Clarifications section: each question and its answer, the assumed ones marked."""
    def said(a: dict) -> str:
        notes = [msg("build.clarify.assumed_note", lang)] if a["assumed"] else []
        notes += [msg("build.clarify.owner_note", lang, note=a["note"])] if a.get("note") else []
        return f" ({'; '.join(notes)})" if notes else ""
    lines = [f"- Q: {a['question']} → A: {a['answer']}{said(a)}" for a in answers]
    return "\n\n## Clarifications\n\n### Session " + date.today().isoformat() + "\n\n" + "\n".join(lines) + "\n"


def card_rows(answers: list[dict]) -> list[dict]:
    """What the spec card shows of the answers."""
    return [{"kind": a["kind"], "question": a["question"], "answer": a["answer"], "assumed": a["assumed"],
             **({"note": a["note"]} if a.get("note") else {})} for a in answers]


def dump(qs: list[dict], card: dict, answers: list[dict] | None = None) -> str:
    """questions.yaml: the questions, the spec card that waits for them, and the answers once there are."""
    return yaml.safe_dump({"questions": qs, "card": card, **({"answers": answers} if answers is not None else {})},
                          allow_unicode=True, sort_keys=False)


def made(base: str, commit: str) -> list[dict]:
    """The constitution changes the owner chose on a spec's questions, as its commit records them."""
    try:
        record = yaml.safe_load(registry.read_at(f"{base}/{FILE}", commit))
    except RuntimeError:
        return []
    return [a["change"] for a in (record or {}).get("answers") or [] if a.get("change")]


def asked(base: str, commit: str) -> dict[str, dict]:
    """Each question a spec asked the owner, by id, as its commit records it: the question, and the answer once the
    owner gave one (None until then, and for the answer assumed when the owner put the question off)."""
    try:
        record = yaml.safe_load(registry.read_at(f"{base}/{FILE}", commit)) or {}
    except RuntimeError:
        return {}
    answers = {a["id"]: a["answer"] for a in record.get("answers") or [] if not a.get("assumed")}
    return {q["id"]: {"question": q["question"], "answer": answers.get(q["id"])} for q in record.get("questions") or []}


def cards(s, spec: Spec) -> dict[str, AgentRequest]:
    """The spec's question cards, by the question each asks: the newest, since a spec written again from a revised
    plan asks its questions again."""
    found = s.scalars(select(AgentRequest).where(AgentRequest.action_params["spec_id"].as_integer() == spec.id,
                                                 AgentRequest.action_params.has_key(KEY)).order_by(AgentRequest.id))
    return {c.action_params[KEY]: c for c in found}


def open_cards(s, spec: Spec) -> list[AgentRequest]:
    return [c for c in cards(s, spec).values() if c.status == RequestStatus.PENDING.value]


# The spec's own files that the answers are written into; the sources, the question record and the constitution are not
# the spec writer's text.
_NOT_WRITTEN = ("sources/", FILE, constitution.FILE, constitution.RULES)


def settled(files: dict[str, str], answers: list[dict], lang: Lang) -> dict[str, str]:
    """The spec's own files (by name inside the spec) with every marker settled, and the Clarifications section on the
    prd when there are answers. A YAML file is read, settled through its values and written again, since its dump may
    fold a marker across lines."""
    out = {}
    for name, text in files.items():
        if name.startswith(_NOT_WRITTEN):
            continue
        new = written_in_yaml(text, answers, lang) if name.endswith(".yaml") else written_in(text, answers, lang)
        if name == "prd.md" and answers:
            new = new.rstrip("\n") + "\n" + section(answers, lang)
        if new != text:
            out[name] = new
    return out


def written_back(base: str, sha: str, answers: list[dict], lang: Lang) -> dict[str, str]:
    """The spec's files at `sha` with the owner's answers in."""
    names = [rel.removeprefix(f"{base}/") for rel in registry.tree_at(base, sha)]
    files = {n: registry.read_at(f"{base}/{n}", sha) for n in names if not n.startswith(_NOT_WRITTEN)}
    return {f"{base}/{n}": text for n, text in settled(files, answers, lang).items()}
