"""Spec Registry: CPO writes the PRD, the owner approves it, the approved commit is pinned."""
from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import func, select, update

from justshipit.agents.executor import run_agent
from justshipit.contracts.handoff import (
    DEV_JOB_DELIVERED, DesignBriefOut, DevJobStatus, Handoff, HandoffApproval, HandoffSource, HandoffTarget,
    HandoffWorkflow, SpecDraftOut, SpecStatus,
)
from justshipit.contracts.plan import PlanStatus
from justshipit.contracts.product import ExistingProduct
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import (
    AgentRequestDraft, HumanResponse, RequestStatus, RequestType, RiskLevel, ScreenNote,
)
from justshipit.db.models import AgentRequest, Artifact, DevJob, Job, Plan, Project, Spec, Task
from justshipit.db.session import session_scope
from justshipit.engineering import constitution, journeys, owner_trace, plans, questions, stories, wireflow
from justshipit.i18n import Lang, glossary, msg
from justshipit.i18n.match import test_words
from justshipit.i18n.said import said
from justshipit.orchestration import context as ctx
from justshipit.sandbox import hostgit
from justshipit.services import activity, app_settings, existing_product, matters, queue, registry, requests
from justshipit.services.fence import fenced
from justshipit.services.tasks import display_id
from justshipit.settings import settings

log = logging.getLogger(__name__)

def _spec_key(s, p: Project) -> str:
    code = re.sub(r"[^A-Z0-9]", "", p.slug.upper())[:6] or f"P{p.id}"
    # Slugs that share their first six letters (test-dev-pipeline, test-dev-pipeline-2) must not share keys.
    if s.scalar(select(Spec.id).where(Spec.spec_key.like(f"{code}-%"), Spec.project_id != p.id).limit(1)):
        code = f"{code}{p.id}"
    n = (s.scalar(select(func.count(Spec.id)).where(Spec.project_id == p.id)) or 0) + 1
    return f"{code}-{n:04d}"


def _repo_name(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:60] or "product"


SPEC_ASK = (
    "Write a spec package the engineering team can build without making a business assumption. Scope it to the "
    "smallest version that tests the core hypothesis; for a product that already exists, the smallest change to its "
    "current code. The tests run in a fresh suggested_test_image container: the "
    "repository's scripts/ci-setup.sh installs the dependencies, then suggested_test_command runs. The full report and "
    "task results above are saved with the spec under sources/, and the engineering team reads them at "
    "`.ship/spec/sources/`. Do not recompute or shorten their tables, numbers and rules: when you need a part cut "
    "off here, quote that source file (for example the table in sources/T-30.md) in the requirement as it is.\n"
    "Write the user stories as user_stories, most important first: P1 is the smallest version worth shipping, and "
    "every story is independently testable and shippable, so built alone it still gives the user something whole. "
    "Each story is one plain sentence the owner reads on the spec card, with Given/When/Then scenarios on a normal "
    "day and its empty_case: the same story on the day nothing has happened yet (no data, every flag no, an empty "
    "list), where the user still gets a result that says so. The P1 scenarios are tests: the build's own script runs "
    "them at the acceptance check, so write each Given with data shaped like the owner's own, and describe that shape "
    "in owner_data. Requirements and the PRD say what and why, never how: frameworks, libraries, files and APIs go in "
    "design_notes_markdown.\n"
    "Write `about` from the approved plan's title, goal and users lines (the PRD's, when there is no plan), adding no "
    "claim they don't make. Then write 1 to 3 `journeys`: how real people will use this version. Each names one "
    "person (a role and a moment, not a tester), why they came, what they bring, 2 to 8 steps in their words, what "
    "they see at each step, and what they have when it worked. A journey is not a test. Never repeat a step to "
    "compare runs, never check what the product leaves alone, never name a command or a file format the person would "
    "not know. Those belong in `user_stories` scenarios, which the build's script runs. Give each scenario `guards`: "
    "what it catches, in one line the owner reads.\n"
    "Where the owner has not said, make an informed guess from the research, the plan and common practice, and write "
    "it in assumptions. Mark a question [NEEDS CLARIFICATION Qn: ...] and put it in clarifications only when it "
    "changes the scope or the user's experience a lot, has several reasonable answers, and has no reasonable default "
    "(where the app lives and who can open it, what happens to a payment); at most 3, most important first. Each "
    "becomes a decision card the owner answers before approving the spec, so write the question, the answers and "
    "why you recommend the first in the owner's plain words.\n"
    "Set product_language to the language the product's users read. Take it from who they are and where, not from "
    "the language you write this spec in. The product's screens, copy, README and messages are written in it, and so "
    "is every exact screen text you give. That text speaks to the product's users: it never addresses the owner by "
    "name (when a user must reach someone, name the role or say \"us\"). When the product speaks more than one "
    "language, every message carries the same facts in each language (a count, a date, how many days late).\n"
    "If the product has a screen people use, fill design_brief. The engineering team designs the screens from it, so "
    "start from this business's users and their moment (where, on what device, how rushed), and write the core task "
    "and the order the first screen is taken in, not a list of components. In states, give every core screen's empty "
    "state (the first day), error (a wrong entry, a failed save) and success (just after saving or finishing), none "
    "left out, each with its screen text. The server turns these states and the core task's step count into "
    "acceptance criteria. For a product that already has screens, the design brief covers only the screens and "
    "states this change touches, and its feel is the product's current look: the change keeps that look and adds no "
    "design system." + wireflow.prompt_section())


def _code_ask(repo: Path, branch: str) -> str:
    from justshipit.engineering.current_state import repo_map
    return (f"## This product's code\nThe working directory holds products/{repo.name} as it is today (branch {branch}), "
            "read-only. Before you specify a change, read the code the change touches with Read, Glob and Grep, and "
            "name in the spec only files and modules you opened. Extend what is there (its copy or language file, "
            "its components, its styles) rather than adding a layer beside it. This call has no web tools: the "
            "research is in the report and task results above.\n\n" + fenced("Repository map", repo_map(repo)))


def _code_to_read(s, project_id: int, product: ExistingProduct | None, pinned: str | None) -> tuple[Path, str] | None:
    """The product's code the spec is written against, as (repository, branch): the product's one repository at the
    branch the owner brought, or the one an earlier version was merged into. None while the product has no code."""
    if pinned is None:
        return None
    if product is not None:
        branch = product.base_branch
    elif merged := [sp for sp in matters.product_specs(s, project_id) if sp.status == SpecStatus.MERGED.value]:
        branch = ((merged[-1].handoff or {}).get("target") or {}).get("base_branch") or "main"
    else:
        return None
    repo = settings().products_dir / pinned
    # A full ref, so a branch named like an option ("--index-output=x") is never read as one by git.
    has = (repo / ".git").exists() and hostgit.run(repo, "rev-parse", "--verify", "-q",
                                                   f"refs/heads/{branch}^{{commit}}").returncode == 0
    return (repo, branch) if has else None


