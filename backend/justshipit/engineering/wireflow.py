"""A spec's planned screens: what the owner sees on the spec card before approving, and what the engineer names
captures after. `screens.yaml` beside the spec holds them, keyed by screen id. The id is the spec writer's key as the
server normalised it, and it is the one name a screen has: in a story's flow, in scripts/ui-states.txt and in a
capture's file name."""
from __future__ import annotations

import logging
import re
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy import select

from justshipit.contracts import owner_text
from justshipit.contracts.handoff import (
    SKETCH_BLOCKS, AcceptanceOut, BlockKind, BlockOut, DesignBriefOut, ScreenStateOut, SpecStatus, TraceDisposition,
    UserStoryOut,
)
from justshipit.contracts.requests import RequestStatus
from justshipit.db.models import AgentRequest, DevJob, Job, Spec
from justshipit.engineering import questions, try_it
from justshipit.i18n.match import screen_states
from justshipit.services import matters, queue, registry, requests
from justshipit.services.fence import fenced

log = logging.getLogger(__name__)

FILE = "screens.yaml"
FIRST = "first-open"      # the screen the capture always takes at `/`: it has no ui-states line
MAX_SCREENS = 12          # the capture's limit on ui-states lines: a plan never names more than can be shot
UI_STATES = "scripts/ui-states.txt"
_ID_CHARS = 32

# What `text` and `items` mean for each kind. The spec writer's prompt is generated from it.
VOCAB: dict[BlockKind, str] = {
    BlockKind.NAV: "the top bar. text is the product's name, items are its tabs",
    BlockKind.TITLE: "the screen's headline, in text",
    BlockKind.TEXT: "a sentence, in text",
    BlockKind.NOTICE: "a message in text: an error, a warning, an empty result",
    BlockKind.ACTIONS: "buttons. items are their labels, the main one first",
    BlockKind.FIELD: "an input. text is its label, items are its options (none when the user types)",
    BlockKind.UPLOAD: "an area to drop a file on. text is its prompt",
    BlockKind.LIST: "a list. text is its heading, items are its first rows",
    BlockKind.TABLE: "a table. text is its caption, items are its column names",
    BlockKind.CHIPS: "a row of counts or labels, in items",
    BlockKind.MEDIA: "an image, a chart or a map. text is its caption",
}

# For the engineer: a capture is joined to its planned screen by this name and nothing else.
NAMING_RULE = (f" Name each {UI_STATES} line by a screen id from `.ship/spec/{FILE}`: one line per screen there, "
               f"`<id> /path`, the id exactly as the file gives it. The screen with the id {FIRST} has no line. A state "
               "you add beyond the plan comes after those lines, with your own name.")
PRECEDENCE = (f" `.ship/spec/{FILE}` fixes which screens exist, what is on each and in what order. It sketches content, "
              "never look: ui-standard.md and DESIGN.md decide the look, and design-brief.md's words win where a "
              "sketch's differ.")


class PlannedScreen(BaseModel):
    name: str
    state: str
    shows: str
    blocks: list[BlockOut]    # empty: the frame draws `shows`
    asks: list[str] = []      # the ids of the questions (Q1) whose marker sat in this screen's words


class Step(BaseModel):
    screen: str
    do: str


class Branch(BaseModel):
    screen: str | None        # None: the empty case names no planned screen, and its Then still shows
    then: str


class FlowRow(BaseModel):
    story: str                # US1, as stories.ordered gave it
    steps: list[Step]
    then: str
    empty: Branch | None


class Wireflow(BaseModel):
    screens: dict[str, PlannedScreen]     # keyed by screen id, in the brief's order
    rows: list[FlowRow]
    dropped: int = 0                      # the screens and flow steps `plan` left out

    @model_validator(mode="after")
    def _rows_walk_planned_screens(self) -> Wireflow:
        for row in self.rows:
            named = [st.screen for st in row.steps] + ([row.empty.screen] if row.empty and row.empty.screen else [])
            if not row.steps or any(i not in self.screens for i in named):
                raise ValueError(f"row {row.story} names a screen the plan lacks, or has no step")
        return self

    def loose(self) -> list[str]:
        """The ids no row walks, in order."""
        walked = {st.screen for row in self.rows for st in row.steps} | {
            row.empty.screen for row in self.rows if row.empty}
        return [i for i in self.screens if i not in walked]


