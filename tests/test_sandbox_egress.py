"""The sandbox network: engineer, reviewer and test containers reach only the egress proxy's allowlist, never the
host's Postgres or console API, and a workspace call never runs on the host without an acknowledgement."""
import subprocess
from pathlib import Path

import pytest
from justshipit.runtime import claude_cli, host_auth
from justshipit.runtime.base import LLMCall, LLMError
from justshipit.sandbox import docker
from justshipit.settings import company_config, settings


def _config(monkeypatch, **engineering):
    cfg = company_config()
    eng = {**cfg["engineering"], **engineering}
    monkeypatch.setattr(claude_cli, "company_config", lambda: {**cfg, "engineering": eng})


def _spec_of(monkeypatch, tmp_path, **call) -> docker.ContainerSpec:
    """The container spec ClaudeRunner starts for a call, captured instead of run."""
    _config(monkeypatch, isolation="container")
    monkeypatch.setattr(host_auth, "access_token", lambda min_valid_s: "at-secret")
    seen: list[docker.ContainerSpec] = []
    monkeypatch.setattr(claude_cli.docker, "run",
                        lambda spec, **kw: seen.append(spec) or docker.ContainerResult(0, b"", b"", False))
    runner = claude_cli.ClaudeRunner.__new__(claude_cli.ClaudeRunner)
    runner.bin = "claude"
    with pytest.raises(LLMError):  # the fake container prints no JSON
        runner.run(LLMCall(agent="a", system_prompt="s", prompt="p", output_schema={"type": "object"}, cwd=tmp_path,
                           **call))
    [spec] = seen
    return spec


def test_engineer_runs_on_the_sandbox_network_behind_the_proxy(monkeypatch, tmp_path):
    spec = _spec_of(monkeypatch, tmp_path, mode="engineering")
    args = " ".join(docker.build_args(spec, "n"))
    assert f"--network {docker.SANDBOX_NETWORK}" in args and "bridge" not in args
    assert "-e HTTPS_PROXY " in args and "-e HTTP_PROXY " in args and "ship-egress" not in args, "names only"
    assert "at-secret" not in args
    assert (spec.mounts[0].container, spec.mounts[0].read_only) == ("/work", False)


def test_the_code_reviewer_runs_in_the_sandbox_with_only_the_export_mounted_read_only(monkeypatch, tmp_path):
    spec = _spec_of(monkeypatch, tmp_path, capabilities=["read_workspace"])
    assert spec.network == "sandbox"
    work = spec.mounts[0]
    assert (work.host, work.container, work.read_only) == (tmp_path, "/work", True)
    assert spec.command[spec.command.index("--tools") + 1] == "Glob,Grep,Read"


def test_a_business_call_stays_on_the_host(monkeypatch, tmp_path):
    _config(monkeypatch, isolation="container")
    monkeypatch.setattr(claude_cli.docker, "run", lambda *a, **k: pytest.fail("business calls need no container"))
    monkeypatch.setattr(claude_cli, "run_process",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""))
    runner = claude_cli.ClaudeRunner.__new__(claude_cli.ClaudeRunner)
    runner.bin = "claude"
    with pytest.raises(LLMError, match="no JSON"):
        runner.run(LLMCall(agent="a", system_prompt="s", prompt="p", output_schema={}, capabilities=["web_search"]))


def test_host_isolation_is_refused_without_the_unsafe_acknowledgement(monkeypatch, tmp_path):
    ran: list = []
    monkeypatch.setattr(claude_cli, "run_process", lambda *a, **k: ran.append(a) or subprocess.CompletedProcess(
        a, 0, b"", b""))
    runner = claude_cli.ClaudeRunner.__new__(claude_cli.ClaudeRunner)
    runner.bin = "claude"
    call = LLMCall(agent="a", system_prompt="s", prompt="p", output_schema={}, mode="engineering", cwd=tmp_path)
    _config(monkeypatch, isolation="host")
    with pytest.raises(LLMError, match="unsafe_host_isolation_ack"):
        runner.run(call)
    assert ran == []
    _config(monkeypatch, isolation="host", unsafe_host_isolation_ack=True)
    with pytest.raises(LLMError, match="no JSON"):
        runner.run(call)
    assert len(ran) == 1


def test_every_claude_call_is_denied_the_env_file():
    runner = claude_cli.ClaudeRunner.__new__(claude_cli.ClaudeRunner)
    runner.bin = "claude"
    args = runner.build_args(LLMCall(agent="a", system_prompt="s", prompt="p", output_schema={},
                                     capabilities=["read_workspace"]), "sp.md")
    assert f"Read({claude_cli._rule_path(settings().root / '.env')})" in args[args.index("--disallowedTools") + 1]


