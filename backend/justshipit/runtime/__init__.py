"""LLM runtime: the Claude subscription CLI (`claude -p`) behind one interface."""
from __future__ import annotations

import time
from functools import lru_cache

from justshipit.runtime import quota
from justshipit.runtime.base import LLMCall, LLMError, LLMResult, Runner, default_timeout

RETRIES = 2


@lru_cache
def runner_for(provider: str) -> Runner:
    if provider == "claude":
        from justshipit.runtime.claude_cli import ClaudeRunner
        return ClaudeRunner()
    raise ValueError(f"unknown provider {provider}")


def _backoff_s(attempt: int) -> float:
    return 20.0 * (attempt + 1)


def longest_call_s(retries: int = RETRIES) -> float:
    """The longest `run_with_retry` can take: every attempt times out, with the pauses between them."""
    return (retries + 1) * default_timeout() + sum(_backoff_s(a) for a in range(retries))


def run_with_retry(call: LLMCall, retries: int = RETRIES) -> LLMResult:
    """The cost of every attempt lands on what is returned or raised: a retried attempt was spent too."""
    start = time.monotonic()
    failed_usd = 0.0
    for attempt in range(retries + 1):
        try:
            result = runner_for(call.provider).run(call)
            if not result.duration_ms:
                result.duration_ms = int((time.monotonic() - start) * 1000)
            result.cost_usd += failed_usd
            return result
        except LLMError as exc:
            failed_usd += exc.cost_usd
            if not exc.transient or attempt == retries:
                exc.cost_usd = failed_usd
                raise
            time.sleep(_backoff_s(attempt))
    raise AssertionError("unreachable")


__all__ = ["LLMCall", "LLMError", "LLMResult", "quota", "run_with_retry", "runner_for"]
