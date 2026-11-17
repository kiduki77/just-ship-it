"""A problem on this PC shows where the owner looks, with the command that fixes it: the checks, the home screen's
alerts, Settings' System list, the startup warnings and `ship doctor`. Docker and the host login are stubbed; the
database is the test one, at the code's head."""
import json
import logging
import time

import pytest
from justshipit import cli
from justshipit.api import console
from justshipit.api.app import app
from justshipit.db.models import Worker
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, catalog
from justshipit.runtime import host_auth
from justshipit.runtime.base import LLMError
from justshipit.sandbox import docker
from justshipit.services import queue, system_health
from fastapi.testclient import TestClient
from sqlalchemy import delete
from typer.testing import CliRunner

TOKEN = "sk-ant-oat01-NOT-FOR-ANY-OUTPUT"


@pytest.fixture(autouse=True)
def _pc_is_ready():
    """Overrides conftest's: these tests run the real checks."""
    system_health.forget()
    yield
    system_health.forget()


@pytest.fixture
def pc(monkeypatch, tmp_path):
    """A PC where every check passes. A test turns one thing off through the returned dict."""
    login = tmp_path / ".credentials.json"
    expires = int((time.time() + 3600) * 1000)
    login.write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN, "expiresAt": expires}}))
    state = {"docker": True, "image": True, "egress": True, "claude": True, "login": login}
    monkeypatch.setattr(docker, "docker_ok", lambda: state["docker"])
    monkeypatch.setattr(docker, "image_present", lambda image: state["image"])
    monkeypatch.setattr(docker, "egress_running", lambda: state["egress"])
    monkeypatch.setattr(host_auth, "credentials_path", lambda: state["login"])

    def resolve(name, override):
        if not state["claude"]:
            raise LLMError("`claude` CLI not found on PATH")
        return "claude"
    monkeypatch.setattr("justshipit.runtime.base.resolve_bin", resolve)
    with session_scope() as s:
        s.execute(delete(Worker))
        queue.worker_seen(s, "worker-health-test")
    yield state
    with session_scope() as s:
        s.execute(delete(Worker))


def states() -> dict[str, str]:
    return {r.id: r.state for r in system_health.run_all()}


def problem(check: str, lang="en") -> dict:
    return next(d for d in system_health.report(lang, results=system_health.run_all()) if d["id"] == check)


def system_alerts() -> list[dict]:
    return [a for a in console.alerts(include_test=True) if a["kind"] == "system_problem"]


def test_a_ready_pc_has_no_problem(pc):
    assert set(states().values()) == {"ok"}
    assert system_alerts() == []


def test_docker_off_is_one_problem_and_what_needs_it_is_not_checked(pc):
    pc["docker"] = False
    assert states() == {"docker": "problem", "image": "skipped", "egress": "skipped", "claude": "ok",
                        "database": "ok", "worker": "ok"}
    assert "Docker" in problem("docker")["problem"] and problem("docker")["fix"]


def test_a_missing_sandbox_image_says_how_to_build_it(pc):
    pc["image"] = False
    got = problem("image")
    assert got["state"] == "problem"
    assert "ship/engineer" in got["problem"] and "`uv run ship sandbox build engineer`" in got["fix"]


def test_the_stopped_egress_proxy_says_how_to_start_it(pc):
    pc["egress"] = False
    got = problem("egress")
    assert got["state"] == "problem" and "`docker compose up -d egress`" in got["fix"]
    assert "ship-egress" in got["problem"]


def test_a_missing_claude_cli_and_a_missing_login_are_told_apart(pc):
    pc["claude"] = False
    assert "npm i -g @anthropic-ai/claude-code" in problem("claude")["fix"]
    pc["claude"] = True
    pc["login"] = pc["login"].parent / "no-such-file.json"
    got = problem("claude")
    assert got["state"] == "problem" and "`claude`" in got["fix"] and "no-such-file.json" in got["problem"]


def test_a_host_isolated_engineer_needs_no_login_file(pc, monkeypatch):
    pc["login"] = pc["login"].parent / "no-such-file.json"
    cc = system_health.company_config()
    monkeypatch.setattr(system_health, "company_config",
                        lambda: {**cc, "engineering": {**cc["engineering"], "isolation": "host"}})
    assert states()["claude"] == "ok"


def test_a_database_behind_the_code_says_to_migrate_with_the_worker_stopped(pc, monkeypatch):
    monkeypatch.setattr("alembic.script.ScriptDirectory.get_heads", lambda self: ["f00dfeedbeef"])
    got = problem("database")
    assert got["state"] == "problem" and "f00dfeedbeef" in got["problem"]
    assert "Stop the background process" in got["fix"] and "`uv run alembic upgrade head`" in got["fix"]


