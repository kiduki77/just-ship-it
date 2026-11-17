"""The company lane's server text in both languages: an English owner reads no Korean, and the Korean an owner reads
passes the 번역체 lint. Each area is checked through its catalog and through the functions that build its sentences."""
import re
from datetime import datetime, timezone

import pytest
from justshipit.api import console
from justshipit.contracts.work import FinancialModelOut, FinancialScenarioOut
from justshipit.i18n import catalog, msg
from justshipit.services import app_settings, budget, secrets, usage
from justshipit.services.finance import run_model
from pydantic import ValidationError
from test_korean_style import smells_in

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")
AREAS = ("console", "settings", "usage", "budget", "queue", "operations", "factory", "finance")


def _texts(entry_lang) -> list[str]:
    return list(entry_lang.values()) if isinstance(entry_lang, dict) else [entry_lang]


def reads_in(text: str, lang: str) -> bool:
    """English with no Korean in it, or Korean with none of the lint's smells."""
    return not HANGUL.search(text) if lang == "en" else bool(HANGUL.search(text)) and not smells_in(text)


@pytest.mark.parametrize("area", AREAS)
def test_every_message_of_the_area_reads_in_its_language(area):
    entries = {k: e for k, e in catalog().items() if k.startswith(f"{area}.")}
    assert entries, area
    for key, entry in entries.items():
        for lang in ("en", "ko"):
            for text in _texts(entry[lang]):
                assert not HANGUL.search(text) if lang == "en" else not smells_in(text), (key, lang, text)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_console_job_names_and_failure_reasons_read_in_the_owners_language(lang):
    for kind in ("dev_job", "execute_action", "kpi_review", "bet_retro", "find_people"):
        assert reads_in(console.job_label(kind, lang), lang), kind
    assert console.job_label("no_such_kind", lang) == "no_such_kind"
    assert reads_in(console.failure_reason(None, lang), lang)
    assert reads_in(console.failure_reason("RuntimeError: x", lang), lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_usage_sentences_read_in_the_owners_language(lang):
    at = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    assert usage.local_time(at, "Asia/Seoul", lang) == ("Nov 5, 00:00 (KST)" if lang == "en" else "11월 5일 00:00 (KST)")
    for reason in ("usage_cap_day", "usage_cap_week", "subscription_limit", "github_rate_limit"):
        assert usage.reason_label(reason, lang) and not (lang == "en" and HANGUL.search(usage.reason_label(reason, lang)))
    for what, _ in usage.class_needs(lang).values():
        assert not (lang == "en" and HANGUL.search(what)), what
    cfg = app_settings.UsageSettings.model_construct(**{**app_settings.UsageSettings().model_dump(), "day_usd_eq": 0.5})
    refusals = usage.too_small(cfg, lang=lang)
    assert refusals and all(reads_in(r, lang) for r in refusals), refusals


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_settings_refusals_and_secret_problems_read_in_the_owners_language(lang):
    with pytest.raises(ValidationError) as bad:
        app_settings.NotificationSettings.model_validate({"briefing_time": "25:00"}, context={"lang": lang})
    assert reads_in(app_settings.describe(bad.value, lang), lang)
    problem = secrets.format_problem("slack_bot_token", "not-a-token")
    assert problem is not None and reads_in(problem.say(lang), lang)
    assert not HANGUL.search(str(problem)), "the exception's own text is English, for logs"
    for name in secrets.REGISTRY:
        assert not (lang == "en" and HANGUL.search(secrets.label(name, lang))), name


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_a_budget_stop_reads_in_the_owners_language(s, project, lang):
    project.spent_usd = project.budget_usd
    s.flush()
    assert reads_in(budget.stop_line(s, "project", project.id, lang=lang), lang)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_financial_table_reads_in_the_owners_language(lang):
    model = FinancialModelOut(currency="USD", scenarios=[FinancialScenarioOut(
        name="base", price_per_customer_month=10, customers_month_1=5, monthly_customer_growth_rate=0.1,
        monthly_churn_rate=0.05, variable_cost_per_customer_month=1, fixed_cost_month=500,
        acquisition_cost_per_customer=20, months=6, assumption_notes=["a guess"])])
    _, table, claims = run_model(model, lang)
    assert all(not HANGUL.search(x) for x in (table, *claims)) if lang == "en" else all(reads_in(x, lang) for x in claims)


def test_the_english_owners_default_reaches_code_that_names_no_language():
    """Without a language, the queue's and usage's times follow the setting, which is English by default."""
    from justshipit.services import queue
    at = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    assert not HANGUL.search(queue.local_time(at)) and not HANGUL.search(usage.local_time(at, "UTC"))
    assert msg("queue.busy.queued", "en", id=3, when="") == "Job #3 is already in the queue."
