"""Where the owner's own click enqueues work, and where the kinds the owner asks for are enqueued: both are pinned, so
a new site fails here until someone reviews it. The owner's clicks run with no stage open; what the company starts on
its own waits for one."""
import pytest
import bet_loop_kit as kit
import cycle_kit
from justshipit.contracts.requests import HumanDecision, HumanResponse
from justshipit.db.models import ActivityEvent, CycleStage, Job, OwnerTask
from justshipit.services import gate, owner_tasks, people_finder, projects, requests
from gate_sites import owner_reach, sites
from owner_task_kit import approve
from owner_task_kit import draft as post_draft
from sqlalchemy import select, update
from test_one_card import asked, spec_for
from ask_kit import by_project_manager

# The owner's own click: the job runs whatever stage is open.
pytestmark = pytest.mark.usefixtures("own_jobs_settled")


OWNER_ACTS = {"api/capability:run_factory", "api/console:job_retry", "api/growth:enqueue", "cli:project_retry",
              "cli:skills", "engineering/plans:revise", "engineering/specs:new_attempt", "engineering/worker:accept_again",
              "engineering/worker:deliver_again", "operations/scheduler:run_now", "services/control:resume",
              "services/control:retry_project", "services/control:_restart_after_raise", "services/control:add_directive",
              "services/kit:request", "services/people_finder:request", "services/requests:respond"}
# Helpers both an owner's click and the company reach; their caller says which it is.
PASSED_ON = {"engineering/revise:_begin", "services/bet_loop:start_routine", "services/projects:create",
             "services/requests:_maybe_resume", "services/requests:_maybe_dispatch_side_effects",
             "services/requests:_replan", "services/roadmap:_start_run", "services/roadmap:queue_retro"}
ASKED_SITES = {
    "kickoff_turn": {"services/kickoff:_enqueue_turn"},
    "product_report": {"services/kickoff:_enqueue_report"},
    "triage_idea": {"services/ideas:_enqueue"},
    "chat_reply": {"services/chat:enqueue_reply"},
    "realign_project": {"services/control:add_directive", "services/owner_tasks:_plan_hears", "services/requests:_replan"},
    "dev_merge": {"services/requests:_effect"},
    "dev_merge_declined": {"services/requests:_maybe_dispatch_side_effects"},
    "skill_activate": {"services/requests:_effect"},
    "raise_budget": {"services/requests:_effect"},
    "derive_door_rules": {"services/roadmap:_on_approved"},
    "profile_infer": {"services/profile/infer:enqueue_for_answer", "services/profile/infer:enqueue_for_plan"},
    "kit_prepare": {"services/kit:request"},
}
# Sites whose kind is a variable: each was read to know which kinds reach it.
DYNAMIC = {"api/console:job_retry", "engineering/revise:_begin", "operations/scheduler:tick",
           "operations/scheduler:run_now", "services/idea_review:_open_round", "services/people_finder:request",
           "services/requests:_maybe_dispatch_side_effects", "services/cycle:_ask", "engineering/plans:_write_again"}


def test_the_sites_that_say_the_owner_asked_are_the_reviewed_ones():
    found = sites()
    assert {x.where for x in found if x.by_owner == "True"} == OWNER_ACTS
    assert {x.where for x in found if x.by_owner == "passed on"} == PASSED_ON


def test_each_kind_the_owner_asks_for_is_enqueued_only_where_reviewed():
    found = sites()
    asked = {k for k, r in gate.POLICY.items() if r.gate is gate.Gate.ASKED} - {"idea_review"}  # its kind is KIND
    assert set(ASKED_SITES) == asked
    for kind, where in ASKED_SITES.items():
        assert {x.where for x in found if x.kind == kind} == where, kind
    assert {x.where for x in found if x.kind is None} == DYNAMIC