def _draft(prompt: str, project_id: int, code: tuple[Path, str] | None) -> SpecDraftOut:
    """The CPO's draft. With code to read, the call reads an export of that branch (no .git) with Read, Glob and Grep
    only, in the read-only sandbox the current-state report and the code reviewer use, and has no web tools: the
    repository's text never sits in a call that can reach the internet."""
    if code is None:
        return run_agent("product", "spec", prompt, SpecDraftOut, project_id=project_id)
    from justshipit.engineering.worker import _export_branch, drop_links
    with tempfile.TemporaryDirectory(prefix="ship-spec-", ignore_cleanup_errors=True) as tmp:
        export = Path(tmp) / "repo"
        export.mkdir()
        _export_branch(code[0], f"refs/heads/{code[1]}", export)
        drop_links(export)
        return run_agent("product", "spec", prompt, SpecDraftOut, project_id=project_id, cwd=export,
                         tools=["read_workspace"])


_NO_P1 = ("\n\n## Your last draft had no P1 user story\nEvery spec builds something, so it has at least one P1 story with "
          "at least one scenario and its empty_case. Write the user_stories again with the smallest version worth "
          "shipping as P1.")


def _has_p1(out: SpecDraftOut) -> bool:
    """Whether the draft says what the build must do first: a P1 story with a scenario. Without one the acceptance check
    has no scenario to run and nothing to call built."""
    return any(x.priority == "P1" and x.scenarios for x in out.user_stories)


_TEST_WORDS = re.compile("|".join(w for words in test_words.WORDS.values() for w in words), re.IGNORECASE)
_JOURNEY_NOTES = {
    "none": "Write 1 to 3 journeys: how real people will use this version.",
    "too_many": "Write at most 3 journeys, the ones that matter most.",
    "steps": "Give every journey 2 to 8 steps.",
    "see": "Say in every step what the person sees when it worked.",
    "test": "A step in your journeys reads like a test (repeating a run to compare, or checking what the product "
            "leaves alone). Put that in a user_stories scenario and write the step as a person using the product.",
}


def journeys_missing(out: SpecDraftOut) -> str | None:
    """The reason code (`_JOURNEY_NOTES`) of the first thing wrong with a draft's journeys, or None."""
    if not out.journeys:
        return "none"
    if len(out.journeys) > 3:
        return "too_many"
    steps = [st for x in out.journeys for st in x.steps]
    if any(not 2 <= len(x.steps) <= 8 for x in out.journeys):
        return "steps"
    if any(not st.see.strip() for st in steps):
        return "see"
    if any(_TEST_WORDS.search(f"{st.do} {st.see}") for st in steps):
        return "test"
    return None


def journeys_problem(out: SpecDraftOut) -> str | None:
    """What the spec writer is told when its journeys are missing or not journeys, or None. The same draft says why on
    its spec card (`journeys_missing`), so the owner decides what to do with a spec that has none."""
    flaw = journeys_missing(out)
    return None if flaw is None else f"\n\n## Your last draft's journeys need work\n{_JOURNEY_NOTES[flaw]}"


@dataclass(frozen=True)
class _Brief:
    """Everything a draft is written from, gathered in one transaction."""
    prompt: str
    owner: list[owner_trace.OwnerSource]
    sources: dict[str, str]
    code: tuple[Path, str] | None
    product: ExistingProduct | None
    need: dict


def _brief(s, project_id: int, request_id: int, lang: Lang, *, redraft: Spec | None = None, added: str = "") -> _Brief:
    """`redraft` is the spec being written again, which is no earlier version of itself; `added` is what a redraft
    adds, read just before the ask."""
    p = s.get(Project, project_id)
    req = s.get(AgentRequest, request_id)
    report = s.get(Artifact, (p.context or {}).get("report_artifact_id") or 0)
    report_text = registry.read(report.path) if report else ""
    need = req.action_params.get("need") or {"title": req.title, "problem": req.summary}
    # Needs that arrived while an earlier version was in review sat on its card; this version takes them up.
    extra = [*(req.action_params.get("extra_needs") or []), *plans.extra_needs(s, req),
             *(n for sp in matters.product_specs(s, project_id) if sp.approval_request_id
               for n in (s.get(AgentRequest, sp.approval_request_id).action_params or {}).get("extra_needs", []))]
    owner_note = (req.response or {}).get("note") or ""
    pinned = matters.product_repo(s, project_id)
    product = existing_product.of_project(s, project_id)
    code = _code_to_read(s, project_id, product, pinned)
    earlier = [f"- {sp.spec_key} {sp.title} ({sp.status})" for sp in matters.product_specs(s, project_id)
               if sp is not redraft]
    existing_repos = sorted(d.name for d in settings().products_dir.iterdir()
                            if (d / ".git").exists() and not d.name.startswith(".")) \
        if settings().products_dir.exists() else []
    owner = owner_trace.owner_sources(s, p, lang)
    plan = plans.prompt_section(s, req)
    # A task an owner source names ("the 'I ordered it' agreed in T-1") is read whole, like one the request names.
    asked = "\n".join([ctx.as_json(need), owner_note, report_text, plan, *(x.text for x in owner)])
    repo_block = (f"## This product's repository\nproducts/{pinned} (already settled: put it in "
                  "suggested_target_repository as it is)"
                  + ("\nIt already holds the product the owner brought ('The product as it is today' above). "
                     "Specify changes to its current code: name the files and modules that change in "
                     "design_notes_markdown, keep its "
                     "structure, stack and conventions, and leave what works alone." if product else "")
                  if pinned else
                  f"## Existing product repositories (products/)\n"
                  f"{', '.join(existing_repos) or '(none: suggest a name for a new one)'}")
    prompt = "\n\n".join(x for x in [
        ctx.project_brief(s, p, reader="product"),
        "## Final report\n" + ctx.clip(report_text, 16000, "sources/report.md"),
        task_results(s, p, asked),
        f"## Approved build request {requests.display_id(req.id)}\n{ctx.as_json(need)}\nOwner's note: {owner_note}",
        plan,
        ("## Other build candidates for the same product\n" + ctx.as_json(extra)) if extra else "",
        ("## This product's earlier specs (do not put in again what they already built)\n" + "\n".join(earlier))
        if earlier else "",
        repo_block,
        _code_ask(*code) if code else "",
        owner_trace.prompt_section(owner),
        constitution.prompt_section(constitution.read(pinned) if pinned else None),
        added,
        SPEC_ASK,
    ] if x)
    return _Brief(prompt, owner, _sources(s, p, report_text), code, product, need)


