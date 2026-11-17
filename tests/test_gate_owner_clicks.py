"""The owner's own buttons run at a shut gate. Each one below was parked `gated` with no sentence to the owner (review
probes A to E): the click answered "queued" and nothing ran until a stage the owner had not started would open."""
import bet_loop_kit as bet_kit
import cycle_kit
import pytest
from justshipit.contracts.handoff import Handoff
from justshipit.db.models import DevJob, Job, Project, Spec
from justshipit.engineering import revise, specs, worker
from justshipit.services import bet_loop, projects, roadmap
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("own_jobs_settled")


def _spec(s, p, *, key: str) -> Spec:
    h = Handoff(spec_id="SPEC-OC", project="Test", title="t", source={"path": "specs/oc", "commit_sha": "abc"},
                target={"repository": f"oc-{p.id}"}, approval={"spec_approved": True, "implementation_authorized": True})
    spec = Spec(spec_key=f"{key}-{p.id}", project_id=p.id, title="t", registry_path="specs/oc", approved_commit_sha="abc",
                handoff=h.model_dump(mode="json"), status="DELIVERED")
    s.add(spec)
    s.flush()
    return spec


def _build(s, name: str, status: str, **kw):
    """An approved plan with no stage running, a project of it, and a build of that project in `status`."""
    b = cycle_kit.business(s, name)
    p = projects.create(s, "g", name="P", start=False, business=b)
    spec = _spec(s, p, key=name[:2].upper())
    job = DevJob(spec_id=spec.id, status=status, attempt=1, steps=kw.pop("steps", []), result=kw.pop("result", {}), **kw)
    s.add(job)
    s.flush()
    s.commit()
    return b, p, spec, job


def _the_job(s, kind: str, **payload) -> Job:
    q = select(Job).where(Job.kind == kind)
    for k, v in payload.items():
        q = q.where(Job.payload[k].as_integer() == v)
    return s.scalar(q.order_by(Job.id.desc()))


def test_accept_again_runs_at_a_shut_gate(s):
    _, _, _, job = _build(s, "accept", "DELIVERED")
    worker.accept_again(job.id, "en")
    j = _the_job(s, "acceptance", dev_job_id=job.id)
    assert (j.status, j.gate) == ("queued", "direct")


def test_deliver_again_runs_at_a_shut_gate(s, monkeypatch):
    _, _, _, job = _build(s, "deliver", "FAILED")
    monkeypatch.setattr(worker, "_redelivery_refusal", lambda *a, **kw: None)
    worker.deliver_again(job.id, "en")
    j = _the_job(s, "dev_deliver", dev_job_id=job.id)
    assert (j.status, j.gate) == ("queued", "direct")


def test_a_new_attempt_the_owner_asked_for_runs_at_a_shut_gate(s, monkeypatch):
    """Retry build (specs.retry_dev) and Discard (worker.discard) both end in new_attempt."""
    _, _, spec, job = _build(s, "attempt", "FAILED")
    monkeypatch.setattr("justshipit.engineering.worker.branch_prs", lambda *a, **kw: [])
    new_id = specs.new_attempt(s, spec, "en")
    j = _the_job(s, "dev_job", dev_job_id=new_id)
    assert (j.status, j.gate) == ("queued", "direct")


def test_a_fix_round_the_owner_asked_for_runs_but_the_automatic_one_waits(s, monkeypatch):
    """revise.again (the owner's button) and merge_declined start a round the owner asked for; the round the company
    starts by itself after a failed check waits for a stage like any other build."""
    monkeypatch.setattr(revise, "_head", lambda *a, **kw: "abc")
    monkeypatch.setattr(revise, "shortfall", lambda *a, **kw: None)
    _, _, _, owned = _build(s, "owned", "DELIVERED")
    revise._begin(s, owned, "make it blue", None, "en", by_owner=True)
    _, _, _, auto = _build(s, "auto", "DELIVERED")
    revise._begin(s, auto, "the check failed", None, "en", auto=True)
    s.flush()
    ran = _the_job(s, revise.RUN, dev_job_id=owned.id)
    waits = _the_job(s, revise.RUN, dev_job_id=auto.id)
    assert (ran.status, ran.gate) == ("queued", "direct")
    assert waits.status == "gated"


def test_redo_retro_runs_at_a_shut_gate(s):
    """The console answered queued:true for a retro that sat gated."""
    b = cycle_kit.business(s, "retro")
    bet = bet_kit.make_bet(s, b, status="REVIEW")
    job = bet_loop.redo_retro(s, bet.id, lang="en")
    assert (job.status, job.gate) == ("queued", "direct")


def test_starting_a_bet_by_hand_runs_at_a_shut_gate(s):
    """The console's bet move is a button too: Running starts the bet's run, and moving it to review asks for its retro."""
    b = cycle_kit.business(s, "move")
    bet = bet_kit.make_bet(s, b, status="PICKED")
    roadmap.move_bet(s, bet.id, roadmap.BetStatus.RUNNING, lang="en", by_owner=True)
    s.flush()
    run = s.scalar(select(Project).where(Project.bet_id == bet.id))
    started = _the_job(s, "run_project", project_id=run.id)
    assert (started.status, started.gate) == ("queued", "direct")
    roadmap.move_bet(s, bet.id, roadmap.BetStatus.REVIEW, lang="en", by_owner=True)
    retro = _the_job(s, "bet_retro", bet_id=bet.id)
    assert (retro.status, retro.gate) == ("queued", "direct")


def test_a_bet_the_sweep_moves_waits_for_a_stage_like_other_company_work(s):
    b = cycle_kit.business(s, "sweep")
    bet = bet_kit.make_bet(s, b, status="RUNNING")
    roadmap.move_bet(s, bet.id, roadmap.BetStatus.REVIEW, lang="en")
    s.flush()
    assert _the_job(s, "bet_retro", bet_id=bet.id).status == "gated"