def test_a_database_that_does_not_answer_is_a_problem_and_the_worker_is_not_checked(pc, monkeypatch):
    def down(**kw):
        raise ConnectionError("refused")
    monkeypatch.setattr("justshipit.db.session.session_scope", down)
    got = states()
    assert got["database"] == "problem" and got["worker"] == "skipped"
    assert "docker compose up -d" in problem("database")["fix"]


def test_no_worker_is_a_problem_with_the_command_to_start_one(pc):
    with session_scope() as s:
        s.execute(delete(Worker))
    got = problem("worker")
    assert got["state"] == "problem" and "`uv run ship supervise`" in got["fix"]


def test_a_check_that_hangs_is_reported_slow_and_does_not_hold_the_caller(pc, monkeypatch):
    monkeypatch.setattr(docker, "docker_ok", lambda: time.sleep(2) or True)
    began = time.monotonic()
    results = {r.id: r for r in system_health.run_all(deadline_s=0.3)}
    assert time.monotonic() - began < 1.5
    assert results["docker"].problem.code == "slow" and results["image"].state == "skipped"


def test_a_check_that_raises_is_a_problem_not_a_crash(pc, monkeypatch):
    def boom():
        raise RuntimeError("secret detail")
    monkeypatch.setattr(docker, "egress_running", boom)
    got = problem("egress")
    assert got["state"] == "problem" and "RuntimeError" in got["problem"] and "secret detail" not in got["problem"]


def test_the_korean_sentence_and_fix_come_with_the_same_command(pc):
    pc["egress"] = False
    got = problem("egress", "ko")
    assert "프록시" in got["problem"] and "`docker compose up -d egress`" in got["fix"]


def test_every_problem_has_a_sentence_and_a_fix_in_both_languages():
    keys = {k: v for k, v in catalog().items() if k.startswith("system.")}
    codes = {k.removeprefix("system.").rsplit(".", 1)[0] for k in keys if k.endswith((".problem", ".fix"))} - {"cli"}
    assert codes
    for code in codes:
        for part in ("problem", "fix"):
            assert set(keys[f"system.{code}.{part}"]) == set(LANGS)
    assert {c.id for c in system_health.CHECKS} <= {k.split(".")[1] for k in keys if k.endswith(".title")}


# ------------------------------------------------------------------ where it shows

def test_each_problem_is_a_bad_alert_with_its_fix_and_the_worker_has_only_its_own(pc):
    pc["egress"] = False
    pc["image"] = False
    with session_scope() as s:
        s.execute(delete(Worker))
    found = system_alerts()
    assert sorted(a["check"] for a in found) == ["egress", "image"]
    egress = next(a for a in found if a["check"] == "egress")
    assert egress["severity"] == "bad" and egress["project_id"] is None
    assert "`docker compose up -d egress`" in egress["detail"]
    assert [a["kind"] for a in console.alerts(include_test=True)].count("worker_down") == 1


def test_system_alerts_come_after_a_stopped_worker_and_before_the_rest(pc):
    pc["docker"] = False
    with session_scope() as s:
        s.execute(delete(Worker))
    kinds = [a["kind"] for a in console.alerts(include_test=True)]
    assert kinds.index("worker_down") < kinds.index("system_problem")


def test_the_settings_list_names_every_check_and_can_recheck_now(pc, api_token):
    client = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {api_token}"})
    body = client.get("/api/system").json()
    assert body["ok"] is True and [c["id"] for c in body["checks"]] == [c.id for c in system_health.CHECKS]
    pc["egress"] = False
    assert client.get("/api/system").json()["ok"] is True, "served from the last 30 seconds"
    body = client.get("/api/system?fresh=true").json()
    assert body["ok"] is False
    assert next(c for c in body["checks"] if c["id"] == "egress")["fix"] == "Run `docker compose up -d egress`."


def test_a_stale_run_is_served_while_a_new_one_refreshes_behind_the_caller(pc, monkeypatch):
    runs = []
    real = system_health.run_all

    def counted(*a, **k):
        runs.append(1)
        return real(*a, **k)
    monkeypatch.setattr(system_health, "run_all", counted)
    first = system_health.snapshot()
    assert system_health.snapshot() is first and len(runs) == 1
    monkeypatch.setattr(system_health, "CACHE_S", 0)
    assert system_health.snapshot() is first
    deadline = time.monotonic() + 5
    while (len(runs) < 2 or system_health._refreshing) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(runs) == 2


