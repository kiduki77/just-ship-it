"""Where each development stands, for the owner: one line that says what is happening or what stopped it, since
when, and the one thing the owner can do. A development starts as an approved DEVELOPMENT request, becomes a spec,
then dev jobs, so the Engineering page never shows "no spec yet" while a spec is being written or waits for a worker.
Every line is in the owner's language (`lang`), read once by the route."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from justshipit.contracts import owner_text
from justshipit.contracts.handoff import DevJobStatus, SpecStatus
from justshipit.contracts.requests import RequestStatus, RequestType
from justshipit.db.models import AgentRequest, DevJob, Job, Project, Spec
from justshipit.engineering import wireflow
from justshipit.i18n import Lang, catalog, msg
from justshipit.i18n.match.dev_titles import PROPOSAL
from justshipit.services import budget, queue, requests

_J = DevJobStatus

# The running statuses. What each is doing, when its last step has no line of its own (a job from before steps had
# one), is build.doing.<status>.
_RUNNING = frozenset({_J.INTAKE, _J.PLAN_REVIEW, _J.IMPLEMENTING, _J.QA, _J.REVIEWING, _J.SHIPPING})
_APPROVED = (RequestStatus.APPROVED.value, RequestStatus.AUTO_APPROVED.value)


@dataclass
class Link:
    label: str
    to: str


@dataclass
class Stage:
    """One development as the owner reads it. `actions` are worker.owner_actions keys, `retry_dev` offers a new
    attempt from scratch, and `retry_wait` says until when it can't, while the old worker's hold lasts. `retry_job`
    re-queues a failed worker job (명세 작성). `waits` names the decision a delivered build waits on in Decisions:
    "merge", or "fix" (what to change)."""
    key: str
    tone: str  # bad: stopped and the owner must act · warn: waits on the owner · run · wait · ok
    ref: str
    title: str
    headline: str
    detail: str | None = None
    since: datetime | str | None = None
    project_id: int | None = None
    link: Link | None = None
    dev_job_id: int | None = None
    actions: list[str] = field(default_factory=list)
    retry_dev: bool = False
    retry_wait: str | None = None
    retry_job: int | None = None
    waits: str | None = None


_ORDER = {"bad": 0, "warn": 1, "run": 2, "wait": 3, "ok": 4}


def listed_projects(business_id: int | None):
    """The projects the 개발 page lists without one project picked: one business's, or every business's, never an
    archived one. An archived project's own page still shows its development."""
    live = select(Project.id).where(Project.status != "ARCHIVED")
    return live if business_id is None else live.where(Project.business_id == business_id)


def board(s, project_id: int | None = None, business_id: int | None = None, *, lang: Lang) -> list[dict]:
    """Every development not yet merged of one project, one business, or all, the ones that need the owner first."""
    def scoped(column):
        return column == project_id if project_id is not None else column.in_(listed_projects(business_id))
    out = [*_unwritten(s, scoped(AgentRequest.project_id), lang),
           *(st for spec in _specs(s, scoped(Spec.project_id)) if (st := _of_spec(s, spec, lang)))]
    return [asdict(st) for st in sorted(out, key=lambda st: _ORDER[st.tone])]


# The steps whose record is the engineer's own account, written in the owner's language: the console shows it under the
# step's details. Every other record is the worker's (English, a command, a skill's name), kept for engineers only.
_ACCOUNTS = frozenset({"build.step.intake_done", "build.step.plan_done", "build.step.build_done",
                       "build.step.review_passed", "build.step.review_blocking", "build.step.clarify"})
# The lists a step keeps that the owner reads, under build.step.list.<name>.
_LISTS = ("assumptions", "decisions")


def steps(job: DevJob, lang: Lang) -> list[dict]:
    """The job's steps as the console shows them: each one's line in the owner's language, and under its details the
    engineer's account and lists, without codes or skill names."""
    from justshipit.engineering import worker
    return [_shown(x, worker.step_line(x, lang), lang) for x in job.steps]


def _shown(step: dict, line: str | None, lang: Lang) -> dict:
    said = step.get("say")
    key = said.get("key") if isinstance(said, dict) else None
    data = step.get("data") or {}
    lists = [{"label": msg(f"build.step.list.{name}", lang), "items": items} for name in _LISTS
             if (items := [x for v in data.get(name) or [] if (x := _plain(str(v)))])]
    return {"at": step["at"], "status": step["status"], "say": line, "by": step.get("by") or by_status(step),
            "detail": (_plain(step.get("summary") or "") or None) if key in _ACCOUNTS else None, "lists": lists}


# The step lines whose work is the product agent's or the owner's, for a step from before steps named who did them.
_BY_KEY = {"build.step.accepted": "product", "build.step.not_accepted": "product", "build.step.merged": "owner",
           "build.step.fix_queued": "owner", "build.step.merge_deferred": "owner"}


def by_status(step: dict) -> str | None:
    """Who did a step written before steps named anyone: its line's role, else its status's (worker.STEP_BY)."""
    from justshipit.engineering import worker
    said = step.get("say")
    key = said.get("key") if isinstance(said, dict) else None
    return _BY_KEY.get(key or "") or worker.STEP_BY.get(step.get("status") or "")


def _plain(text: str) -> str:
    return owner_text.scrub(text, line=True, names=True).strip()


def _specs(s, where):
    return s.scalars(select(Spec).where(Spec.status != SpecStatus.REJECTED.value, where).order_by(Spec.id.desc()))


def _title(title: str) -> str:
    """A request's title without the prefix the company puts on a build proposal, in whichever language it was made."""
    for prefix in PROPOSAL.values():
        title = title.removeprefix(prefix)
    return owner_text.title(title)


def _unwritten(s, where, lang: Lang) -> list[Stage]:
    """Approved DEVELOPMENT requests with no spec yet: the CPO's write_spec job is all there is to show."""
    q = select(AgentRequest).where(AgentRequest.type == RequestType.DEVELOPMENT.value,
                                   AgentRequest.status.in_(_APPROVED), where,
                                   ~select(Spec.id).where(Spec.request_id == AgentRequest.id).exists())
    out = []
    for req in s.scalars(q.order_by(AgentRequest.id)):
        job = _latest(s, "write_spec", "request_id", req.id)
        st = Stage(key=f"req-{req.id}", tone="wait", ref=requests.display_id(req.id), title=_title(req.title),
                   headline=msg("build.stage.spec_waiting", lang), since=req.resolved_at, project_id=req.project_id)
        if job is None or job.status == "done":
            st.detail = msg("build.stage.spec_job_missing", lang)
            st.link = Link(msg("build.link.decision", lang), f"/inbox?req={req.id}")
        elif job.status == "running":
            st.tone, st.headline, st.since = "run", msg("build.stage.spec_writing", lang), job.locked_at or st.since
            st.detail = msg("build.stage.spec_writing_next", lang)
        elif job.status == "failed":
            st.tone, st.headline = "bad", msg("build.stage.spec_failed", lang)
            st.detail, st.retry_job, st.since = _first_line(job.last_error), job.id, job.finished_at
        else:
            st.detail = why_queued(s, job, lang)
        out.append(st)
    return out


# What decides an unapproved draft, read from its approval card: the spec's stored status stays IN_REVIEW whatever
# the owner answered, so the owner's view is derived here.
REJECTED, SUPERSEDED = SpecStatus.REJECTED.value, "SUPERSEDED"
_SAID_NO = (RequestStatus.REJECTED.value, RequestStatus.DENIED_BY_POLICY.value)
_STILL_ASKED = (RequestStatus.PENDING.value, RequestStatus.DEFERRED.value, RequestStatus.EXPIRED.value, *_APPROVED)