def test_a_sandbox_container_is_not_started_while_the_egress_proxy_is_down(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(args, 1, "", "No such object: ship-egress")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(docker.subprocess, "run", fake_run)
    with pytest.raises(docker.SandboxUnavailable, match="docker compose up -d egress"):
        docker.run(docker.ContainerSpec(image="img", command=["true"], network="sandbox"))
    assert not any(a[:2] == ["docker", "run"] for a in calls)


@pytest.mark.parametrize("answers, why", [
    ({"image": 0, "info": 0, "inspect": 0}, None),
    ({"image": 0, "info": 0, "inspect": 1}, "egress_down"),
    ({"image": 1, "info": 0, "inspect": 0}, "image_missing"),
    ({"image": 1, "info": 1, "inspect": 1}, "docker_down"),
])
def test_the_sandbox_says_what_keeps_a_container_from_starting(monkeypatch, answers, why):
    def fake_run(args, **kw):
        rc = answers[args[1]]
        return subprocess.CompletedProcess(args, rc, "true" if rc == 0 else "", b"")

    monkeypatch.setattr(docker.subprocess, "run", fake_run)
    down = docker.unavailable("img")
    assert (down.said.key if down else None) == (f"build.sandbox.{why}" if why else None)
    if why == "egress_down":
        assert docker.unavailable("img", "none") is None, "a container with no network needs no proxy"


_ENGINEER_IMAGE = company_config()["engineering"]["sandbox"]["image"]
_PROBE = r"""
for t in host.docker.internal:55432 host.docker.internal:56379 host.docker.internal:8000; do
  timeout 4 bash -c "exec 3<>/dev/tcp/${t%:*}/${t#*:}" 2>/dev/null && echo "direct $t open" || echo "direct $t closed"
  curl -s -o /dev/null -m 10 -w "proxy $t %{http_code}\n" "http://$t/"
done
curl -s -o /dev/null -m 20 -w "proxy pypi %{http_code}\n" https://pypi.org/simple/six/
"""


@pytest.mark.skipif(not (docker.docker_ok() and docker.image_present(_ENGINEER_IMAGE) and docker.egress_running()),
                    reason="needs Docker, the engineer image and the ship-egress proxy (docker compose up -d egress)")
def test_a_sandbox_container_cannot_reach_the_hosts_services_but_reaches_pypi_through_the_proxy():
    res = docker.run(docker.ContainerSpec(image=_ENGINEER_IMAGE, command=["bash", "-c", _PROBE], network="sandbox"),
                     timeout_s=120)
    out = res.stdout.decode()
    for port in (55432, 56379, 8000):
        assert f"direct host.docker.internal:{port} closed" in out
        assert f"proxy host.docker.internal:{port} 403" in out, "the proxy refuses a host not on the allowlist"
    assert "proxy pypi 200" in out


def test_registry_read_stays_inside_the_registry(tmp_path):
    from justshipit.services import registry
    registry.ensure_repo()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (registry.root() / "projects" / "link").symlink_to(outside)
    for rel in ("../outside.txt", str(outside), "projects/link", "projects/../../outside.txt"):
        with pytest.raises(ValueError):
            registry.read(rel)
    assert registry.read("README.md").startswith("# AI Company Registry")


def test_var_and_the_secrets_folder_are_owner_only():
    from justshipit import fileperm
    from justshipit.services import secrets
    assert fileperm.owner_only(settings().var_dir), "settings() restricts var/ at startup"
    secrets.put("github_token", "ghp_" + "x" * 36)
    try:
        assert fileperm.owner_only(settings().var_dir / "secrets")
        assert fileperm.owner_only(settings().var_dir / "secrets" / "github_token")
    finally:
        secrets.clear("github_token")


def test_restrict_takes_group_and_other_access_away(tmp_path):
    from justshipit import fileperm
    d, f = tmp_path / "d", tmp_path / "f"
    d.mkdir(mode=0o777)
    f.write_text("x")
    f.chmod(0o666)
    assert not fileperm.owner_only(d) and not fileperm.owner_only(f)
    fileperm.restrict(d)
    fileperm.restrict(f)
    assert fileperm.owner_only(d) and fileperm.owner_only(f)
    assert Path(d).stat().st_mode & 0o777 == 0o700 and Path(f).stat().st_mode & 0o777 == 0o600
