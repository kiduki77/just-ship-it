"""What the owner needs on a merge card to find what to fix before approving it: a way to use the build, the user
journeys as the worker walked them on this build, each criterion the product agent checked with what it expected
(grouped by what it protects), where the build and the spec disagree, what the build is made of, and how far its
branch is from the base.

Everything comes from what the acceptance check already recorded on the job (`worker._accept`), never from free text
read again. The steps to use it are the product agent's reading of the branch's own files (`AcceptanceOut.use`, the
same steps the after-merge decision gives, usable.py), set after the fixed commands that copy the branch into a folder
of its own (try_it.clone_steps), and labelled on the card as copied from the product's files. Every list and line is
capped, the way the card's other fields are.
"""
from __future__ import annotations

from justshipit.contracts import owner_text
from justshipit.db.models import DevJob
from justshipit.engineering import stories, try_it, walkthrough
from justshipit.i18n import LANGS, msg

_CRITERIA, _CRITERION, _EVIDENCE = 60, 400, 500
_ABOUT, _JOURNEY, _STEP_WORDS, _SCENARIO, _GUARDS = 300, 240, 300, 1000, 160
_JOURNEY_FIELDS = ("id", "priority", "title", "persona", "why", "brings", "success")
_GAPS, _GAP = 30, 300
_FINDINGS, _FINDING = 20, 300
_TASKS, _TASK_TITLE, _TASK_DETAIL = 40, 160, 400
_STEPS, _STEP = 12, 300
_NEEDS, _NEED = 8, 300
# A P1 scenario and a story that did not run are criteria already (stories.criteria): the card lists them once.
_GAP_KINDS = ("unchecked", "built_partly", "built_no", "beyond")
_MARK = {"given": "\x00given\x00", "when": "\x00when\x00", "then": "\x00then\x00"}
_COUNT = "\x00n\x00"


def card(job: DevJob, repository: str, head: str | None = None) -> dict:
    """{"use", "criteria", "gaps", "findings", "items", "commits_ahead", "walkthrough", "checked"} for the merge card.
    `use` is None when the branch's files give no way to run it; `items` is None for a build whose plan had no list.
    `walkthrough` is the run of the card's `head` (or the newest, marked `later`), None for a build accepted before
    builds were walked; `checked` is every criterion but the journeys', grouped by what it protects."""
    result = job.result or {}
    acceptance = result.get("acceptance") or {}
    criteria = (acceptance.get("criteria_results") or [])[:_CRITERIA]
    runs = [x for x in result.get("walkthroughs") or [] if isinstance(x, dict) and isinstance(x.get("head"), str)]
    return {"use": _use(job, repository, acceptance.get("use")),
            "criteria": [_criterion(c) for c in criteria],
            "gaps": [_gap(g) for g in result.get("gaps") or [] if g.get("kind") in _GAP_KINDS][:_GAPS],
            "findings": [said for f in (acceptance.get("design_findings") or [])[:_FINDINGS]
                         if (said := owner_text.one_line(f, _FINDING))],
            "items": _items(result.get("progress")),
            "commits_ahead": result.get("commits_ahead") if isinstance(result.get("commits_ahead"), int) else None,
            "walkthrough": _walkthrough(job, runs, head),
            "checked": _checked(criteria, runs)}


def _walkthrough(job: DevJob, runs: list[dict], head: str | None) -> dict | None:
    """The run the card shows: its own head's, else the newest, which is of a later commit than the card's. What a
    step printed passes as stored, cleaned when it was captured: a terminal's lines and code names are the point. The
    spec's words and the agent's lines are capped like the card's other lines."""
    own = next((x for x in runs if head and x["head"] == head), None)
    run = own or (runs[-1] if runs else None)
    if run is None:
        return None
    shots = f"/api/dev-jobs/{job.id}/walk/{run['head'][:12]}/"
    about = run.get("about") if isinstance(run.get("about"), dict) else None
    return {"head": run["head"], "spec_commit": str(run.get("spec_commit") or ""),
            "ran_at": str(run.get("ran_at") or ""), "ms": int(run.get("ms") or 0),
            "setup_ms": int(run.get("setup_ms") or 0),
            "later": own is None and bool(head),
            "about": {k: _line(about.get(k), _ABOUT) for k in ("what", "who", "problem")} if about else None,
            "setup": [_step(st, shots) for st in run.get("setup") or []],
            "journeys": [{**{k: _line(j.get(k), _JOURNEY) for k in _JOURNEY_FIELDS},
                          "steps": [_step(st, shots) for st in j.get("steps") or []]}
                         for j in run.get("journeys") or []]}