def _own_card(s, spec: Spec) -> AgentRequest | None:
    """The approval card that decides this spec. A card names its spec in action_params; an older merge pointed
    several drafts at one card that decides another spec, and a draft whose card approved an earlier version has
    none of its own. A card from before cards named their spec is taken at its word."""
    asked = s.get(AgentRequest, spec.approval_request_id) if spec.approval_request_id else None
    named = (asked.action_params or {}).get("spec_id") if asked is not None else None
    return asked if asked is not None and named in (None, spec.id) else None


# An approved spec reads as where its latest dev job is: the stored status stays READY_FOR_DEV after a job fails.
BUDGET_STOPPED = "BUDGET_STOPPED"
DROPPED = "DROPPED"
_BY_JOB = {**{st.value: SpecStatus.IN_DEVELOPMENT.value for st in _RUNNING},
           _J.CLARIFICATION_REQUIRED.value: SpecStatus.CLARIFICATION_REQUIRED.value,
           _J.DELIVERED.value: SpecStatus.DELIVERED.value, _J.MERGED.value: SpecStatus.MERGED.value}


def spec_state(s, spec: Spec) -> str:
    """The spec's status as the owner reads it: an approved spec's latest dev job's (BUDGET_STOPPED or FAILED when it
    stopped), REJECTED when its card was turned down, SUPERSEDED when no card of its own is open (folded under the
    product's live spec), else its stored status."""
    if spec.approved_commit_sha:
        from justshipit.engineering import worker
        job = s.scalar(select(DevJob).where(DevJob.spec_id == spec.id)
                       .order_by(DevJob.attempt.desc(), DevJob.id.desc()).limit(1))
        if job is not None and job.status == _J.FAILED.value:
            return BUDGET_STOPPED if worker.budget_scope(job.error) else _J.FAILED.value
        if job is not None and worker.dropped(job):
            return DROPPED
        return _BY_JOB.get(job.status, spec.status) if job is not None else spec.status
    card = _own_card(s, spec)
    if card is None and spec.status == SpecStatus.CLARIFICATION_REQUIRED.value:
        return spec.status  # its card waits for the owner's answers to its questions (engineering/questions.py)
    if wireflow.sent_back(spec):
        return spec.status  # its next card waits for the redraft the owner's notes asked for (specs.redraft_spec)
    if card is not None and card.status in _SAID_NO:
        return REJECTED
    return spec.status if card is not None and card.status in _STILL_ASKED else SUPERSEDED


def _of_spec(s, spec: Spec, lang: Lang) -> Stage | None:
    st = Stage(key=f"spec-{spec.id}", tone="wait", ref=spec.spec_key, title=owner_text.title(spec.title), headline="",
               since=spec.updated_at, project_id=spec.project_id)
    if not spec.approved_commit_sha:
        if spec_state(s, spec) in (REJECTED, SUPERSEDED):
            return None
        asked = _own_card(s, spec)
        if wireflow.sent_back(spec):
            return _redrafting(s, spec, st, lang)
        if asked is None:  # questions before the card
            from justshipit.engineering import questions
            if waiting := questions.open_cards(s, spec):
                st.tone, st.headline = "warn", msg("build.stage.spec_questions", lang, n=len(waiting))
                st.detail = msg("build.stage.spec_questions_next", lang)
                st.link = Link(msg("build.link.answer", lang), f"/inbox?req={waiting[0].id}")
                return st
            run = _latest(s, "spec_questions", "spec_id", spec.id)
            if run is not None and run.status == "failed":
                st.tone, st.headline = "bad", msg("build.stage.spec_answers_failed", lang)
                st.detail, st.retry_job = _first_line(run.last_error), run.id
            else:
                st.headline = msg("build.stage.spec_answers_writing", lang)
            return st
        st.link = Link(msg("build.link.spec", lang), f"/inbox?req={asked.id}")
        if asked.status == RequestStatus.PENDING.value:
            st.tone, st.headline = "warn", msg("build.stage.spec_pending", lang)
            st.detail = msg("build.stage.spec_pending_next", lang)
            return st
        if asked.status not in _APPROVED:
            st.headline, st.detail = msg("build.stage.spec_deferred", lang), msg("build.stage.spec_deferred_next", lang)
            return st
        st.link = None
    job = s.scalar(select(DevJob).where(DevJob.spec_id == spec.id).order_by(DevJob.attempt.desc(), DevJob.id.desc()))
    if job is None:
        start = _latest(s, "spec_approved", "spec_id", spec.id)
        st.headline = msg("build.stage.preparing", lang)
        if start is not None and start.status == "failed":
            st.tone, st.headline = "bad", msg("build.stage.prepare_failed", lang)
            st.detail, st.retry_job = _first_line(start.last_error), start.id
        elif start is not None and start.status == "queued":
            st.detail = why_queued(s, start, lang)
        return st
    return _of_job(s, spec, job, st, lang)


