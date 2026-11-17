"""Every screen has a subject for team chat: each `<Route path>` in main.tsx has an entry in the console's route table,
and the pages that table names are the pages the server knows."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from justshipit.db.models import AgentMessage
from justshipit.i18n import msg
from justshipit.services import chat, chat_tags
from i18n_kit import WEB
from test_chat_store import HEAD, merge_card

MAIN = WEB / "main.tsx"
ROUTES = WEB / "chat" / "routes.ts"
SUBJECT = WEB / "chat" / "subject.tsx"
DOCK = WEB / "chat" / "Dock.tsx"


def _route_paths() -> list[str]:
    return re.findall(r'<Route\s+path="([^"]+)"', MAIN.read_text(encoding="utf-8"))


def test_every_route_has_a_chat_subject():
    table = set(re.findall(r'^\s*"([^"]+)":', ROUTES.read_text(encoding="utf-8"), re.MULTILINE))
    paths = _route_paths()
    assert paths, "no <Route path> found in main.tsx: the pattern this test reads has drifted"
    assert [p for p in paths if p not in table] == []


def test_the_pages_the_route_table_names_exist_on_the_server():
    named = re.findall(r'\bpage\("([a-z_]+)"\)', ROUTES.read_text(encoding="utf-8"))
    assert named, "no page(...) entry found in routes.ts"
    assert [p for p in named if p not in chat_tags.PAGES] == []


def test_the_console_and_the_server_list_the_same_pages():
    block = re.search(r"export const PAGES = \[(.*?)\] as const", SUBJECT.read_text(encoding="utf-8"), re.DOTALL)
    assert block, "PAGES not found in subject.tsx"
    assert set(re.findall(r'"([a-z_]+)"', block.group(1))) == set(chat_tags.PAGES)


def test_the_console_names_the_step_kind_the_server_accepts():
    declared = re.search(r"interface StepAbout \{ kind: \"([a-z_]+)\"", DOCK.read_text(encoding="utf-8"))
    assert declared, "StepAbout not found in Dock.tsx"
    assert declared.group(1) == chat.STEP


@pytest.fixture
def api(api_token):
    from justshipit.api.app import app
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_a_message_posted_with_a_step_keeps_it_and_one_the_build_never_ran_is_refused_in_words(s, project, api,
                                                                                               own_jobs_settled):
    card, _ = merge_card(s, project)
    subject = {"kind": "request", "ref": str(card.id)}
    about = {"kind": "walkthrough_step", "step": "JN1.1", "head": HEAD}

    sent = api.post("/api/chat/messages", json={"subject": subject, "body": "it printed nothing", "about": about})
    unknown = api.post("/api/chat/messages", json={"subject": subject, "body": "again", "about": {**about, "step": "JN7.7"}})

    assert sent.status_code == 200
    s.expire_all()
    assert s.scalar(select(AgentMessage.data).where(AgentMessage.id == sent.json()["message_id"])) == {"about": about}
    assert unknown.status_code == 422 and unknown.json()["detail"] == msg("chat.refuse.about_step", "en")