def prompt_section() -> str:
    """The part of the spec writer's ask that teaches the sketch: the vocabulary, the key rule and the flow."""
    kinds = "\n".join(f"- {kind.value}: {means}" for kind, means in VOCAB.items())
    return (
        "\nThe owner sees the planned screens on the spec card as grey sketches, in the order a user meets them, and "
        "approves from that picture. So with design_brief, give each item of states two more things.\n"
        "key: a short lowercase ASCII name for that screen in that state, unique in the list (report, "
        f"report-empty). Key the screen a user meets on opening the app `{FIRST}`; when this spec does not change "
        "that screen, no item has that key. Every state of one screen gets the same screen name, so its error or "
        f"empty state is drawn beside the step it belongs to. Write each state as one of {', '.join(STATES)}, or "
        f"normal for the screen in ordinary use. At most {MAX_SCREENS} states besides `{FIRST}` are drawn.\n"
        f"sketch: the screen in that state as blocks, top to bottom, at most {SKETCH_BLOCKS}. A block is a kind, a "
        "text and items; leave the part a kind does not use empty. The kinds:\n"
        f"{kinds}\n"
        "A sketch says what is on the screen and in what order, never how it looks. Write its words exactly as the "
        "screen shows them, in the product's language, the same words `shows` gives. A [NEEDS CLARIFICATION] marker "
        "goes in `shows`, never in a sketch.\n"
        "On each P1 story, flow is the screens its first scenario walks through, in order: each step names a screen "
        "by its key, with what the user does there to reach the next in a few words (an empty string on the last). "
        "empty_screen is the key of the screen its empty_case ends on. Every other story has an empty flow.")


def slug(key: str) -> str:
    """The writer's key in the characters an id may have. A capture's width suffix is no part of a name."""
    out = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:_ID_CHARS].strip("-")
    while (cut := re.sub(r"-(1440|1280)$", "", out)) != out:
        out = cut.strip("-")
    return out


def ids(states: list[ScreenStateOut]) -> list[str | None]:
    """The screen id of each state, in order: its key as a slug, `s{n}` when that leaves nothing, a repeat numbered
    -2, -3. None for a state past the limit, which is not planned."""
    out: list[str | None] = []
    taken: set[str] = set()
    for n, x in enumerate(states, 1):
        base = slug(x.key) or f"s{n}"
        sid, k = base, 1
        while sid in taken:
            k += 1
            sid = f"{base[:_ID_CHARS - len(str(k)) - 1]}-{k}"
        if sid != FIRST and len(taken - {FIRST}) >= MAX_SCREENS:
            out.append(None)
            continue
        taken.add(sid)
        out.append(sid)
    return out


STATES = ("empty", "loading", "error", "warning", "success")


def state_word(state: str) -> str:
    """The state a frame is drawn as: one of STATES, or "" for a screen in normal use. The writer's own word stays in
    design-brief.md and the acceptance criteria; the card never shows a word like save-error or 기본."""
    s = state.strip().lower()
    for word in STATES:
        if any(w in s for says in screen_states.SAYS.values() for w in says[word]):
            return word
    return ""


def _said(text: str, asks: list[str]) -> str:
    """The words without their markers; the questions the markers named join `asks`."""
    clean, named = questions.unmarked(text)
    asks.extend(q for q in named if q not in asks)
    return clean