def _written(b: _Brief, project_id: int, lang: Lang) -> SpecDraftOut:
    """The draft, asked once more when it has no P1 story or its journeys are not journeys, with every note that
    applies. After the second draft a spec with no P1 story fails; one whose journeys are still wrong is kept, and its
    card says why (`journeys_missing`)."""
    out = _draft(b.prompt, project_id, b.code)
    notes = ("" if _has_p1(out) else _NO_P1) + (journeys_problem(out) or "")
    if notes:
        again = _draft(b.prompt + notes, project_id, b.code)
        # Asked again for its journeys alone, a draft that loses its P1 story is worse than the first.
        out = again if _has_p1(again) or not _has_p1(out) else out
    if not _has_p1(out):
        raise RuntimeError(msg("build.error.no_p1", lang))
    return out


@dataclass(frozen=True)
class _Package:
    """One draft as the registry and the spec card take it."""
    files: dict[str, str]
    trace: list[dict]
    told: list[dict]
    plan: wireflow.Wireflow | None
    asks: list[dict]
    card: dict
    rules: dict
    journey_doc: dict


@dataclass(frozen=True)
class _Kept:
    """What a redraft keeps of the draft it answers. It asks the owner nothing new: the questions that draft asked are
    answered, so its rules stay as those answers left them and the answers are written into the new text."""
    of: dict             # {"request", "commit"}: the card the owner sent back, and the draft it showed
    rules: dict | None
    answers: list[dict]


def _package(base: str, key: str, repo: str, out: SpecDraftOut, b: _Brief, lang: Lang, *,
             kept: _Kept | None = None) -> _Package:
    told = stories.number(out.user_stories)
    journey_doc = {**journeys.number(out.about, out.journeys), "missing": journeys_missing(out)}
    plan = wireflow.plan(out.design_brief, stories.ordered(out.user_stories))
    # A note on a planned screen is held against the draft it was written on (owner_trace.settle).
    drawn = {c: wireflow.at(base, c) if c else None for c in {x.drawn_at for x in b.owner if x.screen}}
    trace = owner_trace.settle(b.owner, out.owner_trace, {r.id for r in out.requirements}, lang, changed={
        x.ref: wireflow.changed(drawn[x.drawn_at], plan) for x in b.owner if x.screen})
    ux = ux_criteria(out.design_brief, lang) if out.design_brief else []
    # The rules this spec is written under: the product's, or the first ones it proposes. Either way they bind
    # only once the owner approves the spec (on_spec_approved).
    rules = kept.rules if kept is not None and kept.rules is not None else constitution.read(repo)
    first_rules = rules is None
    if first_rules:
        rules = constitution.first(repo, out.constitution, b.owner, b.product is not None, lang)
    asks = [] if kept is not None else questions.drafted(
        out.clarifications, [] if first_rules else out.constitution_changes, rules, lang)
    reqs_yaml = yaml.safe_dump({"spec_id": key, "requirements": [r.model_dump() for r in out.requirements]},
                               allow_unicode=True, sort_keys=False)
    counts = {"reqs": len(out.requirements), "criteria": len(out.acceptance_criteria) + len(ux)}
    card = {"title": msg("build.spec_card.title", lang, title=out.title),
            "body": (msg("build.spec_card.body", lang, **counts, ux=len(ux)) if ux
                     else msg("build.spec_card.body_no_screen", lang, **counts)),
            "rationale": b.need.get("problem", ""), "trace": trace, **({"redraft_of": kept.of} if kept else {})}
    files = {
        f"{base}/prd.md": prd_markdown(key, out, told),
        f"{base}/requirements.yaml": reqs_yaml,
        f"{base}/acceptance-criteria.md": acceptance_markdown(key, out.acceptance_criteria, ux),
        f"{base}/design-notes.md": f"# {key} Design Notes\n\n{out.design_notes_markdown}",
        f"{base}/owner-trace.md": owner_trace.markdown(key, trace),
        f"{base}/{constitution.FILE}": constitution.markdown(rules, lang),
        f"{base}/{constitution.RULES}": constitution.dump(rules),
        **({f"{base}/{stories.FILE}": stories.dump(told)} if told else {}),
        **({f"{base}/{journeys.FILE}": journeys.dump(journey_doc)}
           if out.about or out.journeys or journey_doc["missing"] else {}),
        **({f"{base}/{questions.FILE}": questions.dump(asks, card)} if asks else {}),
        **({f"{base}/{DESIGN_BRIEF}": design_brief_markdown(key, out.design_brief)} if out.design_brief else {}),
        **({f"{base}/{wireflow.FILE}": wireflow.dump(plan)} if plan else {}),
        **{f"{base}/{rel}": text for rel, text in b.sources.items()},
    }
    if not asks:  # a marker with nothing to ask the owner says the point is open, never stays a marker
        files.update({f"{base}/{n}": text for n, text in questions.settled(
            {rel.removeprefix(f"{base}/"): text for rel, text in files.items() if rel.startswith(f"{base}/")},
            kept.answers if kept else [], lang).items()})
    return _Package(files, trace, told, plan, asks, card, rules, journey_doc)


def _handoff(spec: tuple[str, str, str], sha: str, repo: str, out: SpecDraftOut, b: _Brief,
             plan: wireflow.Wireflow | None) -> Handoff:
    """The handoff of a draft committed at `sha`; `spec` is its key, its project's slug and its registry path."""
    key, slug, base = spec
    return Handoff(
        spec_id=key, project=slug, title=out.title,
        source=HandoffSource(path=base, commit_sha=sha),
        target=HandoffTarget(repository=repo, test_command=out.suggested_test_command,
                             **({"base_branch": b.product.base_branch} if b.product else {}),
                             test_image=out.suggested_test_image, language=out.product_language),
        workflow=HandoffWorkflow(screens=out.design_brief is not None, planned_screens=plan is not None,
                                 existing_code=b.code is not None),
    )


