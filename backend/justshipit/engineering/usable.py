"""After a merge: merged is not usable. A merged build lives only in products/<repo> until the owner has a way to
open it, so the merge is followed by one decision, how people will use it, and the project's final report learns what
the merged code really does.

The facts come from the acceptance check, which read the delivered branch's own files (`AcceptanceOut.use`, `.built`):
the merge lands exactly that branch. Where the product is used is the owner's call and the owner's hands: picking a way
makes a to-do with the steps, nothing is deployed by the company, and keys the product needs are its own settings,
never Just Ship It's.

`state` is derived, never stored: the decision's status and its to-do's say where the build stands.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from justshipit.contracts import owner_text
from justshipit.contracts.handoff import AcceptanceOut, DevJobStatus, Handoff, UseOut
from justshipit.contracts.origins import OriginKind, ServerOrigin
from justshipit.contracts.requests import (
    AgentRequestDraft,
    CardOption,
    DecisionCard,
    HumanDecision,
    RequestStatus,
    RequestType,
    RiskLevel,
)
from justshipit.db.models import AgentRequest, Artifact, DevJob, OwnerTask, Project, Spec
from justshipit.db.session import session_scope
from justshipit.engineering import try_it
from justshipit.engineering.status import at
from justshipit.i18n import LANGS, Lang, msg
from justshipit.services import app_settings, registry, requests
from justshipit.services.owner_tasks import DONE, OPEN


def after_merge(job_id: int) -> None:
    """The queue's job after a merge (worker._mark_merged). Run again, it changes nothing twice. The decision is
    committed before the report is touched, so a registry that fails never costs the owner the decision."""
    lang = app_settings.language()
    with session_scope() as s:
        job = s.get(DevJob, job_id)
        if job is None or job.status != DevJobStatus.MERGED.value:
            return
        project_id = s.get(Spec, job.spec_id).project_id
        if "use_request" not in (job.result or {}):
            _file(s, job, lang)
    with session_scope() as s:
        update_report(s, project_id, lang)


# The states the owner can ask the decision again from.
AGAIN = ("later", "closed")


def ask_again(job_id: int, lang: Lang) -> int:
    """Owner action on a merged build whose decision was put off or closed: the same facts come to Decisions again.
    Returns the new request's id."""
    with session_scope() as s:
        job = s.get(DevJob, job_id)
        now = state(s, job) if job is not None else None
        req = _file(s, job, lang) if now is not None and now["state"] in AGAIN else None
        if req is None:
            raise ValueError(msg("build.use.refuse_again", lang, id=job_id))
        return req.id


def _file(s, job: DevJob, lang: Lang) -> AgentRequest | None:
    """The decision, or None for a build that is nothing people open (a library, a command-line tool): that is
    recorded as `use_request` None, and its end state says how to use it (`state`, "tool")."""
    acceptance = _acceptance(job)
    if acceptance is None:
        return None
    req = _ask(s, job, s.get(Spec, job.spec_id), acceptance.use, lang) if acceptance.use is not None else None
    job.result = {**(job.result or {}), "use_request": req.id if req is not None else None}
    s.flush()
    return req


def _acceptance(job: DevJob) -> AcceptanceOut | None:
    raw = (job.result or {}).get("acceptance")
    return AcceptanceOut.model_validate(raw) if raw else None


def _name(spec: Spec) -> str:
    return Handoff.model_validate(spec.handoff).target.repository