def plan(brief: DesignBriefOut | None, stories: list[tuple[str, UserStoryOut]]) -> Wireflow | None:
    """The draft's screens as the server keeps them. Pure and total: any draft gives a plan or None, never an error.
    None when the product has no screen. Otherwise:
    - a [NEEDS CLARIFICATION Qn] marker in a screen's words is removed and Qn kept in its `asks`
    - a P1 story's step whose key matches no screen is dropped, two steps in a row on one screen become one, and a
      story left with no step has no row
    - `then` is the story's first scenario's Then; `empty` is its empty_case's Then, on `empty_screen` when that names
      a planned screen
    - `dropped` counts the screens past the limit and the steps that named no screen"""
    if brief is None or not brief.states:
        return None
    screens: dict[str, PlannedScreen] = {}
    by_key: dict[str, str | None] = {}  # the writer's key -> the id of the first screen it names; None past the limit
    dropped = 0
    for x, sid in zip(brief.states, ids(brief.states)):
        if key := slug(x.key):
            by_key.setdefault(key, sid)
        if sid is None:
            dropped += 1
            continue
        asks: list[str] = []
        blocks = [BlockOut(kind=b.kind, text=_said(b.text, asks), items=[w for i in b.items if (w := _said(i, asks))])
                  for b in x.sketch]
        screens[sid] = PlannedScreen(name=_said(x.screen, asks), state=state_word(_said(x.state, asks)),
                                     shows=_said(x.shows, asks), blocks=[b for b in blocks if b.text or b.items],
                                     asks=asks)
    rows = []
    for story, x in stories:
        steps: list[Step] = []
        for st in x.flow if x.priority == "P1" else []:
            key = slug(st.screen)
            if key not in by_key:
                dropped += 1
            elif (sid := by_key[key]) is None:
                continue  # counted with its screen
            elif steps and steps[-1].screen == sid:
                steps[-1].do = questions.unmarked(st.do)[0] or steps[-1].do
            else:
                steps.append(Step(screen=sid, do=questions.unmarked(st.do)[0]))
        if not steps:
            continue
        then = questions.unmarked(x.empty_case.then)[0]
        rows.append(FlowRow(
            story=story, steps=steps, then=questions.unmarked(x.scenarios[0].then)[0] if x.scenarios else "",
            empty=Branch(screen=by_key.get(slug(x.empty_screen)), then=then) if then else None))
    return Wireflow(screens=screens, rows=rows, dropped=dropped)


def dump(w: Wireflow) -> str:
    return yaml.safe_dump(w.model_dump(mode="json"), allow_unicode=True, sort_keys=False)


def at(registry_path: str, commit: str) -> Wireflow | None:
    """The plan as that commit holds it. None for a spec without one, or one whose file no longer reads as a plan."""
    try:
        text = registry.read_at(f"{registry_path}/{FILE}", commit)
    except RuntimeError:
        return None
    try:
        return Wireflow.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as exc:
        log.warning("%s/%s at %s is not a plan: %s", registry_path, FILE, commit[:10], exc)
        return None


def card(w: Wireflow, asked: dict[str, dict]) -> dict:
    """The plan as a spec card draws it. A frame no row walks is drawn beside each step on the screen it is a state
    of (the same name), as that step's `branches`; only the frames left over are `loose`. `asked` is each question
    the spec asked, by id (questions.asked). `change`, `landed` and `removed` are a redraft's, and it has none yet."""
    loose = w.loose()
    rows = [{"story": row.story,
             "steps": [{"screen": st.screen, "do": st.do,
                        "branches": [i for i in loose if w.screens[i].name == w.screens[st.screen].name]}
                       for st in row.steps],
             "then": row.then, "empty": row.empty.model_dump() if row.empty else None} for row in w.rows]
    beside = {i for row in rows for st in row["steps"] for i in st["branches"]}
    return {"mode": "plan",
            "screens": {sid: {**x.model_dump(mode="json"), "asks": [asked[q] for q in x.asks if q in asked],
                              "change": None, "landed": []} for sid, x in w.screens.items()},
            "rows": rows, "loose": [i for i in loose if i not in beside], "removed": [], "dropped": w.dropped}


def for_card(s, req: AgentRequest) -> dict | None:
    """What the detail fetch adds for a spec card with planned screens, read at the commit the card pins, so the owner
    sees the sketches of exactly what approving builds. None for any other card and for a spec without a plan."""
    params = req.action_params or {}
    if req.action_kind == "dev.merge":
        return merge_card(s, params.get("dev_job_id"))
    spec = s.get(Spec, params.get("spec_id") or 0) if req.action_kind == "dev.spec_approve" else None
    if spec is None or not params.get("commit_sha"):
        return None
    w = at(spec.registry_path, params["commit_sha"])
    return _with_notes(s, req, spec, w, card(w, questions.asked(spec.registry_path, params["commit_sha"]))) if w else None