def _step(st: dict, shots: str) -> dict:
    capture = st.get("capture")
    return {**st, **{k: _line(st.get(k), _STEP_WORDS) for k in ("do", "why", "see", "looks_like", "hint")},
            "capture": {**capture, "shot": shots + capture["shot"] if capture.get("shot") else None}
            if isinstance(capture, dict) else None}


def _line(text: object, limit: int) -> str:
    return owner_text.one_line(str(text or ""), limit, names=True)


def _checked(criteria: list[dict], runs: list[dict]) -> dict:
    """The criteria the agent already checked, grouped by what each protects (design 2.5): the core daily flow, each
    story's P1 scenarios with what each guards, the legacy job's scenarios in one group, the owner's rules, what the
    owner asked to change, and the spec's other criteria. A scenario's criterion is matched to its story by the
    exact text stories.criterion writes in each language, never by likeness. The journeys' criteria are the
    walkthrough's, not a check the owner folds away."""
    told = (runs[-1].get("stories") if runs else None) or []
    journeys = {msg("walkthrough.criterion", lang, title=j.get("title") or "", persona=j.get("persona") or "")
                for run in runs for j in walkthrough.p1(run) for lang in LANGS}
    core = {msg("build.core_flow.criterion", lang) for lang in LANGS}
    scenarios = {stories.criterion(sc, lang): (x["id"], sc, lang) for x in told if x.get("priority") == "P1"
                 for sc in x.get("scenarios") or [] for lang in LANGS}
    by_story: dict[str, list[dict]] = {x["id"]: [] for x in told}
    groups: dict[str, list[dict]] = {"core_flow": [], "scenarios": [], "rule": [], "asked": [], "spec": []}
    for c in criteria:
        raw = str(c.get("criterion") or "")
        if raw in journeys:
            continue
        if raw in core:
            groups["core_flow"].append(_item(c))
        elif raw in scenarios:
            sid, sc, _ = scenarios[raw]
            by_story[sid].append(_item(c, sc))
        elif told and (lang := _all_scenarios(raw)):  # the script never ran: each P1 scenario is unmet with it
            for x in told:
                if x.get("priority") == "P1":
                    by_story[x["id"]] += [_item({**c, "criterion": stories.criterion(sc, lang)}, sc)
                                          for sc in x.get("scenarios") or []]
        elif not told and (_all_scenarios(raw) or scenario(raw)):
            groups["scenarios"].append(_item(c))
        else:
            groups[c.get("about") if c.get("about") in ("rule", "asked") else "spec"].append(_item(c))
    out = [_group("core_flow", groups["core_flow"]),
           *(_group("story", by_story[x["id"]], _line(x.get("title"), _JOURNEY), _line(x.get("story"), _SCENARIO))
             for x in told),
           *(_group(kind, groups[kind]) for kind in ("scenarios", "rule", "asked", "spec"))]
    kept = [g for g in out if g["items"]]
    return {"total": sum(g["total"] for g in kept), "met": sum(g["met"] for g in kept), "groups": kept}


def _item(c: dict, sc: dict | None = None) -> dict:
    """A checked criterion; one matched to its story's scenario carries what it guards and its full text."""
    item = _criterion(c)
    if sc is None:
        return {**item, "guards": ""}
    return {**item, "guards": _line(sc.get("guards"), _GUARDS),
            "scenario": {k: _line(sc.get(k), _SCENARIO) for k in ("given", "when", "then")}}


