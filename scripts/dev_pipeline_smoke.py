"""End-to-end smoke test of the engineering pipeline on a clearly labelled [TEST] project.

Approvals are given by a harness identity ("smoke-test-harness"), never on real business requests.
The target repository is local only (no remote), so nothing is pushed. Requires a running worker.

    uv run python scripts/dev_pipeline_smoke.py
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import select

from justshipit.contracts.requests import AgentRequestDraft, HumanDecision, HumanResponse, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, DevJob, Spec
from justshipit.db.session import session_scope
from justshipit.services import projects, requests

HARNESS = "smoke-test-harness"
NEED = {
    "title": "근거 원장 CSV 내보내기 CLI",
    "problem": "근거 원장(evidence) JSON Lines 파일을 사람이 엑셀에서 검토할 수 있는 CSV로 변환하고, 종류별 건수를 요약해야 한다.",
    "why_software": "반복 작업이며 CSV 이스케이프(쉼표·따옴표·줄바꿈·한글)를 손으로 하면 오류가 난다.",
    "expected_value": "검토 시간 단축, 형식 오류 제거",
    "priority": "P1",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for(fn, what: str, timeout_s: int = 5400, every: int = 10):
    start = time.time()
    while time.time() - start < timeout_s:
        if (v := fn()) is not None:
            return v
        time.sleep(every)
    raise TimeoutError(what)


def approve(req_id: int, strong: bool = False) -> None:
    with session_scope() as s:
        requests.respond(s, req_id, HumanResponse(
            decision=HumanDecision.APPROVE, responded_by=HARNESS, note="[TEST] smoke test harness approval",
            confirm_text=requests.display_id(req_id) if strong else None))
    log(f"approved {requests.display_id(req_id)} as {HARNESS}")


def main() -> int:
    if len(sys.argv) > 1:  # follow an existing dev job (e.g. a retry attempt)
        job_id = int(sys.argv[1])
        with session_scope() as s:
            pid = s.get(Spec, s.get(DevJob, job_id).spec_id).project_id
        return follow(pid, job_id)
    with session_scope() as s:
        p = projects.create(s, "[TEST] 개발 파이프라인 종단 검증용 프로젝트. 실제 사업 아님.", name="[TEST] 개발 파이프라인 검증",
                            slug="test-dev-pipeline", budget_usd=25, start=False)
        p.status, p.phase = "DONE", "done"
        p.plan = {"goal_interpretation": "개발 파이프라인 검증용 테스트 프로젝트", "deliverables": [], "success_criteria": []}
        dev = requests.create(s, AgentRequestDraft(
            type=RequestType.DEVELOPMENT, title=f"개발 제안: {NEED['title']}", summary=NEED["problem"],
            rationale="[TEST] smoke test", options=["PRD 작성 및 개발팀 접수", "반려"], recommended_option=None,
            action_kind="dev.implementation_start", estimated_cost_usd=None, risk_level=RiskLevel.LOW, blocking=False,
        ), from_agent="product", project_id=p.id, action_params={"need": NEED})
        pid, dev_id = p.id, dev.id
    log(f"created P-{pid}, {requests.display_id(dev_id)}")
    approve(dev_id)

    spec_req = wait_for(lambda: _one(select(AgentRequest.id).where(
        AgentRequest.project_id == pid, AgentRequest.action_kind == "dev.spec_approve")), "spec approval request")
    with session_scope() as s:
        spec = s.scalar(select(Spec).where(Spec.project_id == pid))
        log(f"CPO wrote {spec.spec_key} '{spec.title}' → products/{spec.target_repository} "
            f"(test: {spec.handoff['target'].get('test_command')} / image {spec.handoff['target'].get('test_image')})")
    approve(spec_req)

    job_id = wait_for(lambda: _one(select(DevJob.id).join(Spec).where(Spec.project_id == pid)), "dev job")
    return follow(pid, job_id)


def follow(pid: int, job_id: int) -> int:
    last = None
    while True:
        with session_scope() as s:
            job = s.get(DevJob, job_id)
            status, steps, err = job.status, list(job.steps), job.error
        if status != last:
            log(f"J-{job_id} {status}" + (f" — {steps[-1]['summary'][:160]}" if steps else ""))
            last = status
        if status in ("FAILED", "CLARIFICATION_REQUIRED"):
            log(f"stopped: {err or steps[-1]['summary']}")
            return 1
        if status in ("DELIVERED", "MERGED"):
            break
        time.sleep(15)

    merge_req = wait_for(lambda: _one(select(AgentRequest.id).where(
        AgentRequest.project_id == pid, AgentRequest.action_kind == "dev.merge",
        AgentRequest.status == "PENDING")), "merge request")
    with session_scope() as s:
        job = s.get(DevJob, job_id)
        acc = (job.result or {}).get("acceptance", {})
        log(f"CPO acceptance: accepted={acc.get('accepted')} "
            + "; ".join(f"{'✔' if c['met'] else '✘'} {c['criterion'][:60]}" for c in acc.get("criteria_results", [])))
    approve(merge_req, strong=True)
    merged = wait_for(lambda: True if _job_result(job_id).get("merged") else None, "merge", timeout_s=300, every=5)
    log(f"merged={merged}")
    return 0


def _one(q):
    with session_scope() as s:
        return s.scalar(q)


def _job_result(job_id: int) -> dict:
    with session_scope() as s:
        return dict(s.get(DevJob, job_id).result or {})


if __name__ == "__main__":
    sys.exit(main())
