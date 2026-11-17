"""The owner pastes an install log into team chat to say what broke. Whatever secrets the log carries are gone before
the line is stored: the text says what was removed, and the row keeps only its kind and count."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from justshipit.services import chat

GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SLACK = "xoxb-" + "1234567890-abcdefghijKLMNOP"   # made up; split so secret scanners do not read it as a token
ANTHROPIC = "sk-ant-api03-" + "x" * 40
BEARER = "eyJhbGciOiJIUzI1NiJ9.payload.signature"

NPM_LOG = f"""npm ERR! code ERESOLVE
npm ERR! ERESOLVE unable to resolve dependency tree
npm ERR! While resolving: uxscope@0.1.0
npm ERR! Found: react@19.0.0
npm notice fetching with Authorization: Bearer {BEARER}
npm ERR! Could not resolve dependency: peer react@"^18" from react-day-picker@8.10.1
# my .env, in case it helps
GITHUB_TOKEN={GITHUB}
SLACK_BOT_TOKEN={SLACK}
ANTHROPIC_API_KEY={ANTHROPIC}
DATABASE_PASSWORD=hunter2-very-secret
PORT=3000
"""


def test_a_pasted_npm_log_keeps_the_error_and_loses_every_secret():
    text, removed = chat.scrub(NPM_LOG, "en")

    for secret in (GITHUB, SLACK, ANTHROPIC, BEARER, "hunter2-very-secret"):
        assert secret not in text
    assert "npm ERR! code ERESOLVE" in text
    assert 'peer react@"^18" from react-day-picker@8.10.1' in text
    assert "PORT=3000" in text, "a line that is not a secret stays"
    assert "GITHUB_TOKEN=[removed: GitHub token]" in text
    assert "Authorization: Bearer [removed: access token]" in text
    assert "DATABASE_PASSWORD=[removed: secret value]" in text
    assert sorted((x["kind"], x["n"]) for x in removed) == [
        ("api_key", 1), ("bearer", 1), ("env_secret", 1), ("github_token", 1), ("slack_token", 1)]


def test_the_marker_is_in_the_owners_language():
    text, _ = chat.scrub(f"token: {GITHUB}", "ko")

    assert text == "token: [지움: GitHub 토큰]"


def test_a_private_key_goes_whole():
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAABG5vbmUAAAAEbm9uZQ==\n-----END OPENSSH PRIVATE KEY-----"
    text, removed = chat.scrub(f"here it is\n{key}\nthanks", "en")

    assert text == "here it is\n[removed: private key]\nthanks"
    assert removed == [{"kind": "private_key", "n": 1}]


@pytest.mark.parametrize("plain", ["npm ERR! code ERESOLVE", "the key point is the install step", "password reset works",
                                   "KEY=", "Bearer of bad news", "see https://github.com/o/uxscope/pull/4",
                                   "write to owner@example.com", "use the --token flag", "run it with --keep-going",
                                   "our cookie policy", "Authorization: none"])
def test_plain_words_are_left_alone(plain):
    assert chat.scrub(plain, "en") == (plain, [])


@pytest.mark.parametrize(("pasted", "secret", "kept"), [
    ("DATABASE_URL=postgres://admin:s3cret-pw@db:5432/ship", "s3cret-pw", "postgres://admin:"),
    ("git clone https://x-access-token:v1.abcdef0123456789@github.com/o/r.git", "v1.abcdef0123456789",
     "https://x-access-token:"),
    ('{"user": "ship", "password": "hunter2-json"}', "hunter2-json", '"password": "'),
    ('{"apiToken":"tok_9f8e7d6c5b4a"}', "tok_9f8e7d6c5b4a", '"apiToken":"'),
    ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw==", "Authorization: Basic "),
    ("//registry.npmjs.org/:_authToken=npm_" + "a1B2c3D4" * 4 + "e5F6", "npm_" + "a1B2c3D4" * 4 + "e5F6",
     "//registry.npmjs.org/:_authToken="),
    ("deploy --token Zx9-4kq_LmN2pQ7 --env prod", "Zx9-4kq_LmN2pQ7", "--env prod"),
    ("psql --password=Hunter2pw now", "Hunter2pw", " now"),
    ("vault login --secret 8f14e45fceea167a", "8f14e45fceea167a", "vault login --secret "),
    ("Cookie: session=0a1b2c3d4e5f; theme=dark", "0a1b2c3d4e5f", "Cookie: "),
    ("my token was ghp_A1b2C3d4E5f6G7h8I9j0\nK1l2M3n4O5p6Q7r8 sorry", "K1l2M3n4O5p6Q7r8", " sorry"),
])
def test_secrets_in_other_shapes_go_too(pasted, secret, kept):
    text, removed = chat.scrub(pasted, "en")

    assert secret not in text and "[removed: " in text and kept in text
    assert removed and all(x["n"] >= 1 for x in removed)


def test_a_task_comment_reaches_the_activity_feed_scrubbed(s, project):
    from justshipit.db.models import ActivityEvent, Task
    from justshipit.services import control

    t = Task(project_id=project.id, key="t-scrub", title="t", objective="o", owner="research", status="READY",
             acceptance_criteria=[], depends_on=[])
    s.add(t)
    s.flush()
    control.comment_task(s, t.id, f"use this one: GITHUB_TOKEN={GITHUB}", lang="en")
    s.flush()

    event = s.scalar(select(ActivityEvent).where(ActivityEvent.entity == "task", ActivityEvent.entity_id == t.id,
                                                 ActivityEvent.verb == "comment"))
    assert GITHUB not in str(event.data) and "[removed: GitHub token]" in event.data["body"]
    s.rollback()


def test_the_stored_line_keeps_kinds_and_counts_never_the_value(s, project):
    from justshipit.db.models import AgentMessage
    from justshipit.services import chat_tags

    sent = chat.send(s, subject=chat_tags.resolve(s, "project", str(project.id), None), to_agent=None,
                     body=f"install fails\nGITHUB_TOKEN={GITHUB}", client_id=None, lang="en")
    s.flush()

    row = s.get(AgentMessage, sent["message_id"])
    assert GITHUB not in row.body and GITHUB not in str(row.data) and GITHUB not in str(row.scrubbed)
    assert row.scrubbed == [{"kind": "github_token", "n": 1}] == sent["scrubbed"]
    s.rollback()