def _redrafting(s, spec: Spec, st: Stage, lang: Lang) -> Stage:
    """A spec the owner sent back with notes on its screens: being written again, or not written with no job left to
    do it, which the owner can run again. One the owner stopped is paused, not failed."""
    last, live = wireflow.redraft_jobs(s, spec)
    if live:
        st.headline, st.detail = msg("build.stage.spec_redrafting", lang), msg("build.stage.spec_writing_next", lang)
        if last.status == "queued":
            st.detail = why_queued(s, last, lang)
        else:
            st.tone = "run"
        return st
    stopped = last is not None and last.status == "cancelled"
    st.tone, st.headline = ("wait", msg("build.stage.spec_redraft_stopped", lang)) if stopped else (
        "bad", msg("build.stage.spec_redraft_failed", lang))
    st.detail = msg("build.stage.spec_redraft_failed_next", lang)
    st.retry_job = last.id if last is not None and last.status in wireflow.GONE else None
    return st


def _of_job(s, spec: Spec, job: DevJob, st: Stage, lang: Lang) -> Stage | None:
    from justshipit.engineering import worker
    status = DevJobStatus(job.status)
    st.key, st.dev_job_id, st.since = f"job-{job.id}", job.id, _last_at(job) or job.updated_at
    st.ref = f"J-{job.id} · {spec.spec_key}"
    st.actions = worker.owner_actions(s, job)
    if status == _J.MERGED:
        return None
    if status == _J.QUEUED:
        run = _latest(s, "dev_job", "dev_job_id", job.id)
        st.headline = msg("build.stage.queued", lang)
        st.detail = why_queued(s, run, lang) if run is not None and run.status == "queued" \
            else msg("build.stage.no_worker_job", lang)
        st.retry_dev = run is None or run.status != "queued"
    elif status in _RUNNING:
        progress = _progress(job, lang)
        doing = progress or _last_say(job, lang) or msg(f"build.doing.{status.value}", lang)
        st.tone, st.headline = "run", msg("build.stage.running", lang, doing=doing)
        if progress:  # its time is when the build last moved, not when its step began
            st.since = _progress_at(job) or st.since
        _unanswered(s, job, st, lang)
        if (run := _worker_job(s, job)) is not None and run.kind in queue.BUILD_KINDS and run.status == "queued":
            _resuming(s, job, run, st, lang)
        if (deliver := _latest(s, "dev_deliver", "dev_job_id", job.id)) and deliver.status == "queued":
            st.tone, st.headline = "wait", msg("build.stage.redeliver_waiting", lang)
            st.detail = why_queued(s, deliver, lang)
    elif status == _J.CLARIFICATION_REQUIRED and (run := _worker_job(s, job)) is not None \
            and run.kind in queue.BUILD_KINDS and run.status == "queued" and run.wait_reason:
        _resuming(s, job, run, st, lang)  # a usage limit met while the CPO answered: nothing waits on the owner
    elif status == _J.CLARIFICATION_REQUIRED:
        st.tone, st.headline = "warn", msg("build.stage.question", lang)
        st.detail = msg("build.stage.question_next", lang)
        if q := _open_question(s, spec.project_id, job.id):
            st.link = Link(msg("build.link.answer", lang), f"/inbox?req={q}")
    elif status == _J.FAILED:
        _failed(s, spec, job, st, lang)
    elif status == _J.DELIVERED:
        _delivered(s, job, st, lang)
    elif worker.dropped(job):  # the owner dropped it after it failed its check: nothing comes next
        st.tone, st.headline = "ok", msg("build.stage.dropped", lang)
        st.detail = _first_line(job.error)
    else:  # DISCARDED: its new attempt is the spec's latest, so this is an attempt the owner threw away last
        st.tone, st.headline = "wait", msg("build.stage.discarded", lang)
    return st