# Enqueues an owner's click reaches that are stage work on purpose, each with why. Any other enqueue a click reaches
# must say `by_owner` (True, or passed on from a caller that knows), so a click never parks without a word. The build
# chain says `approved=True`: it follows the owner's approval, so it runs when no stage does and joins the stage when
# one runs (test_the_build_an_owner_approved_runs_...).
REACHED_BY_DESIGN = {
    "engineering/specs:on_spec_approved": "the owner approved the spec: the build runs on that approval with no stage "
                                          "running, and belongs to the stage that runs",
    "engineering/worker:_deliver": "the acceptance check follows its build: it runs on the owner's approval with no "
                                   "stage running, and belongs to the stage that runs",
    "engineering/worker:_mark_merged": "the owner merged: the usable check runs on that with no stage running, and "
                                       "belongs to the stage that runs",
    "services/cycle:_ask": "every kind it asks for is a CYCLE kind, which admit never parks",
    "services/idea_review:_open_round": "its kind is idea_review, an ASKED kind, which admit never parks",
    "engineering/plans:begin": "a plan starts from a development ask, which only the company files (an agent's draft or "
                               "the run's build); the click that reaches requests.create files an external action "
                               "again, never a build",
    "engineering/plans:_write_again": "it writes a plan again when the company's next development ask joins it; the "
                                      "owner's own retry of the failed job is api/console:job_retry",
}
APPROVED_CHAIN = {"engineering/specs:on_spec_approved", "engineering/worker:_deliver", "engineering/worker:_mark_merged"}


def test_an_enqueue_an_owners_click_reaches_says_whether_the_click_runs():
    """The class behind the review's probes A to E: a click reached an enqueue nobody had listed, and the work sat
    `gated` while the console said queued. Sites are found by walking calls from every API route and `requests.respond`
    (and into the jobs a click enqueues as the owner's), so a new site needs no one to remember it."""
    asked = {k for k, r in gate.POLICY.items() if r.gate is gate.Gate.ASKED}
    reach = owner_reach(asked)
    parks = {gate.Gate.STAGE_WORK, gate.Gate.STAGE_RECURRING}
    open_ = {x.where for x in sites() if x.by_owner in ("", "approved") and x.where in reach
             and (x.kind is None or gate.POLICY.get(x.kind) is None or gate.POLICY[x.kind].gate in parks)}
    assert open_ == set(REACHED_BY_DESIGN), {w: " > ".join(reach[w]) for w in open_ - set(REACHED_BY_DESIGN)}


def test_the_build_chain_after_an_owners_approval_says_approved():
    """These three sites park at a shut gate unless they say `approved=True`; the kind they enqueue is stage work."""
    assert {x.where for x in sites() if x.by_owner == "approved"} == APPROVED_CHAIN


def _gate_shut(s, b) -> None:
    s.execute(update(CycleStage).where(CycleStage.business_id == b.id).values(status="done"))
    s.flush()


def test_the_owners_clicks_at_a_shut_gate_run_and_the_companys_own_work_waits(s):
    """Probe F6 of the review: the owner's approval, Resume and Retry were parked when no stage was open."""
    from justshipit.services import control
    b = kit.make_business(s, name="관문 클릭")
    _gate_shut(s, b)
    p = projects.create(s, "g", name="Stage S1 project", start=False, business=b)
    p.status = "DONE"
    s.flush()
    spec = spec_for(s, p, asked(s, p, "견적봇 MVP"))
    requests.respond(s, spec.approval_request_id, HumanResponse(decision=HumanDecision.APPROVE))
    q = projects.create(s, "g2", name="Paused", start=False, business=b)
    q.status = "WAITING_HUMAN"
    s.flush()
    control.pause(s, q.id, "wait")
    control.resume(s, q.id)
    r = projects.create(s, "g3", name="Failed", start=False, business=b)
    r.status, r.error = "FAILED", "x"
    s.flush()
    control.retry_project(s, r.id)
    jobs = {j.kind: j for j in s.scalars(select(Job).where(Job.business_id == b.id))}
    assert {k: (jobs[k].status, jobs[k].gate) for k in ("spec_approved", "resume_project", "retry_project")} == {
        k: ("queued", "direct") for k in ("spec_approved", "resume_project", "retry_project")}
    own = projects.create(s, "g4", name="Started by the company", business=b)
    assert s.scalar(select(Job.status).where(Job.kind == "run_project", Job.payload["project_id"].as_integer() == own.id)) \
        == "gated"
    s.rollback()