def write_spec(project_id: int, request_id: int) -> int | None:
    """Handle an approved DEVELOPMENT request or product plan card: CPO drafts the spec package and asks for approval.
    A plan card's spec is written from the plan version that card approved and records it; when a revision of that
    plan sent its spec back, the spec is written again under its own key. A product has one spec at a time, so a
    request for a product whose spec is in review or being built drafts nothing and returns that spec. A later version
    keeps the product's repository and is told what the earlier ones built."""
    lang = app_settings.language()
    with session_scope() as s:
        if (settled := _settled(s, project_id, request_id, lang)) is not None:
            return settled or None
        b = _brief(s, project_id, request_id, lang, redraft=plans.stale_spec(s, s.get(AgentRequest, request_id)))
    out = _written(b, project_id, lang)

    with session_scope() as s:
        matters.lock(s, project_id)
        if (settled := _settled(s, project_id, request_id, lang)) is not None:  # another draft landed meanwhile
            return settled or None
        p = s.get(Project, project_id)
        req = s.get(AgentRequest, request_id)
        again, pin = plans.stale_spec(s, req), plans.pinned(s, req)
        key = again.spec_key if again else _spec_key(s, p)
        base = again.registry_path if again else f"projects/{p.slug}/specs/{key}"
        repo = (again.target_repository if again else None) or matters.product_repo(s, project_id) \
            or _repo_name(out.suggested_target_repository)
        pkg = _package(base, key, repo, out, b, lang)
        # A spec written again from a new plan takes out what this draft no longer has, its old questions too.
        stale = [f for f in registry.tree_at(base, again.draft_commit_sha) if f not in pkg.files and (
            f.removeprefix(f"{base}/") in (*_OPTIONAL, questions.FILE) or f.startswith(f"{base}/sources/"))] \
            if again else []
        sha = registry.write_files(pkg.files, f"[{key}] Draft spec: {out.title}", remove=stale)
        handoff = _handoff((key, p.slug, base), sha, repo, out, b, pkg.plan)
        # A spec with questions for the owner waits for the answers before its approval card (engineering/questions.py).
        status = SpecStatus.CLARIFICATION_REQUIRED if pkg.asks else SpecStatus.IN_REVIEW
        written = dict(title=out.title, status=status.value, draft_commit_sha=sha, target_repository=repo,
                       handoff=handoff.model_dump(mode="json"), request_id=request_id,
                       plan_id=pin[0].id if pin else None, plan_commit_sha=pin[1] if pin else None)
        if again is not None:
            plans.withdraw_spec_cards(s, again, lang)
            spec = again
            for field, value in written.items():
                setattr(spec, field, value)
            s.execute(update(Artifact).where(Artifact.path == f"{base}/prd.md").values(commit_sha=sha))
        else:
            spec = Spec(spec_key=key, project_id=p.id, registry_path=base, **written)
            s.add(spec)
            s.flush()
            s.add(Artifact(project_id=p.id, kind="prd", title=f"{key} {out.title}", path=f"{base}/prd.md",
                           commit_sha=sha))
        if pkg.asks:
            questions.ask(s, spec, pkg.asks, lang)
        else:
            card = _raise_card(s, spec, pkg.card, pkg.trace, pkg.told, pkg.journey_doc, [], pkg.rules, lang)
            _carry_waiting_needs(s, spec, card, lang)
        return spec.id


def _raise_card(s, spec: Spec, card: dict, trace: list[dict], told: list[dict], journey_doc: dict,
                clarified: list[dict], rules: dict | None, lang: Lang) -> AgentRequest:
    """The spec's approval card: what the spec did with the owner's asks leads, and the card carries the stories, the
    journeys (or why it has none), the product's rules the approval sets, and the owner's answers to its questions, which the console shows above the spec
    itself. Of the planned screens it carries only how many there are: the console keeps Approve off until the detail
    fetch has brought the sketches (wireflow.for_card). The card of a redraft names the card the owner sent back and
    the draft it showed, takes over what that card was holding for the next version, and that card says it was
    replaced."""
    approve = msg("build.option.approve", lang)
    planned = wireflow.at(spec.registry_path, spec.draft_commit_sha)
    of = card.get("redraft_of")
    new = _ask_approval(s, spec, AgentRequestDraft(
        type=RequestType.APPROVAL, title=card["title"], summary=f"{owner_trace.card_line(trace, lang)}\n{card['body']}",
        rationale=card["rationale"],
        options=[approve, msg("build.option.reject", lang), msg("build.option.defer", lang)],
        recommended_option=approve,
        action_kind="dev.spec_approve", estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM,
    ), {"owner_trace": trace, "stories": stories.card(told), "journeys": journeys.card(journey_doc),
        "journeys_missing": journey_doc.get("missing"), "rules": constitution.card(rules), "clarified": clarified,
        "planned": len(planned.screens) if planned else 0, **({"redraft_of": of} if of else {})})
    if of and (old := s.get(AgentRequest, of["request"])) is not None:
        old.response = {**(old.response or {}), "superseded_by": requests.display_id(new.id)}
        if waiting := (old.action_params or {}).get("extra_needs"):
            new.action_params = {**new.action_params, "extra_needs": waiting}
            new.rationale = old.rationale
    return new


def send_back(s, req: AgentRequest, resp: HumanResponse) -> list[dict]:
    """The owner declined a spec card with words on single screens. In the caller's transaction: the notes on screens
    the card's draft plans are returned as the caller records them ({"screen", "name", "note"}), and the spec goes
    back to DRAFT with no approval card, so it stays the product's spec while it is written again and no new ask
    drafts a rival. The caller queues the redraft. A note on a screen that draft does not plan, or on a card that no
    longer decides its spec, joins the owner's general note in `resp`, so no word of the owner's is lost."""
    params = req.action_params or {}
    spec = s.get(Spec, params.get("spec_id") or 0)
    live = (spec is not None and spec.approval_request_id == req.id and spec.request_id is not None
            and not spec.approved_commit_sha and spec.draft_commit_sha == params.get("commit_sha"))
    plan = wireflow.at(spec.registry_path, spec.draft_commit_sha) if live else None
    planned = plan.screens if plan else {}
    notes: dict[str, str] = {}
    for n in resp.screen_notes:
        if n.screen in planned:
            notes[n.screen] = "\n".join(x for x in (notes.get(n.screen), n.note) if x)
        else:
            resp.note = "\n".join(x for x in (resp.note, n.note) if x)
    resp.screen_notes = [ScreenNote(screen=sid, note=note) for sid, note in notes.items()]
    if not notes:
        return []
    spec.status, spec.approval_request_id = SpecStatus.DRAFT.value, None
    return [{"screen": sid, "name": planned[sid].name, "note": note} for sid, note in notes.items()]


def drop_notes(s, req: AgentRequest, lang: Lang) -> AgentRequest:
    """The owner gives up on a spec sent back with notes on its screens: the card decides its spec again as a plain
    decline, so the next ask drafts a new spec, which reads what waited for the next version. In the caller's
    transaction. Run again it changes nothing. Refused for a card that was not sent back, one whose spec moved on, and
    while a redraft from its notes is in flight."""
    matters.lock(s, req.project_id)
    s.refresh(req, with_for_update=True)
    if (req.response or {}).get("notes_dropped"):
        return req
    params = req.action_params or {}
    spec = s.scalar(select(Spec).where(Spec.id == (params.get("spec_id") or 0)).with_for_update()
                    .execution_options(populate_existing=True))
    if (req.action_kind != "dev.spec_approve" or req.status != RequestStatus.REJECTED.value
            or not (req.response or {}).get("screen_notes") or spec is None):
        raise requests.ResponseRejected(said("build.notes.refuse_not_sent_back"))
    if (req.response.get("superseded_by") or not wireflow.sent_back(spec)
            or spec.draft_commit_sha != params["commit_sha"]):
        raise requests.ResponseRejected(said("build.notes.refuse_moved_on"))
    if wireflow.redraft_jobs(s, spec)[1]:
        raise requests.ResponseRejected(said("build.notes.refuse_writing"))
    spec.approval_request_id, spec.status = req.id, SpecStatus.IN_REVIEW.value
    _carry_waiting_needs(s, spec, req, lang)
    req.response = {**req.response, "notes_dropped": True}
    # The notes were owner sources for the next draft to account for; dropped, they are no longer asked of it.
    for job in s.scalars(select(Job).where(Job.kind == wireflow.REDRAFT, Job.payload["spec_id"].as_integer() == spec.id,
                                           Job.status == "failed", Job.alert_dismissed_at.is_(None))):
        job.alert_dismissed_at = datetime.now(UTC)  # Home would offer a retry that can rewrite nothing now
    mine = requests.display_id(req.id)
    p = s.get(Project, req.project_id, with_for_update=True, populate_existing=True)
    p.context = {**(p.context or {}), "owner_answers": [
        {k: v for k, v in a.items() if k not in ("screens", "commit")} if a.get("request") == mine else a
        for a in (p.context or {}).get("owner_answers", [])]}
    activity.record(s, verb="spec_notes_dropped", entity="request", entity_id=req.id, project_id=req.project_id,
                    actor="owner", summary=msg("build.notes.dropped", lang), data={"request": mine, "spec": spec.id})
    return req


