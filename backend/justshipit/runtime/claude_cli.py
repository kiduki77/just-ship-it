"""Claude Code headless runner (`claude -p`) using the owner's Claude subscription login.

Business agents run fully isolated: the default Claude Code system prompt, user settings,
MCP servers, skills and slash commands are excluded, which keeps a call around 1-2k tokens of
overhead instead of ~120k. Engineering calls keep every setting source: the user settings enable the pstack plugin
and the repo's CLAUDE.md loads (`--setting-sources user` drops it).
"""
from __future__ import annotations

import json
from pathlib import Path

from justshipit.runtime.base import (
    LLMCall, LLMError, LLMResult, Runner, SetupRefused, call_dir, default_timeout, resolve_bin,
    run_process, write_json,
)
from justshipit.runtime import host_auth
from justshipit.runtime.quota import is_quota_error
from justshipit.runtime.slots import provider_slot
from justshipit.sandbox import docker
from justshipit.settings import company_config, settings

CAPABILITY_TOOLS = {
    "web_search": ["WebSearch"],
    "web_fetch": ["WebFetch"],
    "read_workspace": ["Read", "Glob", "Grep"],
}
# What stands where a sandboxed call's output held the access token the server gave it.
REDACTED = "[access token removed]"
_TRANSIENT_MARKERS =("rate limit", "overloaded", "529", "503", "timeout", "ECONNRESET")


def _transient(said: str) -> bool:
    return any(m.lower() in said.lower() for m in _TRANSIENT_MARKERS)


def served_model(model_usage: dict | None, configured: str | None) -> str | None:
    """The model that did the work. The CLI lists every model it used, including cheap helpers for tool output,
    so the one that cost the most is the one that answered. When costs are missing or all zero they cannot
    tell the models apart, so the configured model wins, then the first one listed."""
    costs = {m: float((u or {}).get("costUSD") or 0) for m, u in (model_usage or {}).items()}
    if not costs or max(costs.values()) <= 0:
        return configured or next(iter(costs), None)
    return max(costs, key=costs.__getitem__)


def _rule_path(p: Path) -> str:
    """`//` plus the absolute path in POSIX form (a Windows drive C: becomes /c), the spelling a Claude Code permission rule
    uses for a path from the filesystem root."""
    s = p.resolve().as_posix()
    if s[1:2] == ":":
        s = f"/{s[0].lower()}{s[2:]}"
    return f"/{s}"


def host_deny_rules() -> list[str]:
    """Read rules (which also cover Glob and Grep) that keep every call off what the server keeps on this host:
    var/ (secrets, api_token, login_codes, calls), .env (the database password) and the owner's Claude
    login. Without them a call reads any absolute path its OS user can, whatever its workspace is. /proc is denied
    too: in the sandbox a call's own environment holds the access token, and a call that only reads code (an
    imported product's, the engineer's branch) must not be steered into copying it out. The engineer, which has
    Bash, can still read its own environment; its token is short-lived for that reason (runtime/host_auth.py)."""
    return [f"Read({_rule_path(settings().var_dir)}/**)", f"Read({_rule_path(settings().root / '.env')})",
            f"Read({_rule_path(host_auth.credentials_path())})", f"Read({_rule_path(Path.home() / '.claude.json')})",
            "Read(//proc/**)"]


def engineering_isolation() -> str:
    """Where calls that work in a workspace (the engineer, the code reviewer) run: "container" or "host". On the
    host the engineer's bypassPermissions Bash reads anything its OS user can, var/api_token and the owner's
    Claude login included, so the deny rules protect nothing there and host needs an explicit acknowledgement."""
    eng = company_config()["engineering"]
    isolation = eng.get("isolation", "container")
    if isolation == "host" and eng.get("unsafe_host_isolation_ack") is not True:
        raise SetupRefused("build.sandbox.host_refused")
    return isolation


def _in_workspace(call: LLMCall) -> bool:
    """The engineer, and the reviewer that reads the exported branch. Under container isolation each runs in the
    sandbox with only its workspace mounted, so neither can read this host's files."""
    return call.mode == "engineering" or "read_workspace" in call.capabilities


class ClaudeRunner(Runner):
    provider = "claude"

    def __init__(self) -> None:
        self.bin = resolve_bin("claude", settings().claude_bin)

    def build_args(self, call: LLMCall, system_prompt_file: str) -> list[str]:
        if call.mode == "engineering":
            args = [
                self.bin, "-p",
                "--output-format", "json",
                "--json-schema", json.dumps(call.output_schema, ensure_ascii=False),
                "--append-system-prompt-file", system_prompt_file,
                "--permission-mode", "bypassPermissions",
                "--strict-mcp-config",
                "--max-turns", str(call.max_turns),
            ]
            args += ["--disallowedTools", ",".join([*call.disallowed_tools, *host_deny_rules()])]
            if call.model:
                args += ["--model", call.model]
            return args
        tools = sorted({t for cap in call.capabilities for t in CAPABILITY_TOOLS[cap]})
        args = [
            self.bin, "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(call.output_schema, ensure_ascii=False),
            "--system-prompt-file", system_prompt_file,
            "--tools", ",".join(tools),
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--setting-sources", "",
            "--disallowedTools", ",".join(host_deny_rules()),
            "--max-turns", str(call.max_turns),
        ]
        if tools:
            args += ["--allowedTools", ",".join(tools)]
        if call.model:
            args += ["--model", call.model]
        return args

    def run(self, call: LLMCall) -> LLMResult:
        d = call_dir(call.call_id)
        sp = d / "system_prompt.md"
        sp.write_text(call.system_prompt, encoding="utf-8")
        (d / "prompt.md").write_text(call.prompt, encoding="utf-8")
        timeout = call.timeout_s or default_timeout()
        with provider_slot(self.provider):
            if _in_workspace(call) and engineering_isolation() == "container":
                code, out, err = self._run_in_container(call, d, timeout)
            else:
                proc = run_process(self.build_args(call, str(sp)), call.prompt, call.cwd or d, timeout)
                code, out, err = proc.returncode, proc.stdout, proc.stderr
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        (d / "stdout.json").write_text(stdout, encoding="utf-8")
        if stderr.strip():
            (d / "stderr.txt").write_text(stderr, encoding="utf-8")
        try:
            data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
        except json.JSONDecodeError:
            data = None
        if not data:
            text = (stderr or stdout)[-2000:]
            said = stderr + ("" if stdout.lstrip().startswith("{") else stdout)  # a cut-off JSON holds model text
            raise LLMError(f"claude returned no JSON (exit {code}): {text}", transient=_transient(said), raw=text,
                           quota=is_quota_error(said))
        if data.get("is_error") or data.get("structured_output") is None:
            text = json.dumps({k: data.get(k) for k in ("subtype", "result", "api_error_status",
                                                         "terminal_reason")}, ensure_ascii=False)
            # `result` is the CLI's error message only when it says the turn ended in error; otherwise it is the
            # model's own text, which may well discuss quotas, limits or timeouts.
            said = " ".join(str(data.get(k) or "") for k in ("api_error_status", "terminal_reason")
                            + (("result",) if data.get("is_error") else ()))
            raise LLMError(f"claude call failed: {text}", transient=_transient(said), raw=stdout,
                           quota=is_quota_error(said), cost_usd=float(data.get("total_cost_usd") or 0.0))
        usage = data.get("usage") or {}
        result = LLMResult(
            call_id=call.call_id,
            provider="claude",
            model=served_model(data.get("modelUsage"), call.model),
            output=data["structured_output"],
            input_tokens=int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_tokens=int(usage.get("cache_read_input_tokens", 0)),
            cost_usd=float(data.get("total_cost_usd") or 0.0),
            duration_ms=int(data.get("duration_ms") or 0),
            session_id=data.get("session_id"),
            num_turns=data.get("num_turns"),
            raw_path=str(d),
        )
        write_json(d / "output.json", result.output)
        return result

    def _run_in_container(self, call: LLMCall, d, timeout: int) -> tuple[int | None, bytes, bytes]:
        token = host_auth.access_token(min_valid_s=timeout + 120)
        if call.cwd is None:
            raise LLMError("engineering call without a workspace")
        box = company_config()["engineering"].get("sandbox", {})
        args = self.build_args(call, "/call/system_prompt.md")
        args[0] = "claude"
        spec = docker.ContainerSpec(
            image=box["image"], command=args,
            mounts=[docker.Mount(call.cwd, "/work", read_only=call.mode != "engineering"),
                    docker.Mount(d, "/call", read_only=True)],
            network="sandbox",
            memory=box.get("memory", "6g"), cpus=str(box.get("cpus", 4)), pids=int(box.get("pids", 2048)),
            env={"CLAUDE_CODE_OAUTH_TOKEN": token}, name_prefix="ship-eng",
        )
        try:
            res = docker.run(spec, stdin=call.prompt.encode("utf-8"), timeout_s=timeout)
        except docker.SandboxUnavailable as exc:
            raise LLMError(str(exc)) from exc
        if res.timed_out:
            raise LLMError(f"timed out after {timeout}s (container removed)", transient=True)
        # The container can read the token from its own environment and print it. The server knows the exact value,
        # so it leaves here before anything is written to var/calls, parsed, or kept in the database.
        secret, marker = token.encode("utf-8"), REDACTED.encode("utf-8")
        if not secret:
            return res.exit_code, res.stdout, res.stderr
        return res.exit_code, res.stdout.replace(secret, marker), res.stderr.replace(secret, marker)