def _ask(s, job: DevJob, spec: Spec, use: UseOut, lang: Lang) -> AgentRequest | None:
    """The one decision after a merge. None when the build is nothing people open: no place to host it, no address to
    open and no screens (a library, a command-line tool). The address is empty when the files set no port, so a build
    with screens and no address still gets the decision."""
    if not use.hosting and not use.local_address and not Handoff.model_validate(spec.handoff).workflow.screens:
        return None
    name = _name(spec)
    needs = _needs(use, lang)
    ways: list[tuple[CardOption, str]] = [
        (CardOption(label=msg("build.use.option.host", lang, host=h.host), verdict=HumanDecision.ANSWER,
                    summary=msg("build.use.option.host_summary", lang, cost=h.cost), evidence_ids=[], risks=[],
                    recommended=False),
         "\n".join([msg("build.use.host_heading", lang, host=h.host, cost=h.cost), msg("build.use.from_files", lang),
                    *(f"{i}. {step}" for i, step in enumerate(h.steps, 1))]))
        for h in use.hosting[:2]]
    local = None
    # Others must open it (the spec and the prepared messages promise them a link): on the owner's computer they can't.
    who = _said(use.reach)
    conflict = msg("build.use.local_conflict", lang, who=who) if who else None
    if use.local_steps:
        said = msg("build.use.option.local_summary", lang, address=use.local_address or msg("build.use.no_address", lang))
        local = CardOption(label=msg("build.use.option.local", lang), verdict=HumanDecision.ANSWER, evidence_ids=[],
                           risks=[], recommended=False, summary=f"{conflict} {said}" if conflict else said)
        ways.append((local, "\n".join([msg("build.use.local_heading", lang, name=name), msg("build.use.from_files", lang),
                                       *(f"{i}. `{c}`" for i, c in enumerate(use.local_steps, 1)),
                                       *([msg("build.use.open", lang, address=use.local_address)]
                                         if use.local_address else [])])))
    if not ways:
        return None
    recommend = "host" if use.reach and use.hosting else use.recommend
    pick = next((o for o, _ in ways if (o is local) == (recommend == "local")), ways[0][0])
    pick.recommended = True
    later = CardOption(label=msg("build.use.option.later", lang), verdict=HumanDecision.DEFER, evidence_ids=[],
                       risks=[], summary=msg("build.use.option.later_summary", lang), recommended=False)
    # Named by the build the owner approved: `name` is the product's folder, which the owner never named.
    title = owner_text.title(spec.title)
    card = DecisionCard(headline=msg("build.use.headline", lang, title=title),
                        owner_does=msg("build.use.owner_does", lang), why=msg("build.use.why", lang, name=name),
                        metrics=[], options=[*(o for o, _ in ways), later], recommendation=use.why, reversible=True)
    summary = "\n\n".join([msg("build.use.lead", lang, name=name), *([needs] if needs else []), *(w for _, w in ways)])
    req = requests.create(s, AgentRequestDraft(
        type=RequestType.QUESTION, title=msg("build.use.title", lang, title=title), summary=summary,
        rationale=msg("build.use.rationale", lang), options=[], recommended_option=None,
        action_kind=requests.USE_CARD_KIND, estimated_cost_usd=None, risk_level=RiskLevel.MEDIUM,
        card=card,
    ), from_agent="product", project_id=spec.project_id, origin=ServerOrigin(OriginKind.USE_OF_BUILD, job.id),
        system=True, dedupe=False, action_params={
        "use_of_job": job.id, "local_option": local.label if local else None,
        # What the to-do the owner's pick makes says: that way's steps in full, with what the product needs.
        "use_steps": {o.label: "\n\n".join([*([conflict] if conflict and o is local else []),
                                             *([needs] if needs else []), w]) for o, w in ways}})
    return req


_PROMISED_MAX, _PROMISED_CHARS = 8, 1500


def promised(s, project_id: int, *, most: int = _PROMISED_MAX, chars: int = _PROMISED_CHARS) -> str:
    """The messages the company prepared for the people who would use the product, newest first, at most `most` and
    each cut at `chars`: what they were told they will get (a private upload link) is who must reach it. Empty when
    there are none."""
    # A to-do about a person has no project, but the project's approved message it carries is still a promise.
    asked = select(AgentRequest.id).where(AgentRequest.project_id == project_id)
    tasks = s.scalars(select(OwnerTask).where(
        (OwnerTask.project_id == project_id) | OwnerTask.request_id.in_(asked)).order_by(OwnerTask.id.desc()))
    said = [(h.get("to"), h["body"]) for t in tasks if isinstance(h := (t.card or {}).get("handout"), dict) and h.get("body")]
    return "\n\n".join(f"To {to or '-'}:\n{body[:chars]}" for to, body in said[:most])


def _needs(use: UseOut, lang: Lang) -> str:
    if not use.needs:
        return ""
    return "\n".join([msg("build.use.needs", lang), *(
        msg("build.use.need_required" if n.required else "build.use.need_optional", lang, name=n.name, what=n.what,
            without=n.without) for n in use.needs), msg("build.use.needs_own", lang)])


def state(s, job: DevJob) -> dict | None:
    """Where a merged build stands with the people who would use it: `decide` (the decision waits), `todo` (the owner's
    to-do with the steps is open), `usable` (done, with the address the owner gave or the local one), `later` (the owner
    put it off or skipped the to-do) or `closed` (the decision closed without a way). None for a build that is not
    merged, or merged before this existed. A build that is nothing people open is `tool`: done, with the command that
    runs it, when its files give one, and its README."""
    result = job.result or {}
    if job.status != DevJobStatus.MERGED.value or "use_request" not in result:
        return None
    request_id = result["use_request"]
    if request_id is None:
        use = (_acceptance(job) or AcceptanceOut(accepted=True, criteria_results=[], notes="")).use
        return {"job_id": job.id, "state": "tool", "request_id": None, "task_id": None, "how": None, "address": None,
                "command": use.local_steps[-1] if use is not None and use.local_steps else None,
                "readme": f"products/{_name(s.get(Spec, job.spec_id))}/README.md"}
    req = requests.current(s, s.get(AgentRequest, request_id))
    chosen = (req.response or {}).get("chosen_option")
    how = None if not chosen else "local" if chosen == (req.action_params or {}).get("local_option") else "host"
    task = s.scalar(select(OwnerTask).where(OwnerTask.request_id == req.id))
    out = {"job_id": job.id, "request_id": req.id, "task_id": task.id if task else None, "how": how, "address": None}
    if req.status == RequestStatus.PENDING.value:
        return {**out, "state": "decide"}
    if task is None:
        return {**out, "state": "later" if req.status == RequestStatus.DEFERRED.value else "closed"}
    if task.status == OPEN:
        return {**out, "state": "todo"}
    if task.status != DONE:
        return {**out, "state": "later"}
    use = _acceptance(job).use
    address = (task.result or {}).get("link") or (use.local_address if how == "local" else None)
    return {**out, "state": "usable", "address": address}