def _last_draft(spec: Spec) -> str:
    """The spec's prd and requirements as its draft commit holds them, for the writer of its redraft."""
    def read(name: str) -> str:
        return registry.read_at(f"{spec.registry_path}/{name}", spec.draft_commit_sha)
    return ("## Your last draft of this spec\n"
            "Ask the owner nothing new in this draft: leave clarifications and constitution_changes empty, and write "
            "what is still open as an assumption.\n"
            + fenced("prd.md", ctx.clip(read("prd.md"), 12000, "prd.md")) + "\n"
            + fenced("requirements.yaml", ctx.clip(read("requirements.yaml"), 6000, "requirements.yaml")))


def redraft_spec(spec_id: int, request_id: int) -> int:
    """The same spec written again from everything its draft had plus the owner's notes on its screens: the same key
    and path, a new commit, and a new approval card that names the card the owner sent back. A spec that is no longer
    waiting on that card's notes was written again already, so a second run does nothing. When it fails for good the
    spec stays sent back with no job, which is what the declined card and the build board read as failed."""
    lang = app_settings.language()

    def waiting(s) -> tuple[Spec, AgentRequest] | None:
        spec, old = s.get(Spec, spec_id), s.get(AgentRequest, request_id)
        ok = (wireflow.sent_back(spec) and old.status == RequestStatus.REJECTED.value
              and spec.draft_commit_sha == old.action_params["commit_sha"])
        return (spec, old) if ok else None

    with session_scope() as s:
        if (found := waiting(s)) is None:
            return spec_id
        spec, old = found
        project_id, ref = spec.project_id, requests.display_id(old.id)
        notes = [{**n, "ref": wireflow.note_ref(ref, n["screen"])} for n in old.response["screen_notes"]]
        prev = wireflow.at(spec.registry_path, spec.draft_commit_sha)
        b = _brief(s, project_id, spec.request_id, lang, redraft=spec,
                   added=_last_draft(spec) + "\n\n" + wireflow.redraft_section(prev, notes))
    out = _written(b, project_id, lang)

    with session_scope() as s:
        matters.lock(s, project_id)
        if (found := waiting(s)) is None:
            return spec_id
        spec, old = found
        key, base, was = spec.spec_key, spec.registry_path, spec.draft_commit_sha
        try:
            answers = (yaml.safe_load(registry.read_at(f"{base}/{questions.FILE}", was)) or {}).get("answers") or []
        except RuntimeError:  # the draft asked no question
            answers = []
        pkg = _package(base, key, spec.target_repository, out, b, lang, kept=_Kept(
            {"request": old.id, "commit": was}, constitution.in_spec(base, was), answers))
        stale = [f for f in registry.tree_at(base, was) if f not in pkg.files
                 and (f.removeprefix(f"{base}/") in _OPTIONAL or f.startswith(f"{base}/sources/"))]
        sha = registry.write_files(pkg.files, f"[{key}] Redraft spec from the owner's notes ({ref})", remove=stale)
        s.execute(update(Artifact).where(Artifact.path == f"{base}/prd.md").values(commit_sha=sha))
        slug = s.get(Project, project_id).slug
        spec.handoff = _handoff((key, slug, base), sha, spec.target_repository, out, b, pkg.plan).model_dump(mode="json")
        spec.title, spec.draft_commit_sha, spec.status = out.title, sha, SpecStatus.IN_REVIEW.value
        card = _raise_card(s, spec, pkg.card, pkg.trace, pkg.told, pkg.journey_doc,
                           (old.action_params or {}).get("clarified") or [], pkg.rules, lang)
        _carry_waiting_needs(s, spec, card, lang)
        owner_trace.restate(s, project_id, lang)
        return spec_id


def on_questions_answered(spec_id: int) -> None:
    """A question card about a spec closed. Once none is open, write the owner's answers into the spec (each marker
    where the spec writer put it, and the Clarifications section), write the constitution changes the owner chose into
    the spec's rules, and raise the spec card on that commit. The product's rules change only once the owner approves
    the spec. Run again, it finds the card raised and does nothing."""
    lang = app_settings.language()
    with session_scope() as s:
        spec = s.get(Spec, spec_id)
        matters.lock(s, spec.project_id)
        s.refresh(spec)
        if (spec.status != SpecStatus.CLARIFICATION_REQUIRED.value or spec.approved_commit_sha
                or spec.approval_request_id):
            return
        cards = questions.cards(s, spec)
        if any(c.status == RequestStatus.PENDING.value for c in cards.values()):
            return
        base, sha = spec.registry_path, spec.draft_commit_sha
        record = yaml.safe_load(registry.read_at(f"{base}/{questions.FILE}", sha))
        answers = [questions.answered(q, cards[q["id"]], lang) for q in record["questions"]]
        files = questions.written_back(base, sha, answers, lang)
        rules = constitution.in_spec(base, sha)  # as the draft wrote them, so a run again changes them once
        if rules is not None and (made := [a["change"] for a in answers if a.get("change")]):
            for change in made:
                rules = constitution.amended(rules, change, lang)
            files[f"{base}/{constitution.RULES}"] = constitution.dump(rules)
            files[f"{base}/{constitution.FILE}"] = constitution.markdown(rules, lang)
        # The answers are the owner's word too: the spec now holds them.
        owner = owner_trace.owner_sources(s, s.get(Project, spec.project_id), lang)
        trace = [*record["card"]["trace"],
                 *owner_trace.written_in(owner, {requests.display_id(c.id) for c in cards.values()})]
        files[f"{base}/owner-trace.md"] = owner_trace.markdown(spec.spec_key, trace)
        files[f"{base}/{questions.FILE}"] = questions.dump(record["questions"], record["card"], answers)
        new = registry.write_files(files, f"[{spec.spec_key}] Owner answers")
        s.execute(update(Artifact).where(Artifact.path == f"{base}/prd.md").values(commit_sha=new))
        h = Handoff.model_validate(spec.handoff)
        h.source.commit_sha = new  # the engineer, the reviewer and the acceptance read the spec with the answers in
        spec.handoff, spec.draft_commit_sha = h.model_dump(mode="json"), new
        spec.status = SpecStatus.IN_REVIEW.value
        card = _raise_card(s, spec, record["card"], trace, stories.at(base, new), journeys.at(base, new),
                           questions.card_rows(answers), rules, lang)
        _carry_waiting_needs(s, spec, card, lang)
        owner_trace.restate(s, spec.project_id, lang)


