"""The record screens name every job kind and every model-call purpose in the owner's words, in both languages.

A job kind gates.POLICY allows, or a purpose usage.PURPOSES prices, that has no console label shows up as its code
("cycle_propose") on the background-work and model-call screens."""
from justshipit.services import gate, usage
from i18n_kit import web_catalog


def _entries() -> dict[str, dict]:
    return {k: e for area in web_catalog().values() for k, e in area.items()}


def _named(entry: dict | None) -> bool:
    return bool(entry) and all(entry.get(lang) for lang in ("en", "ko"))


def test_every_job_kind_has_a_console_name():
    entries = _entries()
    missing = sorted(k for k in gate.POLICY if not _named(entries.get(f"company.job.{k}")))
    assert not missing, f"add company.job.<kind> to web/src/i18n/areas/company.ts: {missing}"


def test_every_model_call_purpose_has_a_console_name():
    entries = _entries()
    missing = sorted(p for p in usage.PURPOSES
                     if not (_named(entries.get(f"label.purpose.{p}")) or _named(entries.get(f"audit.purpose.{p}"))))
    assert not missing, f"add label.purpose.<name> (label.ts) or audit.purpose.<name> (audit.ts): {missing}"
