"""Provider-neutral interface for running one agent turn through a subscription CLI."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from justshipit.i18n.said import SaidError
from justshipit.runtime import children
from justshipit.settings import company_config, settings

Provider = Literal["claude"]
Capability = Literal["web_search", "web_fetch", "read_workspace"]


class LLMError(RuntimeError):
    """`quota` is the runner's reading of the CLI's own error, never of what the model wrote: a task about import
    quotas must not switch Claude off for every worker. `cost_usd` is what the CLI reported the failed call cost."""

    def __init__(self, message: str, *, transient: bool = False, raw: str = "", quota: bool = False,
                 cost_usd: float = 0.0):
        super().__init__(message)
        self.raw, self.quota, self.cost_usd = raw, quota, cost_usd
        self.transient = transient and not quota  # waiting seconds does not help a quota


class SetupRefused(SaidError, LLMError):
    """A call refused before it runs, for a reason the owner fixes on this PC (a login, a setting), said in the
    owner's language where the job records it."""


@dataclass
class LLMCall:
    agent: str
    system_prompt: str
    prompt: str
    output_schema: dict[str, Any]
    provider: Provider = "claude"
    model: str | None = None
    capabilities: list[Capability] = field(default_factory=list)
    cwd: Path | None = None
    max_turns: int = 30
    timeout_s: int | None = None
    # "isolated": no user settings/skills/MCP (business agents).
    mode: Literal["isolated", "engineering"] = "isolated"
    disallowed_tools: list[str] = field(default_factory=list)
    call_id: str = field(default_factory=lambda: f"call-{uuid.uuid4().hex[:12]}")


@dataclass
class LLMResult:
    call_id: str
    provider: Provider
    model: str | None
    output: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    duration_ms: int
    session_id: str | None
    num_turns: int | None = None
    raw_path: str | None = None


class Runner:
    provider: Provider

    def run(self, call: LLMCall) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError


def resolve_bin(name: str, override: str) -> str:
    if override:
        return override
    found = shutil.which(name)
    if not found:
        raise LLMError(f"`{name}` CLI not found on PATH")
    return found


# What the subscription CLIs need to start, find the owner's login and reach the network. The rest of the
# worker's environment stays out: service secrets, API keys that would move a CLI off the subscription, and a
# CLAUDE_CODE_OAUTH_TOKEN from the shell, which would move host calls off the login file that
# host_auth refreshes for the engineer container (the container gets its short-lived token by name, separately).
# Names are compared upper-cased because Windows environment names are case-insensitive.
_CHILD_ENV = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TZ", "LANG", "LANGUAGE", "TMPDIR", "TMP", "TEMP",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
})
_CHILD_ENV_PREFIXES = ("LC_", "XDG_")


def run_process(args: list[str], stdin: str, cwd: Path | None, timeout_s: int) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k.upper() in _CHILD_ENV or k.upper().startswith(_CHILD_ENV_PREFIXES)}
    env |= {"PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}
    from justshipit.services import queue  # the queue imports this package
    lease = queue.current_lease.get()
    # A timeout stops what the CLI started (tools, MCP servers) along with it.
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=str(cwd) if cwd else None, env=env, **children.GROUP)
    try:
        with lease.children.process(p) if lease else contextlib.nullcontext():
            out, err = p.communicate(stdin.encode("utf-8"), timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        children.stop_tree(p)
        p.wait()
        raise LLMError(f"timed out after {timeout_s}s", transient=True) from exc
    finally:
        for f in (p.stdin, p.stdout, p.stderr):
            f.close()
    if lease and lease.children.stopped:
        queue.check_lease()  # the worker ended the call to stop: hand the job back, not a failed call
    return subprocess.CompletedProcess(args, p.returncode, out, err)


def call_dir(call_id: str) -> Path:
    d = settings().var_dir / "calls" / time.strftime("%Y%m%d") / call_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_timeout() -> int:
    return int(company_config()["limits"]["llm_call_timeout_s"])