def _carry_waiting_needs(s, spec: Spec, card: AgentRequest, lang: Lang) -> None:
    """Development asks that arrived while the spec had no card (its first draft, its questions, its redraft) waited on
    the spec's request (`matters._product_placement`): the card takes them as it would have taken them open, for the
    next version. They move, so no later card of the spec takes them a second time."""
    source = s.get(AgentRequest, spec.request_id) if spec.request_id else None
    if source is None or requests.WAITING_NEEDS not in (source.action_params or {}):
        return
    # Before needs moved they were copied, so a request can still hold needs its spec's card already took.
    has = card.action_params.get("extra_needs", [])
    waiting = [n for n in source.action_params[requests.WAITING_NEEDS] if n not in has]
    source.action_params = {k: v for k, v in source.action_params.items() if k != requests.WAITING_NEEDS}
    if not waiting:
        return
    label = msg("request.note.next_version", lang)
    card.action_params = {**card.action_params, "extra_needs": [*card.action_params.get("extra_needs", []), *waiting]}
    card.rationale = "\n\n".join([card.rationale, *(
        f"[{label} · {glossary.role(n['by'], lang)}] {n['title']}: {n['problem']}" for n in waiting)])


def prd_markdown(key: str, out: SpecDraftOut, told: list[dict]) -> str:
    """prd.md: the language line, the user stories with the owner's data, the PRD, and the guesses made where the
    owner did not say."""
    parts = [f"# {key} {out.title}", _language_line(out.product_language), stories.markdown(told, out.owner_data),
             out.prd_markdown,
             ("## Assumptions\n" + "\n".join(f"- {a}" for a in out.assumptions)) if out.assumptions else ""]
    return "\n\n".join(x.strip() for x in parts if x.strip()) + "\n"


def _language_line(language: str | None) -> str:
    """The prd's first line for the engineer: the language the product speaks to its users (ui-standard.md, 5)."""
    return f"Product language: {language} (its screens, copy, README and messages)\n\n" if language else ""


DESIGN_BRIEF = "design-brief.md"  # in a spec: the product has a screen people use
# What a draft writes only when it has it: a redraft without one takes the last draft's out of the spec.
_OPTIONAL = (stories.FILE, journeys.FILE, DESIGN_BRIEF, wireflow.FILE)


def ux_criteria(b: DesignBriefOut, lang: Lang) -> list[str]:
    """The design brief's promises as acceptance criteria, so a spec with a screen is never accepted on data rules
    alone: the core task within its steps, what the first glance shows, and every state it named. The owner reads
    them beside the CPO's own criteria, so they are in the owner's language."""
    return [msg("build.ux.core_task", lang, task=b.primary_task, steps=b.primary_task_steps),
            msg("build.ux.first_glance", lang, order=b.first_glance),
            *(msg("build.ux.state", lang, screen=x.screen, state=x.state, shows=x.shows) for x in b.states)]


def acceptance_markdown(key: str, criteria: list[str], ux: list[str]) -> str:
    out = [f"# {key} Acceptance Criteria", "", *(f"- [ ] {c}" for c in criteria)]
    if ux:
        out += ["", f"## User experience (from {DESIGN_BRIEF}; judged on the screens)", "", *(f"- [ ] {c}" for c in ux)]
    return "\n".join(out)


def design_brief_markdown(key: str, b: DesignBriefOut) -> str:
    rows = "\n".join(f"| {sid or ''} | {x.screen} | {x.state} | {x.shows} |"
                     for x, sid in zip(b.states, wireflow.ids(b.states)))
    return (f"# {key} Screen design brief\n\nThe engineering team designs the screens from this and "
            "`.ship/ui-standard.md`.\n\n"
            f"## Users and their moment\n{b.users_and_context}\n\n"
            f"## Core task (at most {b.primary_task_steps} clicks or entries after opening the app)\n{b.primary_task}\n\n"
            f"## Feel\n{b.tone}\n\n## First-glance order\n{b.first_glance}\n\n"
            f"## Screens by state\nThe id is the screen's name in `{wireflow.FILE}` and in its capture's file name.\n\n"
            f"| Id | Screen | State | What the user sees |\n|---|---|---|---|\n{rows}\n")


_TASK_REF = re.compile(r"(?<![A-Za-z0-9])T-(\d+)(?!\d)")  # not \b: a Korean particle may follow the id
# How much of a task's findings the CPO reads in the prompt: whole for the tasks the request or report names, the
# summary alone for the rest. Every cut says so and names the file that holds the whole.
CITED_TASK_CHARS = 8000


def _sources(s, p: Project, report_text: str) -> dict[str, str]:
    """The full report and every task result, committed beside the spec: what the CPO reads in part, the engineer
    and the code reviewer read whole at `.ship/spec/sources/`."""
    files = {"sources/report.md": report_text} if report_text else {}
    for t in s.scalars(select(Task).where(Task.project_id == p.id, Task.result.is_not(None)).order_by(Task.id)):
        r = t.result
        files[f"sources/{display_id(t.id)}.md"] = (f"# {display_id(t.id)} {t.title}\n\nOwner: {t.owner} · Status: "
                                                   f"{t.status}\n\n## Summary\n{r.get('summary', '')}\n\n"
                                                   f"{r.get('findings_markdown') or ''}")
    return files


def task_results(s, p: Project, asked: str) -> str:
    cited = {int(n) for n in _TASK_REF.findall(asked)}
    out = ["## Task results (the full text for the tasks the request or report names, a summary for the rest; "
           "each whole is in sources/T-n.md)"]
    for t in s.scalars(select(Task).where(Task.project_id == p.id, Task.result.is_not(None)).order_by(Task.id)):
        where = f"sources/{display_id(t.id)}.md"
        out.append(f"### {display_id(t.id)} [{t.status}] {t.title} ({t.owner})\n**Summary**: {t.result.get('summary', '')}")
        if t.id in cited:
            out.append(ctx.clip(t.result.get("findings_markdown"), CITED_TASK_CHARS, where))
        elif t.result.get("findings_markdown"):
            out.append(f"(the full text is in {where})")
    return "\n\n".join(out) if len(out) > 1 else ""