def changed(before: Wireflow | None, after: Wireflow | None) -> dict[str, Literal["new", "changed", "removed"]]:
    """The screens that differ between two drafts, by id: `changed` when the frame draws something else (its name, its
    state, its blocks, or its words when it has no blocks). Words reworded under the same blocks draw the same frame.
    With no earlier draft every screen is `new`."""
    was = before.screens if before else {}
    now = after.screens if after else {}
    return {**{i: "new" if i not in was else "changed" for i, x in now.items()
               if i not in was or _drawn(was[i]) != _drawn(x)},
            **{i: "removed" for i in was if i not in now}}


def _drawn(x: PlannedScreen) -> tuple:
    """What a frame draws: its words only when it has no blocks."""
    return x.name, state_word(x.state), [b.model_dump(mode="json") for b in x.blocks] or x.shows


def note_ref(request: str, screen: str) -> str:
    """The id of the owner's note on one screen of a spec card, as the spec writer cites it: REQ-41@report."""
    return f"{request}@{screen}"


def redraft_section(prev: Wireflow, notes: list[dict]) -> str:
    """For the redraft's prompt: the last draft's screens under their keys, and each note of the owner's ({"ref",
    "screen", "note"}) against the key of the screen it is on."""
    screens = yaml.safe_dump({i: x.model_dump(mode="json", exclude={"asks"}) for i, x in prev.screens.items()},
                             allow_unicode=True, sort_keys=False)
    said = "\n".join(f"- [{n['ref']}] on `{n['screen']}`: {n['note']}" for n in notes)
    return ("## The owner sent your last draft back with notes on its screens\n"
            "Write the whole spec again. The owner approves from the screens, so what the notes do not touch stays "
            "as it was: the same stories, requirements and screens, and every screen keeps its key. Change the "
            "screens the notes name, and whatever else in the spec those changes need. Each note is also in the "
            "owner's list under the id given here. A note counts as included only when a screen you cite for it "
            "really differs from the last draft below.\n"
            + fenced("the owner's notes, each on the key of its screen", said) + "\n"
            + fenced("the screens of your last draft, by key", screens))


REDRAFT = "redraft_spec"  # the job that writes a spec again from the owner's notes on its screens
GONE = ("failed", "cancelled")  # a redraft job that ended unwritten, which the owner can run again


def redraft_jobs(s, spec: Spec) -> tuple[Job | None, bool]:
    """The spec's last redraft job, and whether one is still in flight. A spec sent back (DRAFT, no approval card) with
    none in flight has a failed redraft: nothing stores that, so nothing can say otherwise."""
    of_spec = (Job.kind == REDRAFT, Job.payload["spec_id"].as_integer() == spec.id)
    return (s.scalar(select(Job).where(*of_spec).order_by(Job.id.desc()).limit(1)),
            s.scalar(select(Job.id).where(*of_spec, queue.in_flight()).limit(1)) is not None)


def sent_back(spec: Spec) -> bool:
    """Whether the spec waits to be written again from the owner's notes."""
    return spec.status == SpecStatus.DRAFT.value and spec.approval_request_id is None


def _redraft(s, req: AgentRequest, spec: Spec) -> dict | None:
    """Where the redraft a declined card asked for stands: written (the card that replaced this one), being written,
    stopped by the owner or failed (with the job the owner can run again), or given up on (the notes dropped)."""
    resp = req.response or {}
    if req.status != RequestStatus.REJECTED.value or not resp.get("screen_notes"):
        return None
    if (after := matters.request_ref(resp.get("superseded_by"))) is not None:
        return {"state": "done", "card": after, "retry_job": None}
    if resp.get("notes_dropped"):
        return {"state": "dropped", "card": None, "retry_job": None}
    if not sent_back(spec) or spec.draft_commit_sha != req.action_params["commit_sha"]:
        return None
    last, live = redraft_jobs(s, spec)
    state = "writing" if live else "stopped" if last is not None and last.status == "cancelled" else "failed"
    return {"state": state, "card": None,
            "retry_job": last.id if last is not None and last.status in GONE and not live else None}


