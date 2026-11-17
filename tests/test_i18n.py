"""The owner's language: catalogs complete in English and Korean, no Korean left outside them except where the
allowlist says a lane has not reached it yet, one glossary everyone reads, and the setting reaching every agent call."""
import re

import pytest
from justshipit.agents import executor
from justshipit.db.models import AppSetting
from justshipit.db.session import session_scope
from justshipit.i18n import LANGS, catalog, msg, particle, placeholders
from justshipit.i18n.glossary import TERMS, WEB_FILE, retired, web_source
from justshipit.services import app_settings
from i18n_kit import BACKEND, ROOT, allowlist, scanned, web_catalog
from pydantic import BaseModel
from sqlalchemy import delete


@pytest.fixture
def settings_row():
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting).where(AppSetting.section == "general"))
    wipe()
    yield
    wipe()


# ------------------------------------------------------------------ Korean outside the catalogs

def test_korean_owner_text_lives_only_in_the_catalogs_or_on_the_allowlist():
    left, allowed = scanned(), allowlist()
    assert sorted(set(left) - set(allowed)) == [], (
        "Korean text outside the catalogs. Move it to a catalog (docs/I18N.md, 'Converting a file'); "
        "`uv run python tests/i18n_kit.py` lists every line left")


def test_no_listed_file_holds_more_korean_than_its_count():
    from i18n_kit import over_count
    assert over_count(scanned(), allowlist()) == {}, (
        "Korean lines added to a listed file: move them to a catalog. A line's count only goes down")


def test_the_allowlist_only_names_files_that_still_hold_korean():
    assert sorted(set(allowlist()) - set(scanned())) == [], (
        "these files have no Korean left: delete their lines from tests/i18n_hangul_allowlist.txt")


# ------------------------------------------------------------------ catalogs

def _web_entries():
    return {key: entry for area in web_catalog().values() for key, entry in area.items()}


def test_every_console_entry_has_both_languages_and_lives_in_its_own_area():
    for area, entries in web_catalog().items():
        for key, entry in entries.items():
            assert set(entry) == set(LANGS), f"{area}.ts: {key} must be one line-group `\"key\": {{ en: ..., ko: ... }},`"
            assert key.startswith(f"{area}."), f"{area}.ts: {key} belongs in its own area"


@pytest.mark.parametrize("source", ["console", "server"])
def test_every_language_of_a_message_takes_the_same_values(source):
    entries = _web_entries() if source == "console" else catalog()
    for key, entry in entries.items():
        names = {lang: placeholders(entry[lang]) for lang in LANGS}
        assert len({frozenset(n) for n in names.values()}) == 1, f"{key}: {names}"
        for lang in LANGS:
            if isinstance(entry[lang], dict):
                assert set(entry[lang]) == {"one", "other"} and "n" in names[lang], f"{key}: a plural pair counts `n`"


@pytest.mark.parametrize("source", ["console", "server"])
def test_no_catalog_uses_a_word_the_glossary_retired(source):
    entries = _web_entries() if source == "console" else catalog()
    for key, entry in entries.items():
        for lang in LANGS:
            texts = entry[lang].values() if isinstance(entry[lang], dict) else [entry[lang]]
            for text in texts:
                hit = [w for w in retired(lang) if w.lower() in text.lower()]
                assert not hit, f"{key} ({lang}) says {hit}; the glossary's word is in backend/justshipit/i18n/glossary.py"


def test_every_message_key_the_server_names_exists():
    named = {m.group(1) for path in BACKEND.rglob("*.py")
             for m in re.finditer(r"""\bmsg\(\s*["']([a-z_]+\.[\w.]+)["']""", path.read_text(encoding="utf-8"))}
    assert sorted(named - set(catalog())) == []


# ------------------------------------------------------------------ the glossary

def test_the_console_copy_of_the_glossary_is_the_table():
    assert WEB_FILE.read_text(encoding="utf-8") == web_source(), (
        "web/src/i18n/areas/term.ts is stale: run `uv run python -m justshipit.i18n.glossary`")


def test_product_md_lists_every_term_in_both_languages():
    product = (ROOT / "PRODUCT.md").read_text(encoding="utf-8")
    for t in TERMS:
        for lang in LANGS:
            assert f"| {t.words[lang]} |" in product or f"| {t.words[lang]} (" in product, (t.id, lang)