def _settled(s, project_id: int, request_id: int, lang: Lang) -> int | None:
    """What writing a spec for this request ends with before anything is drafted: the spec it already wrote; nothing
    (0) when the plan it approved has moved on, or a plan under way took the ask; the product's live spec; or None,
    to draft. A spec its plan's revision sent back is drafted again, whatever is live."""
    if done := s.scalar(select(Spec.id).where(Spec.request_id == request_id)):
        return done
    req = s.get(AgentRequest, request_id)
    if plans.moved_on(s, req) or plans.took(s, req, lang) is not None:
        return 0
    if plans.stale_spec(s, req) is not None:
        return None
    return _keep_live(s, project_id, request_id, lang)


def _keep_live(s, project_id: int, request_id: int, lang: Lang) -> int | None:
    """The product's current spec, when it has one, noted against the request that asked for another."""
    live = matters.live_spec(s, project_id)
    if live is None:
        return None
    card = s.get(AgentRequest, live.approval_request_id) if live.approval_request_id else None
    if card is not None and card.status == RequestStatus.CANCELLED.value:
        reask_approval(s, card)  # withdrawn before the owner decided: the spec the request is kept on needs its card
    activity.record(s, verb="spec_kept", entity="spec", entity_id=live.id, project_id=project_id, actor="product",
                    data={"ref": live.spec_key, "request": requests.display_id(request_id)},
                    summary=msg("build.spec_kept", lang, request=requests.display_id(request_id), key=live.spec_key))
    return live.id


# What a spec card carries beside the spec: what it did with the owner's asks (owner_trace.settle), its user stories and
# journeys, the owner's answers to its questions and how many screens it plans. A card asked again carries them over.
_CARRIED = ("owner_trace", "stories", "journeys", "journeys_missing", "rules", "clarified", "planned", "redraft_of")


def _ask_approval(s, spec: Spec, draft: AgentRequestDraft, shown: dict | None = None) -> AgentRequest:
    """The one card that decides the spec's build. It never merges into another card: approving it pins this spec."""
    approval = requests.create(s, draft, from_agent="product", project_id=spec.project_id,
                               origin=ServerOrigin(OriginKind.SPEC_APPROVAL, spec.id),
                               action_params={"spec_id": spec.id, "commit_sha": spec.draft_commit_sha,
                                              # the plan version this draft follows, so the card links that one
                                              **({"plan_commit_sha": spec.plan_commit_sha} if spec.plan_commit_sha
                                                 else {}),
                                              **{k: v for k, v in (shown or {}).items() if k in _CARRIED}},
                               system=True, dedupe=False)
    spec.approval_request_id = approval.id
    return approval


def card_spec(s, req: AgentRequest) -> dict | None:
    """The spec a spec card asks the owner to approve, as the card's commit holds it, so the owner reads on the card
    exactly what approving pins. None for any other card."""
    params = req.action_params or {}
    spec = s.get(Spec, params.get("spec_id") or 0) if req.action_kind == "dev.spec_approve" else None
    if spec is None or not params.get("commit_sha"):
        return None
    try:
        text = registry.read_at(f"{spec.registry_path}/prd.md", params["commit_sha"])
    except RuntimeError:  # a registry restored without that commit: the card still opens, without the text
        return None
    return {"text": for_owner(text, spec.spec_key)}


# A task's result as the engineers read it beside the spec ("the format in sources/T-103.md").
_SOURCE_FILE = re.compile(r"`?(?:\.ship/spec/)?sources/(T-\d+)\.md`?")


def for_owner(text: str, key: str) -> str:
    """The spec as the owner reads it on the card: without the engineers' key for it, and a task's result named as the
    task, which the console links by its title."""
    return _SOURCE_FILE.sub(r"\1", re.sub(rf"(?<![\w-]){re.escape(key)}(?![\w-])[ \t]*", "", text))


def reask_approval(s, old: AgentRequest) -> AgentRequest:
    """Ask again about a spec whose card the owner deferred or let expire: the same spec and commit on a new card, and
    the old card closed as replaced."""
    spec = s.get(Spec, old.action_params["spec_id"])
    again = _ask_approval(s, spec, AgentRequestDraft(
        type=RequestType.APPROVAL, title=old.title, summary=old.summary, rationale=old.rationale,
        options=old.options or [], recommended_option=old.recommended_option, action_kind="dev.spec_approve",
        estimated_cost_usd=None, risk_level=RiskLevel(old.risk_level)),
        old.action_params or {})
    old.response = {**(old.response or {}), "superseded_by": requests.display_id(again.id)}
    old.status = RequestStatus.CANCELLED.value
    return again


def add_clarification(spec: Spec, who: str, text: str) -> None:
    """Add to the spec's clarifications.md, which the engineers read beside the approved spec. The owner's note on
    approving 반품 예측's spec ("일곱 항목으로 갑니다") reached no one, and the engineer wrote "clarifications.md는
    없어요". Adding the same text again changes nothing."""
    path = f"{spec.registry_path}/clarifications.md"
    prev = registry.read(path) if (registry.root() / path).exists() else f"# {spec.spec_key} Clarifications\n"
    block = f"\n## {who}\n{text.strip()}\n"
    if block not in prev:
        registry.write_files({path: prev + block}, f"[{spec.spec_key}] Clarification: {who}")


def on_spec_approved(spec_id: int, request_id: int) -> int | None:
    """Pin the approved commit, write the approved handoff.yaml, prepare the repo, queue the dev job."""
    with session_scope() as s:
        spec = s.get(Spec, spec_id)
        req = s.get(AgentRequest, request_id)
        if req.status != RequestStatus.APPROVED.value or req.action_params.get("spec_id") != spec_id:
            raise PermissionError(f"spec {spec.spec_key}: approval {requests.display_id(request_id)} not valid")
        if req.action_params.get("commit_sha") != spec.draft_commit_sha:
            raise PermissionError(f"spec {spec.spec_key}: approved commit differs from current draft")
        plan = s.get(Plan, spec.plan_id) if spec.plan_id else None
        if plan is not None and (plan.status != PlanStatus.APPROVED.value
                                 or plan.approved_commit_sha != spec.plan_commit_sha):
            # Not an error to retry: the plan moved on, and its next approval writes the spec again.
            log.warning("spec %s: approval %s not built, its plan %s is no longer approved at %s", spec.spec_key,
                        requests.display_id(req.id), plan.plan_key, spec.plan_commit_sha)
            return None
        requests.consume_grant(s, "dev.spec_approve", spec.project_id, request_id=req.id)
        h = Handoff.model_validate(spec.handoff)
        h.approval = HandoffApproval(spec_approved=True, implementation_authorized=True,
                                     deployment_requires_human=True, approval_request_id=requests.display_id(req.id))
        files = {f"{spec.registry_path}/handoff.yaml": yaml.safe_dump(h.model_dump(mode="json"), allow_unicode=True,
                                                                       sort_keys=False)}
        # The approval sets the rules the card showed: the first ones this spec proposed, or the changes the owner chose.
        if (rules := constitution.approved(spec.target_repository,
                                           constitution.in_spec(spec.registry_path, spec.draft_commit_sha),
                                           questions.made(spec.registry_path, spec.draft_commit_sha),
                                           app_settings.language())) is not None:
            files[constitution.path(spec.target_repository)] = constitution.dump(rules)
        registry.write_files(files, f"[{spec.spec_key}] Approve spec ({requests.display_id(req.id)})")
        if note := (req.response or {}).get("note"):
            add_clarification(spec, f"Owner, approving the spec ({requests.display_id(req.id)})", note)
        spec.handoff = h.model_dump(mode="json")
        spec.approved_commit_sha = spec.draft_commit_sha
        spec.status = SpecStatus.READY_FOR_DEV.value
        ensure_product_repo(h.target.repository, h.target.base_branch, spec.title)
        job = DevJob(spec_id=spec.id, status="QUEUED", attempt=1)
        s.add(job)
        s.flush()
        queue.enqueue(s, "dev_job", {"dev_job_id": job.id}, idempotency_key=f"dev_job:{job.id}", max_attempts=1,
                      approved=True)
        return job.id


