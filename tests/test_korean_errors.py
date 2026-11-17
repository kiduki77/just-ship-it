"""Refusals the console shows the owner are Korean and name things with PRODUCT.md's terms (REQ-n, P-n)."""
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from justshipit.api.app import app
from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
from justshipit.db.models import AgentRequest, Job
from justshipit.engineering.specs import retry_dev
from justshipit.services import queue, requests
from lang_kit import owner_speaks
from sqlalchemy import update
from ask_kit import contract, by_project_manager

ENGLISH_PHRASE = re.compile(r"[A-Za-z]{3,} [A-Za-z]{3,}")


def korean(detail: str, *terms: str) -> bool:
    return (bool(re.search("[가-힣]", detail)) and not ENGLISH_PHRASE.search(detail)
            and all(t in detail for t in terms))


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def _request(s, project_id: int, **kw) -> int:
    base = dict(type=RequestType.APPROVAL, title="t", summary="s", rationale="r", options=[],
                recommended_option=None, action_kind=None, estimated_cost_usd=None, risk_level=RiskLevel.LOW,
                context=contract())
    r = requests.create(s, AgentRequestDraft(**{**base, **kw}), from_agent="project_manager", project_id=project_id,
                        origin=by_project_manager(s, project_id))
    s.commit()
    return r.id


def _respond(console, rid: int, **body):
    return console.post(f"/api/requests/{rid}/respond", json={"decision": "approve", "revision": 1, **body})


def test_request_refusals_are_korean_when_the_owner_reads_korean(s, project, console):
    with owner_speaks("ko"):
        _request_refusals(s, project, console)


def _request_refusals(s, project, console):
    missing = _respond(console, 10**8)
    assert missing.status_code == 409 and korean(missing.json()["detail"], f"REQ-{10**8}")

    done = _request(s, project.id)
    assert _respond(console, done).status_code == 200
    again = _respond(console, done)
    assert again.status_code == 409 and korean(again.json()["detail"], f"REQ-{done}")

    late = _request(s, project.id)
    s.get(AgentRequest, late).expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    s.commit()
    expired = _respond(console, late)
    assert expired.status_code == 409 and korean(expired.json()["detail"], f"REQ-{late}")

    strong = _request(s, project.id, action_kind="external.ads_run", estimated_cost_usd=100)
    unconfirmed = _respond(console, strong)
    assert unconfirmed.status_code == 409 and korean(unconfirmed.json()["detail"], f"REQ-{strong}")

    question = _request(s, project.id, type=RequestType.QUESTION)
    blank = _respond(console, question)
    assert blank.status_code == 409 and korean(blank.json()["detail"])


@pytest.mark.usefixtures("korean_owner")
def test_archive_refusals_are_korean(s, project, console):
    missing = console.post(f"/api/projects/{10**8}/archive")
    assert missing.status_code in (404, 409) and korean(missing.json()["detail"], f"P-{10**8}")
    job_id = queue.enqueue(s, "run_project", {"project_id": project.id}, max_attempts=1).id
    s.commit()
    try:
        busy = console.post(f"/api/projects/{project.id}/archive")
        assert busy.status_code == 409 and korean(busy.json()["detail"], f"P-{project.id}")
    finally:
        s.execute(update(Job).where(Job.id == job_id).values(status="done"))
        s.commit()


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/projects/{missing}", None),
    ("get", "/api/artifacts/{missing}", None),
    ("post", "/api/schedules/{missing}/toggle", None),
    ("post", "/api/schedules/{missing}/run-now", None),
    ("post", "/api/kpis", {"project_id": "{missing}", "name": "n", "target": 1}),
    ("post", "/api/kpis/{missing}/values", {"value": 1}),
    ("post", "/api/crm/contacts/{missing}/consent", {"consent": True}),
])
def test_missing_things_are_named_in_korean(korean_owner, console, method, path, body):
    missing = 10**8
    if body:
        body = {k: (missing if v == "{missing}" else v) for k, v in body.items()}
    r = getattr(console, method)(path.format(missing=missing), **({"json": body} if body else {}))
    assert r.status_code == 404, r.text
    assert korean(r.json()["detail"]), r.json()["detail"]


def test_dev_retry_refusals_are_korean():
    with pytest.raises(ValueError) as exc:
        retry_dev("S-nope", "ko")
    assert korean(str(exc.value), "S-nope")


PARTICLE_AFTER_VALUE = re.compile(r"\}(</b>)?(는|가|를|은|이|을)(?=[\s.,:!?'\"`]|$)")


def test_no_particle_is_glued_to_an_interpolated_value():
    """`P-1는` reads wrong (일 ends in a consonant): the right particle depends on the value, so messages
    put interpolated ids before a colon or a noun instead."""
    root = Path(__file__).resolve().parents[1]
    files = [*(root / "backend" / "justshipit").rglob("*.py"), *(root / "web" / "src").rglob("*.tsx")]
    hits = [f"{p.relative_to(root)}:{n}: {line.strip()}" for p in files
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if PARTICLE_AFTER_VALUE.search(line)]
    assert hits == []
