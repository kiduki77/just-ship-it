"""Optional OpenTelemetry export of agent calls (GenAI semantic conventions).

Disabled unless SHIP_OTEL_ENDPOINT is set (environment or .env). For Langfuse, point it at
  https://<langfuse-host>/api/public/otel/v1/traces
and set SHIP_OTEL_HEADERS="Authorization=Basic <base64(public_key:secret_key)>".
Set SHIP_OTEL_ENDPOINT=console to print spans locally.
"""
from __future__ import annotations

import threading
from typing import Any

from justshipit.settings import settings

_lock = threading.Lock()
_tracer = None
_disabled = False


def tracer():
    global _tracer, _disabled
    if _tracer is not None or _disabled:
        return _tracer
    with _lock:
        endpoint = settings().otel_endpoint
        if not endpoint:
            _disabled = True
            return None
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor
        provider = TracerProvider(resource=Resource.create({"service.name": "ship"}))
        if endpoint == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            headers = dict(h.split("=", 1) for h in settings().otel_headers.split(",") if "=" in h)
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
        _tracer = provider.get_tracer("ship")
        return _tracer


def record_call(*, agent: str, purpose: str, provider: str, model: str | None, project_id: int | None,
                task_id: int | None, status: str, input_tokens: int, output_tokens: int, cost_usd: float,
                duration_ms: int, prompt_version: str | None, call_id: str, error: str | None = None,
                prompt: str | None = None, output: Any = None) -> None:
    t = tracer()
    if t is None:
        return
    import json
    import time
    end = time.time_ns()
    span = t.start_span(f"{agent}.{purpose}", start_time=end - duration_ms * 1_000_000)
    attrs = {
        "gen_ai.system": "anthropic",
        "gen_ai.request.model": model or "", "gen_ai.operation.name": purpose,
        "gen_ai.usage.input_tokens": input_tokens, "gen_ai.usage.output_tokens": output_tokens,
        "ship.agent": agent, "ship.project_id": project_id or 0, "ship.task_id": task_id or 0,
        "ship.cost_usd_equiv": cost_usd, "ship.prompt_version": prompt_version or "",
        "ship.call_id": call_id, "ship.status": status,
        # Langfuse maps these to the generation input/output view.
        "langfuse.observation.input": (prompt or "")[:20000],
        "langfuse.observation.output": json.dumps(output, ensure_ascii=False)[:20000] if output is not None else "",
        "langfuse.session.id": f"project-{project_id}" if project_id else "company",
    }
    for k, v in attrs.items():
        span.set_attribute(k, v)
    if error:
        from opentelemetry.trace import Status, StatusCode
        span.set_status(Status(StatusCode.ERROR, error[:500]))
    span.end(end_time=end)
