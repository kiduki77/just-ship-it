"""An agent goes by one name on every owner screen, the glossary's (labels.ts agentTitle/agentName). Home said
'마지막 실행은 Engineering Reviewer' while the sidebar said 코드 리뷰어: the page rendered the config's English title."""
import re
from pathlib import Path

from justshipit.settings import agents_config
from i18n_kit import web_catalog

WEB = Path(__file__).resolve().parents[1] / "web" / "src"


def _glossary() -> set[str]:
    """Agents the `label` catalog names (`label.agent.<id>`); test_i18n checks each entry has both languages."""
    return {key.removeprefix("label.agent.") for key in web_catalog()["label"] if key.startswith("label.agent.")}


def test_every_configured_agent_has_a_name_in_both_languages():
    assert sorted(set(agents_config()) - _glossary()) == []


def _raw_names(text: str) -> list[str]:
    """JSX text that shows `.title` of a row the same file links as an agent (`/agents/${a.id}`), by line. A
    `title={a.title}` tooltip is the English subtitle the glossary keeps, so only rendered text counts."""
    agents = set(re.findall(r"/agents/\$\{(\w+)\.id\}", text))
    return [f"{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}"
            for name in agents for m in re.finditer(rf"(?<![=\w$]){{{name}\.title}}", text)]


def test_no_page_renders_an_agents_config_title():
    hits = [f"{path.relative_to(WEB)}:{hit}" for path in sorted(WEB.rglob("*.tsx"))
            for hit in _raw_names(path.read_text(encoding="utf-8"))]
    assert hits == []


def test_the_lint_catches_the_home_line_it_was_written_for():
    page = ("<Link to={`/agents/${last.id}`}>{last.title}</Link>\n<span title={last.title}>{agentTitle(last)}</span>\n"
            "<a title={`${last.title}: 일하는 중`}>{agentTitle(last)}</a>")
    assert _raw_names(page) == ["1: {last.title}"]


def test_the_server_names_an_agent_as_the_console_does():
    """The task document read 'Review: 리스크·감사 → unmet' on an English screen: the server wrote the agent's id and
    the console glossed it into Korean. The server now names agents from the glossary's ROLES, in the owner's language,
    and those names must be the console's."""
    from justshipit.i18n.glossary import ROLES
    label = web_catalog()["label"]
    assert sorted(set(agents_config()) - set(ROLES)) == []
    assert {k: v for k, v in ROLES.items() if label[f"label.agent.{k}"] != v} == {}


def test_a_department_has_one_name_on_every_screen():
    """The Agents page said 'Strategy & growth' and the org chart 'Business / 사업 본부' for one department, and
    Business is the owner's business (B-n). Both screens read `company.dept.<id>`, which says what the glossary says."""
    from justshipit.i18n.glossary import DEPARTMENTS, TERMS
    company = web_catalog()["company"]
    assert sorted({v.get("department") for v in agents_config().values()} - set(DEPARTMENTS)) == []
    assert {k: v for k, v in DEPARTMENTS.items() if company[f"company.dept.{k}"] != v} == {}
    assert [k for k in company if re.fullmatch(r"company\.org\.dept\.\w+", k)] == [], "a second set of names"
    words = {t.words[lang] for t in TERMS for lang in t.words}
    assert [v for names in DEPARTMENTS.values() for v in names.values() if v in words] == []