def _group(kind: str, items: list[dict], title: str = "", purpose: str = "") -> dict:
    return {"kind": kind, "title": title, "purpose": purpose, "total": len(items),
            "met": sum(1 for x in items if x["met"]), "items": items}


def _all_scenarios(criterion: str) -> str | None:
    """The language of the one criterion stories.criteria writes for every P1 scenario when the script never ran, or
    None for any other criterion."""
    for lang in LANGS:
        head, _, tail = msg("build.scenario.all", lang, n=_COUNT).partition(_COUNT)
        body = criterion[len(head):len(criterion) - len(tail)] if tail else criterion[len(head):]
        if criterion.startswith(head) and criterion.endswith(tail) and body.isdigit():
            return lang
    return None


def _use(job: DevJob, repository: str, use: dict | None) -> dict | None:
    """How to run the build from its branch. A step cut short would be a broken command, so an overlong list or step
    leaves the card with no steps rather than a wrong one."""
    steps = [" ".join(str(x).split()) for x in (use or {}).get("local_steps") or []]
    if not steps or not job.branch or len(steps) > _STEPS or any(len(x) > _STEP for x in steps):
        return None
    return {"folder": f"products/{repository}", "branch": job.branch,
            "steps": [*try_it.clone_steps(job, repository), *steps],
            "needs": [{"name": owner_text.clip(str(n.get("name") or ""), 80),
                       "what": owner_text.one_line(n.get("what") or "", _NEED, names=True),
                       "without": owner_text.one_line(n.get("without") or "", _NEED, names=True),
                       "required": bool(n.get("required"))} for n in (use.get("needs") or [])[:_NEEDS]],
            "why": owner_text.one_line(use.get("why") or "", _NEED, names=True) or None}


def _criterion(c: dict) -> dict:
    raw = str(c.get("criterion") or "")
    parts = scenario(raw)
    return {"criterion": owner_text.one_line(raw, _CRITERION), "met": bool(c.get("met")),
            "evidence": owner_text.one_line(c.get("evidence") or "", _EVIDENCE),
            "scenario": {k: owner_text.one_line(v, _CRITERION) for k, v in parts.items()} if parts else None}


def scenario(criterion: str) -> dict | None:
    """A P1 scenario's criterion (stories.criteria) back in its parts, read against the catalog line that wrote it in
    every language: {"given", "when", "then"}, or None for any other criterion."""
    for lang in LANGS:
        form = msg("build.scenario.criterion", lang, **_MARK)
        head, rest = form.split(_MARK["given"])
        between, rest = rest.split(_MARK["when"])
        before_then, tail = rest.split(_MARK["then"])
        if not criterion.startswith(head) or (tail and not criterion.endswith(tail)):
            continue
        body = criterion[len(head):len(criterion) - len(tail)]
        given, sep, rest = body.partition(between)
        when, sep2, then = rest.partition(before_then)
        if sep and sep2 and given and when and then:
            return {"given": given, "when": when, "then": then}
    return None


def _gap(g: dict) -> dict:
    return {"kind": g["kind"], "story": g.get("story"), "priority": g.get("priority"),
            "title": owner_text.one_line(g.get("title") or "", _GAP, names=True),
            "detail": owner_text.one_line(g.get("detail") or "", _GAP, names=True)}


def _items(progress: dict | None) -> dict | None:
    tasks = (progress or {}).get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return None
    kept = [{"n": x.get("n"), "done": bool(x.get("done")),
             "title": owner_text.one_line(x.get("title") or "", _TASK_TITLE, names=True),
             "detail": owner_text.one_line(x.get("detail") or "", _TASK_DETAIL, names=True)}
            for x in tasks[:_TASKS] if isinstance(x, dict)]
    return {"done": sum(x["done"] for x in kept), "total": len(kept), "tasks": kept}