@pytest.mark.parametrize(("lang", "drift"), [
    ("en", r"\b[Ww]orkers?\b"), ("ko", r"워커"),
    ("en", r"\b[Mm]onthly limit\b|\bcompany limit\b"), ("ko", r"월 예산|회사 한도|사용 상한"),
    ("en", r"^Runs$"), ("ko", r"^실행 기록$"),
])
def test_the_console_says_the_glossary_word_where_it_used_to_drift(lang, drift):
    """The screens called one thing by several names: the worker beside the background process, a monthly limit
    beside the monthly budget, Runs beside Model calls. The owner approved one word for each (glossary.py)."""
    hits = [key for key, entry in _web_entries().items() if key and not key.startswith("term.")
            for text in (entry[lang].values() if isinstance(entry[lang], dict) else [entry[lang]])
            if re.search(drift, text)]
    assert hits == []


def test_the_glossary_has_one_word_per_idea():
    for lang in LANGS:
        words = [t.words[lang] for t in TERMS]
        assert len(words) == len(set(words)), lang


# ------------------------------------------------------------------ formatting

@pytest.mark.parametrize(("word", "form", "expected"), [
    ("계획", "을", "을"), ("결정", "를", "을"), ("아이디어", "을", "를"), ("사업", "이", "이"), ("에이전트", "가", "가"),
    ("서울", "으로", "로"), ("부산", "로", "으로"), ("회사", "으로", "로"), ("12", "을", "를"), ("3", "은", "은"),
    ("Slack", "을", "을(를)"), ("메모)", "이에요", "예요"),
])
def test_a_korean_particle_fits_the_word_before_it(word, form, expected):
    assert particle(word, form) == expected


def test_a_message_fills_values_particles_and_english_plurals():
    assert msg("today.yesterday.people_found", "en", n=1) == "Found 1 person"
    assert msg("today.yesterday.people_found", "en", n=4) == "Found 4 people"
    assert msg("today.yesterday.people_found", "ko", n=4) == "사람 4명을 찾았어요"
    with pytest.raises(KeyError):
        msg("today.no_such_key", "en")


# ------------------------------------------------------------------ the setting and the agents

def test_the_language_defaults_to_english_and_takes_only_known_languages(settings_row):
    from pydantic import ValidationError
    assert app_settings.language() == "en"
    with session_scope() as s:
        app_settings.save(s, "general", {"language": "ko"}, actor="owner")
    assert app_settings.language() == "ko"
    with session_scope() as s, pytest.raises(ValidationError):
        app_settings.save(s, "general", {"language": "fr"}, actor="owner")


class _Out(BaseModel):
    ok: bool


@pytest.mark.parametrize("override", [None, "a prompt of the caller's own"])
def test_every_agent_call_ends_with_the_owners_language_whatever_prompt_it_uses(owner_asked, settings_row, monkeypatch, override):
    from justshipit.runtime import LLMResult
    sent: list[str] = []

    def model(call):
        sent.append(call.system_prompt)
        return LLMResult(call_id=call.call_id, provider=call.provider, model="m", output={"ok": True}, input_tokens=1,
                         output_tokens=1, cached_tokens=0, cost_usd=0.0, duration_ms=1, session_id=None)

    monkeypatch.setattr(executor, "run_with_retry", model)
    executor.run_agent("project_manager", "kickoff", "hello", _Out, project_id=None, system_override=override)
    with session_scope() as s:
        app_settings.save(s, "general", {"language": "ko"}, actor="owner")
    executor.run_agent("project_manager", "kickoff", "hello", _Out, project_id=None, system_override=override)

    english, korean = sent
    assert "The owner reads English." in english.rsplit("## Output language", 1)[1]
    assert "The owner reads Korean." in korean.rsplit("## Output language", 1)[1]
    assert "- Plan:" in english and "never call it 'work brief'" in english
    assert "- 계획:" in korean and "never call it '지시서'" in korean


def test_a_length_limit_is_in_korean_characters_and_stretches_for_english():
    from justshipit.contracts.owner_text import one_line
    korean, english = "가" * 80, "The CEO wants to start writing the spec now, so please approve it today."
    assert len(one_line(korean, 70)) <= 70
    assert one_line(english, 50) == english