_NOT_BUILT = (TraceDisposition.DROPPED.value, TraceDisposition.NOT_PRODUCT.value)


def _landing(note: dict, name: str, rows: list[dict]) -> dict:
    """Where one note landed, from the trace rows about it: on its own screen (`changed`), on another (`moved`),
    left out with the spec's reason (`dropped`), or nowhere (`missed`)."""
    on = list(dict.fromkeys(i for r in rows if r["disposition"] == TraceDisposition.INCLUDED.value
                            for i in r.get("screens", [])))
    left_out = next((r for r in rows if r["disposition"] in _NOT_BUILT), None)
    if note["screen"] in on:
        outcome, reason = "changed", ""
    elif on:
        outcome, reason = "moved", ""
    elif left_out is not None:
        outcome, reason = "dropped", left_out["reason"]
    else:
        outcome, reason = "missed", next((r["reason"] for r in rows if r["reason"]), "")
    return {"screen": note["screen"], "name": name, "note": note["note"], "outcome": outcome, "reason": reason,
            "on": on}


def _with_notes(s, req: AgentRequest, spec: Spec, w: Wireflow, out: dict) -> dict:
    """The card's plan with the owner's notes on it. On a card the owner sent back: the note on each screen, and where
    the redraft stands. On the card of a redraft: what changed since the draft it answers, and where each note of that
    draft's card landed. All of it is read here from the two commits and the card's trace, so a trace written again
    (owner_trace.restate) never leaves a frame saying something else."""
    params = req.action_params
    sent = {n["screen"]: n["note"] for n in (req.response or {}).get("screen_notes") or []}
    for sid, x in out["screens"].items():
        x["note"] = sent.get(sid)
    out["redraft"] = _redraft(s, req, spec)
    out["unplaced"] = []
    of = params.get("redraft_of")
    old = s.get(AgentRequest, of["request"]) if of else None
    if old is None:
        return out
    prev = at(spec.registry_path, of["commit"])
    diff = changed(prev, w)
    was = prev.screens if prev else {}
    for sid, x in out["screens"].items():
        x["change"] = diff.get(sid)
    gone = {sid: {"id": sid, "name": was[sid].name, "state": was[sid].state, "landed": []}
            for sid, how in diff.items() if how == "removed"}
    for note in (old.response or {}).get("screen_notes") or []:
        ref = note_ref(requests.display_id(old.id), note["screen"])
        name = was[note["screen"]].name if note["screen"] in was else ""
        landing = _landing(note, name, [r for r in params.get("owner_trace") or [] if r["source"] == ref])
        # On its own screen whatever became of it, and on every screen that changed for it, a removed one included.
        frames = [out["screens"].get(sid) or gone[sid] for sid in dict.fromkeys([note["screen"], *landing["on"]])
                  if sid in out["screens"] or (sid in gone and sid in landing["on"])]
        for frame in frames:
            frame["landed"].append(landing)
        if not frames:
            out["unplaced"].append(landing)
    out["removed"] = list(gone.values())
    return out


def shot_of(w: Wireflow, files: list[str]) -> dict[str, str]:
    """Planned screen id -> its capture file, from the files of one acceptance run. The only code that maps a capture's
    name to a screen: `{id}{SHOT_AT}.png`. The second width of the first open names no screen."""
    suffix = f"{try_it.SHOT_AT}.png"
    return {sid: f for f in files if f.endswith(suffix) and (sid := f.removesuffix(suffix)) in w.screens}


def missing(w: Wireflow, files: list[str]) -> list[str]:
    """The planned ids with no capture, in plan order."""
    shot = shot_of(w, files)
    return [i for i in w.screens if i not in shot]