NO_DEV = {"dev_failed": [], "dev_active": 0, "dev_delivered": 0, "dev_dropped": 0, "dev_stop": None, "dev_use": None}
_STOP_FIRST = ("budget", "deliver", "rebuild")


def by_project(s, project_ids: list[int] | None = None) -> dict[int, dict]:
    """Each spec's latest dev job, by project (of `project_ids`, or every project): the failed ones by J-n, how many
    are under way (a delivered one too, while the company checks, merges or fixes it), and the owner's way on for the
    stop that needs the owner first (`dev_stop`, stop_kind). A project whose run is DONE while its development failed
    must not read as finished, nor one whose build waits on the owner's merge decision (`dev_delivered`), nor one whose
    merged build nobody can open yet (`dev_use`, usable.state, of its merged build furthest from people).
    `project_stage` reads it."""
    from justshipit.engineering import usable, worker
    latest = select(DevJob.id, DevJob.status, Spec.project_id, func.row_number().over(
        partition_by=DevJob.spec_id, order_by=DevJob.id.desc()).label("rn")).join(Spec, Spec.id == DevJob.spec_id)
    if project_ids is not None:
        latest = latest.where(Spec.project_id.in_(project_ids))
    latest = latest.subquery()
    out: dict[int, dict] = {}
    stops: dict[int, dict[str, list[int]]] = {}
    for jid, st, pid, _ in s.execute(select(latest).where(latest.c.rn == 1).order_by(latest.c.id)):
        d = out.setdefault(pid, {**NO_DEV, "dev_failed": []})
        if st == "FAILED":
            d["dev_failed"].append(jid)
            stops.setdefault(pid, {}).setdefault(stop_kind(s.get(DevJob, jid)), []).append(jid)
        elif st in (*queue.DEV_ACTIVE, "CLARIFICATION_REQUIRED"):
            d["dev_active"] += 1
        elif st == "DELIVERED" and isinstance(worker.pending_for(s, jid), Job):
            d["dev_active"] += 1  # the company still checks, merges or fixes it: no card waits on the owner yet
        elif st == "DELIVERED":  # waits on the owner to merge it, have it fixed, or throw it away
            d["dev_delivered"] += 1
        elif st == "DISCARDED" and worker.dropped(s.get(DevJob, jid)):
            d["dev_dropped"] += 1
        elif st == "MERGED" and (use := usable.state(s, s.get(DevJob, jid))) is not None:
            if d["dev_use"] is None or usable.WORST_FIRST.index(use["state"]) < usable.WORST_FIRST.index(
                    d["dev_use"]["state"]):
                d["dev_use"] = use
    for pid, by in stops.items():
        kind = next(k for k in _STOP_FIRST if k in by)
        short = (budget.dev_shortfall(s, pid) or {}).get("short_usd") if kind == "budget" else None
        out[pid]["dev_stop"] = {"kind": kind, "jobs": by[kind], "short_usd": short}
    return out


# A merged build is finished for the owner when people can use it, or when it is nothing people open (a library).
_FINISHED_USE = ("usable", "tool")