# How far a project's merged builds are from people, worst first: the project line names the build furthest away.
WORST_FIRST = ("decide", "closed", "later", "todo", "tool", "usable")


# ------------------------------------------------------------------ the report

def _merged_jobs(s, project_id: int) -> list[tuple[DevJob, Spec]]:
    """Each spec's latest job, where it is merged."""
    latest = (select(func.max(DevJob.id)).join(Spec, Spec.id == DevJob.spec_id)
              .where(Spec.project_id == project_id).group_by(DevJob.spec_id))
    jobs = s.scalars(select(DevJob).where(DevJob.id.in_(latest), DevJob.status == DevJobStatus.MERGED.value)
                     .order_by(DevJob.id))
    return [(j, s.get(Spec, j.spec_id)) for j in jobs]


def report_section(s, project_id: int, lang: Lang) -> str:
    """The final report's account of what was merged, from the acceptance check of the merged branch, never the spec.
    Empty while nothing is merged."""
    blocks = []
    for job, spec in _merged_jobs(s, project_id):
        acceptance = _acceptance(job)
        if acceptance is None:
            continue
        h = Handoff.model_validate(spec.handoff)
        merged_at = next((x["at"] for x in reversed(job.steps) if x["status"] == DevJobStatus.MERGED.value), None)
        lines = [msg("build.report.merged", lang, title=spec.title, name=h.target.repository, base=h.target.base_branch,
                     at=at(datetime.fromisoformat(merged_at), lang) if merged_at else "-")]
        lines += [f"- {_flat(b)}" for b in acceptance.built]
        met = sum(1 for c in acceptance.criteria_results if c.met)
        lines.append(msg("build.report.criteria", lang, n=len(acceptance.criteria_results), met=met))
        lines += [f"- {msg('build.report.unmet', lang, criterion=_flat(c.criterion))}"
                  for c in acceptance.criteria_results if not c.met]
        if unseen := [said for x in acceptance.unseen if (said := _said(x))]:
            lines.append(msg("build.merge_card.unseen", lang, what=try_it.unseen_list(unseen)))
        if acceptance.sample_output:
            lines.append(msg("build.merge_card.sample", lang, why=_flat(acceptance.sample_output)))
        if acceptance.use is not None:
            needed = [n.name for n in acceptance.use.needs if n.required]
            if needed:
                lines.append(msg("build.report.needs", lang, names=", ".join(needed)))
        lines.append(msg("build.report.use", lang))
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return "\n\n".join([f"## {msg('build.report.heading', lang)}", msg("build.report.note", lang), *blocks])


def _said(text: str | None) -> str:
    """The CPO's words as the owner reads them, without the engineers' names, as the merge card has them
    (`worker._said`): "the T-104 screen", "the stub AI (AI_PROVIDER=stub)"."""
    return owner_text.scrub(text or "", line=True).strip()


def _flat(text: str) -> str:
    """A model's line as one line: a newline in it could open a heading that ends the section early."""
    return " ".join(text.split())


_HEADINGS = tuple(f"## {msg('build.report.heading', x)}" for x in LANGS)


def with_section(body: str, section: str) -> str:
    """The report with its build section in place: replacing the one already there, else right after the summary, the
    first section, so the owner reads what was built before the plan's older words about it."""
    lines = body.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() in _HEADINGS), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        rest = lines[:start] + lines[end:]
    else:
        start, rest = None, lines
    heads = [i for i, line in enumerate(rest) if line.startswith("## ")]
    at_line = start if start is not None else heads[1] if len(heads) > 1 else len(rest)
    new = rest[:at_line] + ([*section.split("\n"), ""] if section else []) + rest[at_line:]
    return "\n".join(new)


def update_report(s, project_id: int, lang: Lang) -> None:
    """Put the build section into the project's final report, when it has one (a report written later carries it from
    the start, company_graph.report)."""
    p = s.get(Project, project_id)
    art_id = (p.context or {}).get("report_artifact_id") if p else None
    art = s.get(Artifact, art_id) if art_id else None
    if art is None:
        return
    body = registry.read(art.path)
    new = with_section(body, report_section(s, project_id, lang))
    if new != body:
        art.commit_sha = registry.write_files({art.path: new}, f"[P-{project_id}] report: what was merged")
