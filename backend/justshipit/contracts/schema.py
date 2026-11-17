"""Convert Pydantic models into strict JSON Schemas for Claude's structured-output mode (`--json-schema`).

Every property is listed in `required` and every object sets `additionalProperties: false`; optional values
are nullable instead. LLM-facing models therefore avoid free-form dict fields. `$ref`s are inlined because the
contracts are not recursive.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {"default", "title", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
              "minLength", "maxLength", "minItems", "maxItems", "pattern", "format", "$defs"}


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = walk(defs[node["$ref"].split("/")[-1]])
            if "description" in node:
                target = {**target, "description": node["description"]}
            return target
        out: dict[str, Any] = {}
        for key, val in node.items():
            if key in _DROP_KEYS:
                continue
            if key == "properties":
                out[key] = {name: walk(sub) for name, sub in val.items()}
            else:
                out[key] = walk(val)
        if out.get("type") == "object" or "properties" in out:
            out.setdefault("type", "object")
            out.setdefault("properties", {})
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return walk(raw)
