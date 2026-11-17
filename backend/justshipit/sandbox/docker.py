"""Throwaway Docker containers for untrusted work (engineer agent, analysis scripts).

Every container: --rm, no capabilities, no privilege escalation, pid/memory/cpu limits, and only the
explicitly listed host directories mounted. Secrets are passed by environment-variable *name* so their
values never appear on a command line or in logs. A timed-out container is force-removed, not orphaned.

A container that needs the network joins the internal ship-sandbox network, which has no route to the host or
the internet. Its one way out is the ship-egress proxy (docker-compose.yml), which reaches only the hosts in
docker/egress/allowlist: the host's Postgres and console API stay out of reach.
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from justshipit.i18n.said import SaidError
from justshipit.runtime import children
from justshipit.services import queue
from justshipit.settings import PROCESS_ID, settings

OWNER_LABEL = "ship.owner"
DEPLOYMENT_LABEL = "ship.deployment"
TEST_CONTAINER_PREFIX = "ship-test-"  # engineering test runs (engineering.worker.run_tests)
SANDBOX_NETWORK = "ship-sandbox"
EGRESS_PROXY = "ship-egress"
_PROXY = f"http://{EGRESS_PROXY}:3128"
PROXY_ENV = {"HTTPS_PROXY": _PROXY, "HTTP_PROXY": _PROXY, "https_proxy": _PROXY, "http_proxy": _PROXY,
             "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"}
CLI_TIMEOUT_S = 60  # a docker command that is not a container run: past this, Docker is not answering


@dataclass
class Mount:
    host: Path
    container: str
    read_only: bool = False


@dataclass
class ContainerSpec:
    image: str
    command: list[str]
    mounts: list[Mount] = field(default_factory=list)
    workdir: str = "/work"
    network: Literal["none", "sandbox"] = "none"
    memory: str = "2g"
    cpus: str = "2"
    pids: int = 512
    read_only_root: bool = False
    env: dict[str, str] = field(default_factory=dict)   # name -> value; passed by name only
    user: str | None = None        # "uid:gid"; None keeps the image's own user
    name_prefix: str = "ship"


@dataclass
class ContainerResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool


class SandboxUnavailable(SaidError):
    """No sandbox to run in. The owner reads why, in their language, where the job records it."""


def docker_ok() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def image_present(image: str) -> bool:
    try:
        return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                              timeout=CLI_TIMEOUT_S).returncode == 0
    except OSError:
        return False
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable("build.sandbox.docker_slow", s=CLI_TIMEOUT_S) from exc


def egress_running() -> bool:
    try:
        p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", EGRESS_PROXY], capture_output=True,
                           text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and p.stdout.strip() == "true"


_REFUSED = re.compile(r'Proxying refused on filtered domain "([^"\s]+)"')  # tinyproxy's line (docker/egress)


def refused_hosts(since_ms: int, until_ms: int) -> set[str]:
    """The hosts the egress proxy refused between two times (epoch milliseconds), from its log. Empty when the log
    could not be read, which proves no refusal."""
    try:
        p = subprocess.run(["docker", "logs", "--since", f"{since_ms / 1000:.3f}", "--until", f"{until_ms / 1000:.3f}",
                            EGRESS_PROXY], capture_output=True, timeout=CLI_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if p.returncode != 0:
        return set()
    return set(_REFUSED.findall((p.stdout + p.stderr).decode("utf-8", "replace")))


def unavailable(image: str, network: Literal["none", "sandbox"] = "sandbox") -> SandboxUnavailable | None:
    """Why a container of `image` on `network` cannot start right now, or None when it can: the checks `run` makes,
    in its order. An image that is not there is told apart from a Docker that is not running."""
    try:
        present = image_present(image)
    except SandboxUnavailable as exc:
        return exc
    if not present:
        return (SandboxUnavailable("build.sandbox.image_missing", image=image) if docker_ok()
                else SandboxUnavailable("build.sandbox.docker_down"))
    if network == "sandbox" and not egress_running():
        return SandboxUnavailable("build.sandbox.egress_down", proxy=EGRESS_PROXY)
    return None


def _env(spec: ContainerSpec) -> dict[str, str]:
    return {**spec.env, **(PROXY_ENV if spec.network == "sandbox" else {})}


def labels() -> list[str]:
    """The `docker run` labels that let the reaper find a container whose worker died: it removes the ones of
    this deployment whose owner holds no live lease."""
    return ["--label", f"{OWNER_LABEL}={PROCESS_ID}", "--label", f"{DEPLOYMENT_LABEL}={settings().deployment_id}"]


def build_args(spec: ContainerSpec, name: str) -> list[str]:
    args = ["docker", "run", "--rm", "-i", "--name", name, *labels(),
            "--network", SANDBOX_NETWORK if spec.network == "sandbox" else "none",
            "--memory", spec.memory, "--cpus", spec.cpus,
            "--pids-limit", str(spec.pids), "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-w", spec.workdir]
    if spec.user:
        args += ["--user", spec.user]
    if spec.read_only_root:
        args += ["--read-only", "--tmpfs", "/tmp:rw,size=512m"]
    for m in spec.mounts:
        args += ["-v", f"{m.host.resolve()}:{m.container}{':ro' if m.read_only else ''}"]
    for key in _env(spec):
        args += ["-e", key]
    return [*args, spec.image, *spec.command]


def run(spec: ContainerSpec, *, stdin: bytes = b"", timeout_s: int = 600) -> ContainerResult:
    if (down := unavailable(spec.image, spec.network)) is not None:
        raise down
    name = f"{spec.name_prefix}-{uuid.uuid4().hex[:10]}"
    env = {**os.environ, **_env(spec)}
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(key, None)
    lease = queue.current_lease.get()
    with lease.children.container(name) if lease else nullcontext():
        try:
            p = subprocess.run(build_args(spec, name), input=stdin, capture_output=True, timeout=timeout_s,
                               env=env, check=False, **children.GROUP)
            result = ContainerResult(p.returncode, p.stdout, p.stderr, False)
        except subprocess.TimeoutExpired as exc:
            remove([name])
            result = ContainerResult(None, exc.stdout or b"", exc.stderr or b"", True)
    if lease and lease.children.stopped:
        queue.check_lease()  # the worker removed the container to stop: hand the job back, not a failed run
    return result


def _listed(*filters: str) -> dict[str, str] | None:
    """This deployment's containers that pass the `docker ps` filters, mapped to the process that started them ("" if
    unlabeled). None when Docker did not answer."""
    try:
        p = subprocess.run(["docker", "ps", "-a", *filters,
                            "--filter", f"label={DEPLOYMENT_LABEL}={settings().deployment_id}",
                            "--format", f'{{{{.Names}}}}\t{{{{.Label "{OWNER_LABEL}"}}}}'],
                           capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return {name: owner for name, _, owner in (line.partition("\t") for line in p.stdout.splitlines())}


def containers(*name_prefixes: str) -> dict[str, str]:
    """This deployment's containers whose name starts with one of `name_prefixes`, mapped to the process that
    started them ("" if unlabeled). Empty when Docker is unavailable."""
    listed = _listed(*(f"--filter=name={n}" for n in name_prefixes)) or {}
    return {name: owner for name, owner in listed.items() if name.startswith(name_prefixes)}


def started_by(owner: str) -> list[str] | None:
    """Every container of this deployment that the process `owner` started, whatever it runs. None when Docker did not
    answer, which proves nothing about them."""
    listed = _listed("--filter", f"label={OWNER_LABEL}={owner}")
    return None if listed is None else sorted(name for name, by in listed.items() if by == owner)


def remove(names: list[str]) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", *names], capture_output=True, timeout=CLI_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass
