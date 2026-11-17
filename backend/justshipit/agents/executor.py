"""Run one agent turn: compose prompts, reserve budget, call the runtime, record the ledger."""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from justshipit.agents.independence import Served
from justshipit.contracts.schema import strict_json_schema
from justshipit.db.models import LLMCallRecord
from justshipit.i18n import msg
from justshipit.i18n.prompting import output_rule
from justshipit.runtime import LLMCall, LLMError, quota, run_with_retry
from justshipit.services import app_settings, budget, channels, gate, policy, queue, strengths, tracing, usage
from justshipit.services.usage import MustWait
from justshipit.settings import agents_config, company_config, settings

T = TypeVar("T", bound=BaseModel)


class AgentFailed(RuntimeError):
    pass


@lru_cache
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _place(c: channels.Channel) -> str:
    return f"`{c.key}`: {' / '.join(dict.fromkeys(c.names.values()))}"


def _post_channels(*, writable: bool) -> str:
    """The places a post card may be made for, or the ones no card can be made for because their rules were not read.
    `other` is in neither: it is for the owner's own site, whose rules need no reading (`owner_tasks.build_post_card`)."""
    places = [c for c in channels.registry().values() if c.key != "other" and channels.writable(c) is writable]
    return "\n".join(f"- {_place(c)} ({c.mode})" for c in places) if writable else ", ".join(_place(c) for c in places)


def system_prompt(agent_id: str, include_skills: bool = True) -> str:
    cfg = agents_config()[agent_id]
    comp = company_config()["company"]
    preamble = _read(settings().prompts_dir / "company_preamble.md").format(
        company_name=comp["name"], mission=comp["mission"], today=date.today().isoformat(),
        principles="\n".join(f"- {p}" for p in comp["principles"]),
        action_kinds=", ".join(k for k, v in policy.modes().items() if v != "deny"),
        post_channels=_post_channels(writable=True), closed_channels=_post_channels(writable=False),
    )
    role = _read(settings().prompts_dir / "agents" / cfg["prompt"])
    return f"{preamble}\n\n{role}"


def with_owner_rules(system: str, *, engineering: bool = False) -> str:
    """The system prompt with what the owner is good at, then the owner's language rule last, so it outranks a
    language a prompt file names. Every call reads both here, whichever prompt file or override it used. A build in
    the engineer's container hands the owner no work and asks nothing, so it reads the language rule only."""
    owner = app_settings.effective()
    fits = "" if engineering else strengths.prompt_section(owner.owner.strengths)
    return "\n\n".join(x for x in (system, fits, output_rule(owner.general.language, engineering=engineering)) if x)


def prompt_version(system: str) -> str:
    """Hash of the full system prompt actually sent (role prompt + active skills)."""
    return hashlib.sha1(system.encode()).hexdigest()[:10]


_last_served: ContextVar[Served | None] = ContextVar("last_served", default=None)


def last_served() -> Served | None:
    """The provider and model that actually served the most recent run_agent call in this thread."""
    return _last_served.get()


_last_cost: ContextVar[float] = ContextVar("last_cost", default=0.0)
_last_call: ContextVar[str | None] = ContextVar("last_call", default=None)


def last_call_id() -> str | None:
    """The ledger id (`llm_calls.call_id`) of the most recent run_agent call in this thread."""
    return _last_call.get()


def last_cost() -> float:
    """What the most recent run_agent call in this thread cost in USD, as the CLI reported it (0 before any call)."""
    return _last_cost.get()


def _quota_wait(provider: str, until: float) -> MustWait:
    at = datetime.fromtimestamp(until, tz=timezone.utc)
    eff = app_settings.effective()
    lang = eff.general.language
    when = usage.local_time(at, eff.usage.timezone, lang)
    return MustWait(at, "subscription_limit", msg("usage.quota", lang, provider=provider, when=when), provider)


def _check_quota(provider: str) -> None:
    if (until := quota.down_until(provider)) is not None:
        raise _quota_wait(provider, until)


def preflight(agent_id: str, purpose: str) -> None:
    """MustWait if a call to this agent would have to wait now, for a job that cannot resume once started."""
    _check_quota(agents_config()[agent_id]["provider"])
    budget.gate(purpose)


def _approved_to_spend(agent_id: str, purpose: str) -> None:
    """The last line of the gate, at the point of spend: a job declared FREE never calls a model, and no call is made
    outside a job unless the owner asked for it in person (`gate.asked`). The check lives here rather than in
    `check_commit`, which every commit under a lease passes, FREE jobs' commits included."""
    lease = queue.current_lease.get()
    if lease is None:
        if gate.asked_why() is None:
            raise gate.GateViolation(f"{agent_id}/{purpose}: a model call outside any job, and nobody asked for it")
        return
    if lease.gate == "none":
        raise queue.GiveUp(f"{agent_id}/{purpose}: job #{lease.job_id} is declared FREE in gate.POLICY and tried to "
                           "spend model usage")


def run_agent(agent_id: str, purpose: str, prompt: str, output_model: type[T], *,
              project_id: int | None, business_id: int | None = None, task_id: int | None = None,
              dev_job_id: int | None = None, attempt: int = 1, cwd: Path | None = None, max_turns: int = 40,
              system_override: str | None = None, tools: list[str] | None = None, runtime: str | None = None,
              model_name: str | None = None) -> T:
    """`business_id` names the business a call outside any project works for, so its cost counts toward that
    business; a project's call takes its project's. `tools`, `runtime` and `model_name` replace the agent's configured
    ones for this one call: the CPO reads screens in a workspace, and the engineer answers team chat as an isolated
    reader ("isolated") instead of the writable engineering container."""
    _last_served.set(None)
    _last_cost.set(0.0)
    _last_call.set(None)
    queue.check_lease()
    _approved_to_spend(agent_id, purpose)
    queue.check_agent(agent_id)
    overrides = {k: v for k, v in (("tools", tools), ("runtime", runtime), ("model", model_name)) if v is not None}
    cfg = {**agents_config()[agent_id], **overrides}
    provider = cfg["provider"]
    _check_quota(provider)
    try:
        result, served_model = _run_once(agent_id, cfg, purpose, prompt, output_model,
                                         project_id=project_id, business_id=business_id, task_id=task_id,
                                         dev_job_id=dev_job_id,
                                         attempt=attempt, cwd=cwd, max_turns=max_turns,
                                         system_override=system_override)
    except AgentFailed as exc:
        cause = exc.__cause__
        if isinstance(cause, LLMError) and cause.quota:
            until = quota.parse_reset(str(cause))
            quota.mark_down(provider, until)
            raise _quota_wait(provider, until) from exc
        raise
    _last_served.set(Served(provider, served_model))
    return result


def _run_once(agent_id: str, cfg: dict, purpose: str, prompt: str,
              output_model: type[T], *, project_id: int | None, business_id: int | None, task_id: int | None,
              dev_job_id: int | None,
              attempt: int, cwd: Path | None, max_turns: int,
              system_override: str | None) -> tuple[T, str | None]:
    engineering = cfg.get("runtime") == "engineering"
    call = LLMCall(
        agent=agent_id,
        system_prompt=with_owner_rules(system_override or system_prompt(agent_id), engineering=engineering),
        prompt=prompt,
        output_schema=strict_json_schema(output_model),
        provider=cfg["provider"],
        model=cfg.get("model"),
        capabilities=list(cfg.get("tools", [])),
        cwd=cwd,
        max_turns=max_turns,
    )
    if engineering:
        call.mode = "engineering"
        call.disallowed_tools = list(company_config()["engineering"]["disallowed_tools"])
    # A shadow check names its task in the ledger but is paid from the project's audit budget, never the task's.
    charged = None if purpose == budget.AUDIT else task_id
    # Hold the budget only once the call is built: only the `finally` below releases a hold.
    reserved = budget.reserve(project_id, charged, purpose,
                              dev=dev_job_id is not None or purpose in budget.DEV_PURPOSES)
    lease = queue.current_lease.get()
    _last_call.set(call.call_id)
    record = LLMCallRecord(call_id=call.call_id, project_id=project_id, business_id=business_id, task_id=task_id,
                           stage_id=lease.stage_id if lease else None,
                           dev_job_id=dev_job_id, agent=agent_id, purpose=purpose, attempt=attempt,
                           provider=call.provider, model=call.model, prompt_version=prompt_version(call.system_prompt),
                           status="error")
    actual = 0.0
    output = None
    try:
        result = run_with_retry(call)
        output = result.output
        actual = result.cost_usd
        record.model = result.model
        record.input_tokens, record.output_tokens = result.input_tokens, result.output_tokens
        record.cached_tokens, record.cost_usd = result.cached_tokens, result.cost_usd
        record.duration_ms, record.raw_path = result.duration_ms, result.raw_path
        try:
            parsed = output_model.model_validate(result.output)
        except ValidationError as exc:
            record.error = f"schema validation: {exc}"[:4000]
            raise AgentFailed(f"{agent_id}/{purpose}: output failed validation") from exc
        record.status = "ok"
        return parsed, record.model
    except LLMError as exc:
        actual = record.cost_usd = exc.cost_usd
        record.error = str(exc)[:4000]
        raise AgentFailed(f"{agent_id}/{purpose}: {exc}") from exc
    finally:
        _last_cost.set(actual)
        budget.settle(project_id, charged, reserved, actual, record)
        tracing.record_call(agent=agent_id, purpose=purpose, provider=call.provider, model=record.model,
                            project_id=project_id, task_id=task_id, status=record.status,
                            input_tokens=record.input_tokens, output_tokens=record.output_tokens,
                            cost_usd=record.cost_usd, duration_ms=record.duration_ms,
                            prompt_version=record.prompt_version, call_id=call.call_id, error=record.error,
                            prompt=prompt, output=output)