def test_startup_warns_once_per_failed_pc_check_and_leaves_the_worker_to_its_alert(pc, caplog):
    pc["egress"] = False
    with session_scope() as s:
        s.execute(delete(Worker))
    with caplog.at_level(logging.WARNING, logger=system_health.log.name):
        found = system_health.startup_problems("en")
    assert [d["id"] for d in found] == ["egress"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "docker compose up -d egress" in warnings[0].getMessage()


def test_the_terminal_block_after_startup_lists_the_problems_with_their_fixes(pc, capsys):
    pc["egress"] = False
    pc["image"] = False
    cli._print_problems()
    out = capsys.readouterr().out
    assert out.startswith("Problems on this PC:") and out.count("Fix:") == 2
    assert "docker compose up -d egress" in out and "ship sandbox build engineer" in out


def test_the_terminal_block_is_silent_when_nothing_is_wrong(pc, capsys):
    cli._print_problems()
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ ship doctor

def test_doctor_exits_0_on_a_ready_pc(pc):
    out = CliRunner().invoke(cli.app, ["doctor"])
    assert out.exit_code == 0, out.output
    assert out.output.count("✓") == len(system_health.CHECKS)


def test_doctor_exits_1_and_prints_the_fix_for_each_problem(pc):
    pc["docker"] = False
    out = CliRunner().invoke(cli.app, ["doctor"])
    assert out.exit_code == 1
    assert "✗ Docker" in out.output and "Fix:" in out.output
    assert "- Sandbox image: not checked, Docker comes first" in out.output


# ------------------------------------------------------------------ no token anywhere

def test_the_login_token_is_in_no_output(pc, caplog, capsys, api_token):
    pc["egress"] = False
    seen = [str(system_health.snapshot(fresh=True)), str(system_health.report("en")), str(system_health.alerts("ko"))]
    with caplog.at_level(logging.DEBUG):
        system_health.startup_problems("en")
        seen.append(CliRunner().invoke(cli.app, ["doctor"]).output)
        seen.append(TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {api_token}"})
                    .get("/api/system?fresh=true").text)
    seen += [r.getMessage() for r in caplog.records] + [capsys.readouterr().out]
    assert TOKEN in pc["login"].read_text()
    assert [text for text in seen if TOKEN in text or "accessToken" in text] == []


def test_a_burst_of_fresh_calls_runs_the_checks_once(pc, monkeypatch):
    """Each waiting caller held a thread and re-ran six checks; one that waited takes the run that finished after it
    arrived."""
    import threading
    runs = []
    real = system_health.run_all

    def slow(*a, **k):
        runs.append(1)
        time.sleep(0.4)
        return real(*a, **k)
    monkeypatch.setattr(system_health, "run_all", slow)
    out = []
    callers = [threading.Thread(target=lambda: out.append(system_health.snapshot(fresh=True))) for _ in range(8)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(10)
    assert len(out) == 8 and len(runs) <= 2, "the first caller runs; those that arrived during it share its result"


def test_a_fresh_call_after_a_finished_run_still_runs_the_checks_again(pc, monkeypatch):
    runs = []
    real = system_health.run_all
    monkeypatch.setattr(system_health, "run_all", lambda *a, **k: (runs.append(1), real(*a, **k))[1])
    system_health.snapshot(fresh=True)
    system_health.snapshot(fresh=True)
    assert len(runs) == 2


def test_a_worker_that_reports_in_is_not_called_ready_without_saying_what_waits(pc):
    """The demo's heartbeat said "Ready" while a job waited for minutes and nothing ran."""
    from datetime import datetime, timedelta, timezone
    from justshipit.db.models import Job
    with session_scope() as s:
        s.merge(Worker(id="note-host:1:abc", started_at=datetime.now(timezone.utc), seen_at=datetime.now(timezone.utc)))
        job = Job(kind="notify", gate="none", payload={"note_probe": True}, status="queued", attempts=0, max_attempts=1,
                  idempotency_key="worker-note-probe", created_at=datetime.now(timezone.utc) - timedelta(minutes=7),
                  run_after=datetime.now(timezone.utc) - timedelta(minutes=7))
        s.add(job)
        s.flush()
        jid = job.id
    try:
        row = next(c for c in system_health.report("en") if c["id"] == "worker")
        assert row["state"] == "ok"
        assert "shows only that it is running" in row["note"] and "waiting to start" in row["note"]
        assert "min" in row["note"]
    finally:
        with session_scope() as s:
            s.execute(delete(Job).where(Job.id == jid))
            s.execute(delete(Worker).where(Worker.id == "note-host:1:abc"))