def acceptance_section(w: Wireflow, files: list[str], where: str) -> str:
    """What the CPO is told of the planned screens: each id with its capture's path under `where`, and the ones with
    none. It answers in AcceptanceOut.screens."""
    shot = shot_of(w, files)
    listing = "\n".join(f"- `{i}`: " + (f"`{where}/{shot[i]}`" if i in shot else "not captured") for i in w.screens)
    return (f"\n\n## Planned screens\n`.ship/spec/{FILE}` plans these screens, and each capture is named after its "
            f"screen's id:\n{listing}\n\nAdd one `screens` entry for each planned screen that has a capture: "
            "screen_id is its id exactly as listed; built says whether the capture shows what the plan sketched (yes, "
            "partly, no, or unseen when you could not tell); differs says in one sentence, in the owner's language, "
            "where the capture differs, or is empty when it does not. A plan sketches content and order, never look, so "
            "a different look is no difference. A screen listed as not captured gets no entry, and its missing capture "
            "alone does not make the build unaccepted: the owner sees it listed as not captured.")


def _order(w: Wireflow) -> list[str]:
    """The screens in the order a user meets them: each step's screen and the states beside it, the empty case's
    screen, then the screens no story walks."""
    c = card(w, {})
    seq = [i for row in c["rows"] for st in row["steps"] for i in (st["screen"], *st["branches"])]
    seq += [row["empty"]["screen"] for row in c["rows"] if row["empty"] and row["empty"]["screen"]] + c["loose"]
    return list(dict.fromkeys(seq))


def built(w: Wireflow, job: DevJob) -> dict:
    """The plan beside what the job's last acceptance run captured and said of it. A planned screen with no capture has
    `shot: None` and no verdict; a verdict for an id the plan lacks is dropped; `extra` is the captures no planned screen
    claims."""
    result = job.result or {}
    files = (result.get("screens") or {}).get("files") or []
    shot = shot_of(w, files)
    acceptance = AcceptanceOut.model_validate(result["acceptance"]) if result.get("acceptance") else None
    said = {}
    for c in acceptance.screens if acceptance else []:
        said.setdefault(c.screen_id, c)
    screens = {}
    for sid, x in w.screens.items():
        c = said.get(sid) if sid in shot else None
        screens[sid] = {**x.model_dump(mode="json", include={"name", "state", "shows", "blocks"}),
                        "built": {"shot": try_it.shot(job.id, shot[sid]) if sid in shot else None,
                                  "verdict": c.built if c else None,
                                  "differs": owner_text.scrub(c.differs, line=True).strip() if c else ""}}
    claimed = set(shot.values())
    extra = [try_it.shot(job.id, f) for f in files
             if f not in claimed and try_it.SHOT_NAME.fullmatch(f) and not f.endswith(f"{try_it.SHOT_ALSO}.png")]
    return {"mode": "built", "screens": screens, "order": _order(w), "extra": extra}


def merge_card(s, job_id) -> dict | None:
    """The merge card's screens: the plan at the commit the build was approved from, beside the job's captures. None
    for a build of a spec without planned screens."""
    job = s.get(DevJob, job_id) if isinstance(job_id, int) else None
    spec = s.get(Spec, job.spec_id) if job is not None else None
    if spec is None or not spec.approved_commit_sha:
        return None
    w = at(spec.registry_path, spec.approved_commit_sha)
    return built(w, job) if w else None


def lint_ui_states(text: str, w: Wireflow) -> list[str]:
    """What scripts/ui-states.txt gets wrong against the plan, as sentences for the engineer: planned screens with no
    line among the ones the capture reads, and lines that name no planned screen."""
    names = [line.split()[0] for line in text.splitlines() if line.strip() and not line.startswith("#")][:MAX_SCREENS]
    planned = [i for i in w.screens if i != FIRST]
    out = []
    if missing := [i for i in planned if i not in names]:
        out.append(f"{UI_STATES} has no line for these planned screens in its first {MAX_SCREENS}: "
                   f"{', '.join(missing)}. Add `<id> /path` for each, the id exactly as `.ship/spec/{FILE}` gives it.")
    if unknown := [n for n in names if n not in planned]:
        out.append(f"These {UI_STATES} lines name no planned screen: {', '.join(unknown)}. Rename a line that shows "
                   "a planned screen to that screen's id. A state you added beyond the plan keeps its name and goes "
                   "after the planned ones.")
    return out
