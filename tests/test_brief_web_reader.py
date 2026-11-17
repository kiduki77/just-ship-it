"""What the project brief shows an agent that can open web addresses of its choosing: a hijacked one could send what
it reads away, so it gets no team chat. The role's tools decide, not its name."""
import pytest
from justshipit.db.models import Business
from justshipit.orchestration import context
from justshipit.services import chat, chat_tags, projects
from justshipit.settings import agents_config

CHAT = "### Team chat in the last 7 days"
SAID = "our supplier quoted 4,200 won a kilo, keep that between us"


@pytest.fixture
def chatted(s, own_jobs_settled):
    b = Business(slug=f"web-reader-{id(s)}", name="[TEST] web reader", one_liner="x")
    s.add(b)
    s.flush()
    project = projects.create(s, "find the market size", name="Web reader", budget_usd=5, start=False, business=b)
    chat.send(s, subject=chat_tags.resolve(s, "project", str(project.id), None), to_agent=None, body=SAID,
              client_id=None, lang="en")
    s.flush()
    yield project
    s.rollback()


def test_an_agent_that_opens_web_addresses_reads_no_team_chat(s, chatted):
    web_roles = {a for a, cfg in agents_config().items() if "web_fetch" in cfg.get("tools", [])}
    assert {"research", "marketing", "strategy", "product", "analyst", "skill_builder"} <= web_roles

    for role in sorted(web_roles):
        brief = context.project_brief(s, chatted, reader=role)
        assert CHAT not in brief and SAID not in brief, role
        assert "### The owner's goal\nfind the market size" in brief, "the rest of the brief stays"


def test_an_agent_without_that_tool_still_reads_team_chat(s, chatted):
    for role in (None, "project_manager", "finance", "risk"):
        assert SAID in context.project_brief(s, chatted, reader=role), role


def test_the_tools_decide_not_the_roles_name(s, chatted, monkeypatch):
    monkeypatch.setitem(agents_config()["finance"], "tools", ["web_search", "web_fetch"])
    monkeypatch.setitem(agents_config()["research"], "tools", ["web_search"])

    assert SAID not in context.project_brief(s, chatted, reader="finance")
    assert SAID in context.project_brief(s, chatted, reader="research")