def project_stage(status: str, dev: dict) -> str:
    """The project's one state, which every page shows (its header, its business, the Projects list and counts): the
    run's status, except that a DONE run is not done while a development of it stopped (DEV_STOPPED), runs
    (DEV_ACTIVE), waits on the owner's merge decision (DEV_DELIVERED), or is merged but nobody can use it yet
    (MERGED). A run whose build the owner dropped is finished, and says so (DEV_DROPPED)."""
    if status != "DONE":
        return status
    if dev["dev_failed"]:
        return "DEV_STOPPED"
    if dev["dev_active"]:
        return "DEV_ACTIVE"
    if dev["dev_delivered"]:
        return "DEV_DELIVERED"
    if dev["dev_use"] is not None and dev["dev_use"]["state"] not in _FINISHED_USE:
        return "MERGED"
    if dev.get("dev_dropped"):
        return "DEV_DROPPED"
    return "DONE"


def stop_kind(job: DevJob) -> str:
    """The owner's way on for a failed job, which every sentence about it names: "deliver" (the code passed the
    tests and the review; deliver again), "budget" (raise it, then build again) or "rebuild" (from scratch)."""
    from justshipit.engineering import worker
    if worker.stopped_shipping(job):
        return "deliver"
    return "budget" if worker.budget_scope(job.error) else "rebuild"


def _failed(s, spec: Spec, job: DevJob, st: Stage, lang: Lang) -> None:
    from justshipit.engineering import worker
    st.tone = "bad"
    error = job.error or ""
    kind = stop_kind(job)
    if kind == "deliver":
        st.headline = msg("build.stage.ship_stuck", lang)
        st.detail = msg("build.stage.ship_stuck_next", lang)
    elif kind == "budget":
        st.headline = msg("build.budget.stop", lang)
        st.detail = worker.budget_line(s, spec.project_id, worker.budget_scope(error), lang)
        st.link = Link(msg("build.link.budget", lang), f"/projects/{spec.project_id}/settings#budget")
    else:
        st.headline = msg("build.stage.stopped", lang)
        st.detail = failure_line(s, job, lang)
    st.retry_dev = not st.actions or st.actions == ["deliver_again"]
    if st.retry_dev:
        st.retry_wait = rebuild_wait(s, job, lang)
        st.detail = f"{st.detail} {st.retry_wait}" if st.retry_wait else st.detail


def rebuild_wait(s, job: DevJob, lang: Lang) -> str | None:
    """Until when a rebuild is refused (specs.retry_dev) while the old worker's hold lasts, as the owner reads it."""
    held = s.scalar(select(func.max(Job.held_until)).where(Job.lock_key == _key(job), Job.held_until > func.now()))
    return msg("build.stage.rebuild_at", lang, until=at(held, lang)) if held is not None else None


def failure_line(s, job: DevJob, lang: Lang) -> str:
    """Why a failed build stopped, as the owner reads it: a worker the queue gave up on says so calmly, and the
    reclaim's own text (worker id, job number, attempts) stays in the job's error for engineers."""
    run = _worker_job(s, job)
    if run is not None and queue.reclaimed_out(run):
        return msg("build.stage.worker_lost", lang)
    return _first_line(job.error) or msg("build.stage.no_cause", lang)


def _key(job: DevJob) -> str:
    return f"dev_job:{job.id}"


def _worker_job(s, job: DevJob) -> Job | None:
    return s.scalar(select(Job).where(Job.lock_key == _key(job), Job.kind.in_(queue.DEV_KINDS)).order_by(Job.id.desc()))