def test_find_now_runs_with_no_stage(s):
    """Probe D of the review: the owner's 'find now' enqueued like the schedule, so the gate would have skipped it."""
    b = kit.make_business(s, name="지금 찾기")
    _gate_shut(s, b)
    assert people_finder.request(s, b.id, lang="ko") == {"queued": {"find_people": True, "find_threads": True}}
    s.rollback()


def test_the_owners_approval_makes_its_to_do_and_an_agents_own_needs_the_stage(s):
    """Probe F7 of the review: the owner's approval and an automatic one reached the same to-do constructor."""
    b = kit.make_business(s, name="할일 관문")
    _gate_shut(s, b)
    p = projects.create(s, "g", name="Posting project", start=False, business=b)
    s.flush()
    by_owner = requests.create(s, post_draft(), from_agent="marketing", project_id=p.id, origin=by_project_manager(s, p.id))
    approve(s, by_owner)
    assert s.scalar(select(OwnerTask.kind).where(OwnerTask.request_id == by_owner.id)) == "post"
    auto = requests.create(s, post_draft(title="Show HN 다시"), from_agent="marketing", project_id=p.id,
                           origin=by_project_manager(s, p.id))
    auto.status = "AUTO_APPROVED"
    assert owner_tasks.create_for_request(s, auto) is None
    assert s.scalar(select(OwnerTask).where(OwnerTask.request_id == auto.id)) is None
    held = s.scalar(select(ActivityEvent).where(ActivityEvent.verb == "owner_task_held",
                                                ActivityEvent.business_id == b.id))
    assert held is not None and held.data["kind"] == "post"
    s.rollback()


def test_the_build_an_owner_approved_runs_with_no_stage_and_belongs_to_the_stage_that_runs(s):
    """The owner's approval is the approval: with no stage running the build chain runs by itself. With a stage
    running it is that stage's work, so stopping the stage stops it."""
    from justshipit.services import queue
    b = kit.make_business(s, name="승인 뒤 빌드")
    _gate_shut(s, b)
    p = projects.create(s, "g", name="Build project", start=False, business=b)
    s.flush()
    for kind in ("dev_job", "acceptance", "dev_merged"):
        j = queue.enqueue(s, kind, {"project_id": p.id}, idempotency_key=f"closed:{kind}", approved=True)
        assert (j.status, j.gate, j.stage_id) == ("queued", "direct", None), kind
    stray = queue.enqueue(s, "dev_job", {"project_id": p.id}, idempotency_key="closed:company")
    assert (stray.status, stray.gate) == ("gated", "stage")

    st = cycle_kit.stage(s, b)
    for kind in ("dev_job", "acceptance", "dev_merged"):
        j = queue.enqueue(s, kind, {"project_id": p.id}, idempotency_key=f"open:{kind}", approved=True)
        assert (j.status, j.gate, j.stage_id) == ("queued", "stage", st.id), kind
    s.rollback()


def test_a_build_of_a_stopped_stage_stays_parked_even_when_approved(s):
    from justshipit.services import queue
    b = kit.make_business(s, name="멈춘 단계 빌드")
    st = cycle_kit.stage(s, b, status="stopped")
    p = projects.create(s, "g", name="Stopped stage build", start=False, business=b)
    p.stage_id = st.id
    s.flush()
    j = queue.enqueue(s, "dev_job", {"project_id": p.id}, idempotency_key="stopped:dev", approved=True)
    assert (j.status, j.gate, j.stage_id) == ("gated", "stage", st.id)
    s.rollback()