def retry_dev(spec_key: str, lang: Lang) -> int:
    """New attempt (fresh branch/worktree) for an approved spec whose last dev job failed, or stalled: still in
    an active status while no worker job is in flight for it, and none delivered. Lapsed leases are settled first,
    in a transaction of their own, so a job whose worker stopped answering is refused with the time its hold ends,
    the same time on every click; a job a worker still runs is refused too. A stalled job is closed as FAILED
    first, since its worktree may be half-written. A failed attempt whose PR the owner merged on the forge, as
    new_attempt's refusal offers, is recorded MERGED next, so the spec gets no new attempt. The decision is taken
    under the claim lock."""
    from justshipit.engineering.worker import _record_if_merged
    queue.settle_lapsed()
    with session_scope() as s:
        spec = s.scalar(select(Spec).where(Spec.spec_key == spec_key))
        if spec is None or not spec.approved_commit_sha:
            raise ValueError(msg("build.retry.no_spec", lang, key=spec_key))
        _close_stalled(s, spec, lang)
        failed = list(s.scalars(select(DevJob.id).where(DevJob.spec_id == spec.id,
                                                        DevJob.status == DevJobStatus.FAILED.value)))
    for job_id in failed:
        _record_if_merged(job_id)
    with session_scope() as s:
        spec = s.scalar(select(Spec).where(Spec.spec_key == spec_key))
        _close_stalled(s, spec, lang)
        return new_attempt(s, spec, lang)


def _close_stalled(s, spec: Spec, lang: Lang) -> None:
    """Under the claim lock: refuse while a worker job is in flight for the spec's last dev job, and close it as
    FAILED when it sits in an active status with none."""
    queue.reclaim(s, {})
    last = s.scalar(select(DevJob).where(DevJob.spec_id == spec.id).order_by(DevJob.attempt.desc()))
    if last and (why := queue.why_busy(s, f"dev_job:{last.id}", queue.DEV_KINDS, lang=lang)):
        raise ValueError(msg("build.retry.busy", lang, key=spec.spec_key, id=last.id, status=last.status, why=why))
    if last and last.status in queue.DEV_ACTIVE:
        from justshipit.engineering import revise
        if revise.stopped(s, last, msg("build.fix.interrupted", lang)):
            return  # a fix round's job is delivered again, never failed
        last.status = DevJobStatus.FAILED.value
        last.error = msg("build.error.stalled", lang, id=last.id)


def pushed_pr(job: DevJob) -> str | None:
    """The PR, or the pushed branch, the job left on the forge."""
    result = job.result or {}
    return job.pr_url or result.get("pr_url") or (result.get("branch") if result.get("pushed") else None)


def new_attempt(s, spec: Spec, lang: Lang) -> int:
    """Queue the spec's next attempt, a fresh branch from its approved commit, when its last one failed or the
    owner threw its result away, and no attempt holds a delivered branch. Only the owner's buttons get here (retry
    build, discard), so the attempt runs whatever stage is open."""
    delivered = s.scalar(select(DevJob).where(DevJob.spec_id == spec.id,
                                              DevJob.status.in_([st.value for st in DEV_JOB_DELIVERED])))
    if delivered:
        raise ValueError(msg("build.retry.delivered", lang, key=spec.spec_key, id=delivered.id, status=delivered.status))
    last = s.scalar(select(DevJob).where(DevJob.spec_id == spec.id).order_by(DevJob.attempt.desc()))
    if last and last.status not in (DevJobStatus.FAILED.value, DevJobStatus.DISCARDED.value):
        raise ValueError(msg("build.retry.not_failed", lang, key=spec.spec_key, id=last.id, status=last.status))
    attempt = (last.attempt + 1) if last else 1
    h = Handoff.model_validate(spec.handoff)
    if attempt > h.limits.max_iterations:
        raise ValueError(msg("build.retry.used_up", lang, key=spec.spec_key))
    # A job marked FAILED can still have pushed and opened its PR: a run that went on after the queue reclaimed it,
    # or a gh pr create that ran out of time after the forge made the PR. Only the forge knows whether it is open.
    from justshipit.engineering.worker import branch_prs
    repo = settings().products_dir / h.target.repository
    for job in s.scalars(select(DevJob).where(DevJob.spec_id == spec.id, DevJob.status != DevJobStatus.DISCARDED.value)):
        if not pushed_pr(job):
            continue
        open_prs = branch_prs(repo, job.branch, "open")
        if open_prs is None:
            raise ValueError(msg("build.retry.forge_silent", lang, key=spec.spec_key, id=job.id, status=job.status,
                                 branch=job.branch))
        if open_prs:
            raise ValueError(msg("build.retry.pr_open", lang, key=spec.spec_key, id=job.id, status=job.status,
                                 url=open_prs[0]))
    job = DevJob(spec_id=spec.id, status="QUEUED", attempt=attempt)
    s.add(job)
    s.flush()
    spec.status = SpecStatus.READY_FOR_DEV.value
    queue.enqueue(s, "dev_job", {"dev_job_id": job.id}, idempotency_key=f"dev_job:{job.id}", max_attempts=1,
                  by_owner=True)
    return job.id


def ensure_product_repo(name: str, base_branch: str, title: str) -> None:
    repo = settings().products_dir / name
    if (repo / ".git").exists():
        return
    if repo.exists() and any(repo.iterdir()):
        raise RuntimeError(f"products/{name} exists but is not a git repository")
    repo.mkdir(parents=True, exist_ok=True)

    def git(*a: str) -> None:
        out = hostgit.run(repo, *a)
        if out.returncode != 0:
            raise RuntimeError(f"git {' '.join(a)}: {out.stderr.strip()}")

    git("init", "-b", base_branch)
    (repo / "README.md").write_text(f"# {name}\n\n{title}\n\nManaged by the AI Company engineering team.\n",
                                    encoding="utf-8")
    (repo / ".gitignore").write_text(".ship/\n", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.name=AI Company", "-c", "user.email=ai@ship.local", "commit", "-m", "Initialize repository")