def _resuming(s, job: DevJob, run: Job, st: Stage, lang: Lang) -> None:
    """A build or fix round the queue gave back mid-run, to pick up at its step: its worker restarted, or it met a usage
    limit and waits for the reset (which the headline names, so nothing below repeats it while the wait lasts). On the
    host a run whose worker stopped mid-step cannot carry on (worker.carries_on): the next run ends it instead. A job
    back at its question has no step to lose, and the next run starts it."""
    from justshipit.engineering import worker
    st.tone = "wait"
    if DevJobStatus(job.status) in _RUNNING and not worker.carries_on(job):
        st.headline, st.detail = msg("build.stage.interrupted", lang), why_queued(s, run, lang)
    elif run.wait_reason and run.run_after > s.scalar(select(func.now())):
        st.headline, st.detail = msg("build.stage.usage_paused", lang, until=at(run.run_after, lang)), None
    elif run.wait_reason:  # the limit has reset and no worker has picked it up yet
        st.headline, st.detail = msg("build.stage.usage_reset", lang), why_queued(s, run, lang)
    else:
        st.headline, st.detail = msg("build.stage.resuming", lang), why_queued(s, run, lang)


def _unanswered(s, job: DevJob, st: Stage, lang: Lang) -> None:
    """A running build whose worker missed several heartbeats: its last progress line would stand until the queue
    takes the job back a full lease after the last beat, so it says the worker is silent and when that ends."""
    run = _worker_job(s, job)
    if run is None or run.status != "running" or run.locked_at is None:
        return
    silent = (s.scalar(select(func.now())) - run.locked_at).total_seconds()
    if silent <= 3 * queue.heartbeat_s():
        return
    st.tone, st.headline = "wait", msg("build.stage.no_answer", lang, n=round(silent / 60))
    st.detail = msg("build.stage.no_answer_next", lang, until=at(run.locked_at + timedelta(seconds=queue.lease_s()), lang))


def _delivered(s, job: DevJob, st: Stage, lang: Lang) -> None:
    from justshipit.engineering import revise, worker
    waiting = worker.pending_for(s, job.id)
    fix = revise.latest(job)
    if isinstance(waiting, AgentRequest) and waiting.action_kind == "dev.merge":
        st.tone, st.headline, st.waits = "warn", msg("build.stage.merge_waiting", lang), "merge"
        st.detail = msg("build.stage.merge_waiting_next", lang)
        st.link = Link(msg("build.link.merge", lang), f"/inbox?req={waiting.id}")
    elif isinstance(waiting, AgentRequest):
        failed = bool((waiting.action_params or {}).get(revise.FAILED))
        st.tone, st.waits = "warn", "fix"
        st.headline = msg("build.stage.failed_check" if failed else "build.stage.fix_question", lang)
        st.detail = msg("build.stage.failed_check_next" if failed else "build.stage.fix_question_next", lang)
        st.link = Link(msg("build.link.answer", lang), f"/inbox?req={waiting.id}")
    elif waiting is not None and waiting.kind in revise.JOB_KINDS:
        st.headline = msg("build.stage.fix_auto_waiting" if (fix or {}).get("auto") else "build.stage.fix_waiting", lang)
        st.detail = why_queued(s, waiting, lang) if waiting.status == "queued" else None
    elif waiting is not None:
        st.tone, st.headline = "run", msg("build.stage.accepting", lang)
    elif fix is not None and fix["state"] == revise.State.WAITING_BUDGET:
        short = revise.shortfall(s, s.get(Spec, job.spec_id).project_id)
        st.tone, st.headline = "warn", msg("build.stage.fix_budget", lang)
        st.detail = msg("build.stage.fix_budget_next", lang, need=f"{revise.need_usd():g}",
                        left=f"{(short or {}).get('left_usd', 0):.2f}") if short else msg("build.stage.fix_budget_ok", lang)
        if short:
            st.link = Link(msg("build.link.budget", lang), f"/projects/{s.get(Spec, job.spec_id).project_id}/settings#budget")
    elif fix is not None and fix["state"] == revise.State.STOPPED and st.actions:
        st.tone, st.headline = "bad", msg("build.stage.fix_stopped", lang)
        st.detail = _first_line(job.error)
    elif (job.result or {}).get("failed"):  # its fix-or-drop card was put off or turned down
        st.tone, st.headline = "warn", msg("build.stage.failed_put_off", lang)
        st.detail = msg("build.stage.failed_put_off_next", lang)
    elif st.actions:
        st.tone, st.headline = "bad" if job.error else "warn", msg("build.stage.delivered_check", lang)
        st.detail = _first_line(job.error) or _last_say(job, lang)
    else:
        st.tone, st.headline = "ok", msg("build.stage.delivered", lang)


def at(moment: datetime, lang: Lang) -> str:
    """A moment as the owner reads it, e.g. "Nov 2, 19:33 (KST)": with its date, since the owner may read it the next
    day, and its zone, since the server's local zone is the only one we know."""
    moment = moment.astimezone()
    return msg("build.at", lang, month=msg(f"build.month.{moment.month}", lang), day=moment.day,
               hm=f"{moment:%H:%M}", tz=moment.tzname())


def why_queued(s, job: Job, lang: Lang) -> str:
    """Why a queued worker job has not started: a usage wait, a reclaimed holder, another job of the same key, no
    worker, or nothing at all."""
    now = s.scalar(select(func.now()))
    if job.run_after > now:
        if job.wait_reason:
            reason = f"usage.reason.{job.wait_reason}"
            return msg("build.queue.usage", lang, until=at(job.run_after, lang),
                       reason=msg(reason if reason in catalog() else "build.queue.usage_limit", lang))
        return msg("build.queue.starts_at", lang, until=at(job.run_after, lang))
    if job.lock_key:
        held = s.scalar(select(func.max(Job.held_until)).where(Job.lock_key == job.lock_key, Job.held_until > now))
        if held is not None:
            return msg("build.queue.held", lang, until=at(held, lang))
        ahead = s.scalar(select(Job).where(Job.lock_key == job.lock_key, Job.status == "running", Job.id != job.id))
        if ahead is not None:
            return msg("build.queue.ahead", lang)
    if not queue.last_worker_seen(s)[1]:
        return msg("build.queue.worker_down", lang)
    busy = s.scalar(select(func.count()).select_from(Job).where(Job.status == "running"))
    return msg("build.queue.busy", lang, n=busy) if busy else msg("build.queue.soon", lang)


def _latest(s, kind: str, key: str, value: int) -> Job | None:
    return s.scalar(select(Job).where(Job.kind == kind, Job.payload[key].as_integer() == value).order_by(Job.id.desc()))


def _open_question(s, project_id: int, job_id: int) -> int | None:
    return s.scalar(select(AgentRequest.id).where(
        AgentRequest.project_id == project_id, AgentRequest.type == RequestType.QUESTION.value,
        AgentRequest.status == RequestStatus.PENDING.value,
        AgentRequest.action_params["dev_job_id"].as_integer() == job_id))


def _last_say(job: DevJob, lang: Lang) -> str | None:
    from justshipit.engineering import worker
    return next((line for x in reversed(job.steps) if (line := worker.step_line(x, lang))), None)


def _progress(job: DevJob, lang: Lang) -> str | None:
    """How far a long build has come (worker.note_progress), while no newer step has come since it was noted. A build
    that carries on where a stopped run left off keeps saying so."""
    from justshipit.engineering import worker
    p = (job.result or {}).get("progress") or {}
    if p.get("status") != job.status or p.get("step") != len(job.steps):
        return None
    said = job.steps[-1].get("say") if job.steps else None
    key = str(said.get("key", "")) if isinstance(said, dict) else ""
    line = worker.progress_say(p, resumed=next((f for f in worker.RESUMED_LINES if key.startswith(f)), None))
    return worker.step_line({"say": line}, lang) if line else None


def _progress_at(job: DevJob) -> str | None:
    return ((job.result or {}).get("progress") or {}).get("at")


def _last_at(job: DevJob) -> str | None:
    return job.steps[-1]["at"] if job.steps else None


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n", 1)[0][:600]
