"""Owner-edited settings: database overrides over file defaults, floors no setting can lower, and credentials
that never come back out."""
import json
import logging
import re
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest
from justshipit.api.app import app
from justshipit.contracts.requests import PolicyMode
from justshipit.db.models import ActionExecution, ActivityEvent, AgentRequest, AppSetting, Grant
from justshipit.db.session import session_scope
from justshipit.operations import gateway
from justshipit.services import app_settings, policy, secrets
from justshipit.settings import company_config, settings, tool_policy
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from ask_kit import contract, by_project_manager

ENGLISH_PHRASE = re.compile(r"[A-Za-z]{3,} [A-Za-z]{3,}")
TOKEN = "xoxb-1234567890-ABCDEFGHIJKLMNOP-wxyz"
APP_TOKEN = "xapp-1-A0123456789-1234567890-abcdef0123456789"


def korean(detail: str) -> bool:
    return bool(re.search("[가-힣]", detail)) and not ENGLISH_PHRASE.search(detail)


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        with session_scope(lease_checked=False) as s:
            s.execute(delete(AppSetting))
        for name in secrets.REGISTRY:
            secrets.clear(name)
    wipe()
    yield
    wipe()


@pytest.fixture
def console(api_token):
    return TestClient(app, base_url="http://127.0.0.1:8000",
                      headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})


def test_with_no_rows_the_effective_settings_are_the_files(console):
    resp = console.get("/api/settings")
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["overrides"] == {}
    assert body["effective"] == body["defaults"]
    assert body["effective"]["connectors"]["email"]["mode"] == company_config()["connectors"]["email"]["mode"]
    assert body["policy_modes"] == tool_policy()
    assert body["tool_policy"] == tool_policy()
    assert body["supported_modes"]["calendar"] == ["off", "outbox"]


def test_an_override_beats_the_file_one_field_at_a_time(console):
    r = console.put("/api/settings/usage", json={"day_usd_eq": 50, "owner_reserve_pct": 30})
    assert r.status_code == 200
    assert r.json()["overrides"] == {"day_usd_eq": 50.0, "owner_reserve_pct": 30}

    usage = app_settings.effective().usage
    assert (usage.day_usd_eq, usage.owner_reserve_pct) == (50.0, 30)
    assert (usage.week_usd_eq, usage.timezone) == (None, "Asia/Seoul")

    console.put("/api/settings/usage", json={})
    assert app_settings.effective().usage.day_usd_eq is None
    assert app_settings.stored() == {}


def test_the_owners_weekly_estimate_is_stored_and_the_caps_stay_in_usd(console):
    r = console.put("/api/settings/usage", json={"capacity_week_usd_eq": 400, "week_usd_eq": 200, "day_usd_eq": 56})
    assert r.status_code == 200, r.text
    assert r.json()["effective"]["capacity_week_usd_eq"] == 400.0
    assert app_settings.effective().usage.week_usd_eq == 200.0
    assert console.put("/api/settings/usage", json={"capacity_week_usd_eq": 0}).status_code == 422
    assert app_settings.effective().usage.capacity_week_usd_eq == 400.0


def test_the_settings_say_what_the_largest_work_of_each_kind_reserves(korean_owner, console):
    needs = console.get("/api/settings").json()["usage_needs"]
    assert set(needs) == {"owner", "background"}
    assert all(korean(n["what"]) and n["usd_eq"] > 0 for n in needs.values())
    reserve = app_settings.effective().usage.owner_reserve_pct
    smallest = max(needs["owner"]["usd_eq"], needs["background"]["usd_eq"] * 100 / (100 - reserve))
    refused = console.put("/api/settings/usage", json={"week_usd_eq": smallest - 0.5})
    assert refused.status_code == 422 and korean(refused.json()["detail"])
    assert console.put("/api/settings/usage", json={"week_usd_eq": smallest}).status_code == 200


@pytest.mark.parametrize("section, body", [
    ("usage", {"owner_reserve_pct": 80}),
    ("usage", {"timezone": "Mars/Base"}),
    ("usage", {"day_usd_eq": 0}),
    ("notifications", {"briefing_time": "9am"}),
    ("notifications", {"kinds": ["everything"]}),
    ("notifications", {"quiet_hours": {"start": "22:00", "end": "25:00"}}),
    ("connectors", {"email": {"mode": "maybe"}}),
    ("connectors", {"fax": {"mode": "off"}}),
])
def test_a_bad_value_is_refused_in_korean_and_stores_nothing(korean_owner, console, section, body):
    r = console.put(f"/api/settings/{section}", json=body)
    assert r.status_code == 422 and korean(r.json()["detail"]), r.text
    assert app_settings.stored() == {}


@pytest.mark.parametrize("connector, mode", [("calendar", "live"), ("survey", "live"), ("github", "live"),
                                             ("cloudflare", "live")])
def test_a_connector_without_a_live_branch_cannot_be_set_live(korean_owner, console, connector, mode):
    r = console.put("/api/settings/connectors", json={connector: {"mode": mode}})
    assert r.status_code == 422 and korean(r.json()["detail"]) and connector in r.json()["detail"], r.text
    assert app_settings.stored() == {}


def test_every_connector_has_a_declared_mode_table_that_includes_off_and_outbox():
    assert set(app_settings.SUPPORTED_MODES) == set(app_settings.ConnectorSettings.model_fields)
    for modes in app_settings.SUPPORTED_MODES.values():
        assert {"off", "outbox"} <= set(modes)


def test_a_deny_action_rises_only_to_strong_approve(korean_owner, console):
    assert tool_policy()["external.pii_share"] == "deny"
    for loose in ("auto", "approve"):
        r = console.put("/api/settings/policy", json={"overrides": {"external.pii_share": loose}})
        assert r.status_code == 422 and korean(r.json()["detail"]) and "external.pii_share" in r.json()["detail"]
    assert app_settings.stored() == {}

    assert console.put("/api/settings/policy", json={"overrides": {"external.pii_share": "strong_approve"}}
                       ).status_code == 200
    assert policy.action_mode("external.pii_share") == PolicyMode.STRONG_APPROVE


def test_no_override_makes_a_strong_approve_action_auto(korean_owner, console):
    assert tool_policy()["dev.merge"] == "strong_approve"
    r = console.put("/api/settings/policy", json={"overrides": {"dev.merge": "auto"}})
    assert r.status_code == 422 and korean(r.json()["detail"])
    assert policy.action_mode("dev.merge") == PolicyMode.STRONG_APPROVE


def test_an_override_can_tighten_or_loosen_an_approve_action_and_an_unknown_kind_is_refused(korean_owner, console):
    assert tool_policy()["external.email_send"] == "approve"
    console.put("/api/settings/policy", json={"overrides": {"external.email_send": "auto"}})
    assert policy.action_mode("external.email_send") == PolicyMode.AUTO
    console.put("/api/settings/policy", json={"overrides": {"external.email_send": "deny"}})
    assert policy.modes()["external.email_send"] == PolicyMode.DENY

    r = console.put("/api/settings/policy", json={"overrides": {"made.up": "auto"}})
    assert r.status_code == 422 and korean(r.json()["detail"])


def test_a_stored_row_below_the_floor_is_clamped_when_read():
    with session_scope(lease_checked=False) as s:
        s.add(AppSetting(section="policy", value={"overrides": {"external.pii_share": "auto", "dev.merge": "auto"}},
                         updated_by="test"))
    assert policy.action_mode("external.pii_share") == PolicyMode.STRONG_APPROVE
    assert policy.action_mode("dev.merge") == PolicyMode.APPROVE


def test_an_unknown_section_is_refused_in_korean(korean_owner, console):
    for path in ("/api/settings/nope", "/api/settings/secrets"):
        r = console.put(path, json={})
        assert r.status_code == 404 and korean(r.json()["detail"]), path


def _approved_email(s, project) -> int:
    r = AgentRequest(project_id=project.id, from_agent="sales", type="APPROVAL", title="mail", summary="s",
                     rationale="r", action_kind="external.email_send", status="APPROVED", policy_mode="approve",
                     action_params={"payload": {"to": ["c@example.com"], "subject": "제목", "body": "본문"}})
    s.add(r)
    s.flush()
    s.add(Grant(request_id=r.id, project_id=project.id, action_kind="external.email_send", max_uses=1,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24)))
    s.commit()
    return r.id


def test_a_connector_mode_change_takes_effect_on_the_next_call_without_a_restart(console):
    assert gateway._mode("email") == "outbox"
    console.put("/api/settings/connectors", json={"email": {"mode": "live"}})
    assert gateway._mode("email") == "live"
    console.put("/api/settings/connectors", json={"email": {"mode": "off"}})
    assert gateway._mode("email") == "off"
    console.put("/api/settings/connectors", json={})
    assert gateway._mode("email") == "outbox"


def test_an_off_connector_sends_nothing_and_a_retry_after_turning_it_on_sends(korean_owner, s, project, console):
    rid = _approved_email(s, project)
    console.put("/api/settings/connectors", json={"email": {"mode": "off"}})

    with pytest.raises(gateway.ConnectorError) as exc:
        gateway.execute_action(rid)
    assert korean(str(exc.value))
    with session_scope() as s2:
        row = s2.scalar(select(ActionExecution).where(ActionExecution.request_id == rid))
        assert row.status == "not_sent" and "꺼져 있어" in row.result_summary
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 0
    assert not list((settings().var_dir / "outbox" / "email").glob(f"req-{rid}*"))

    console.put("/api/settings/connectors", json={"email": {"mode": "outbox"}})
    gateway.execute_action(rid)
    with session_scope() as s2:
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "done"


def test_a_settings_change_is_recorded_for_the_owner_with_the_keys_and_values(console):
    console.put("/api/settings/connectors", json={"email": {"mode": "live"}})
    with session_scope() as s:
        ev = s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "settings")
                       .order_by(ActivityEvent.id.desc())).first()
        assert ev.actor == "owner" and ev.verb == "settings"
        assert ev.data["changes"] == [{"key": "connectors/email/mode", "from": "outbox", "to": "live"}]

    console.put("/api/settings/connectors", json={"email": {"mode": "live"}})
    with session_scope() as s:
        latest = s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "settings")
                           .order_by(ActivityEvent.id.desc())).first()
        assert latest.id == ev.id, "saving the same values again records nothing new"


def test_the_secret_is_stored_but_the_api_shows_only_set_and_last4(korean_owner, console, caplog):
    caplog.set_level(logging.DEBUG)
    r = console.put("/api/settings/secrets/slack_bot_token", json={"value": f"  {TOKEN}\n"})
    assert r.status_code == 200
    assert r.json() == {"label": "Slack 봇 토큰", "set": True, "last4": TOKEN[-4:]}
    assert secrets.get("slack_bot_token") == TOKEN

    listing = console.get("/api/settings")
    assert listing.json()["secrets"]["slack_bot_token"] == {"label": "Slack 봇 토큰", "set": True, "last4": TOKEN[-4:]}
    assert listing.json()["secrets"]["github_token"]["set"] is False
    gone = console.delete("/api/settings/secrets/slack_bot_token")
    assert gone.json()["set"] is False and secrets.get("slack_bot_token") is None

    everything = r.text + listing.text + gone.text + caplog.text
    assert TOKEN not in everything and TOKEN[:-4] not in everything


def test_a_short_secret_shows_no_last_characters(console):
    console.put("/api/settings/secrets/github_token", json={"value": "abcdef"})
    assert console.get("/api/settings").json()["secrets"]["github_token"]["last4"] is None


def test_a_secret_never_reaches_the_activity_log_or_a_refusal(korean_owner, console, caplog):
    caplog.set_level(logging.DEBUG)
    console.put("/api/settings/secrets/slack_app_token", json={"value": APP_TOKEN})
    console.delete("/api/settings/secrets/slack_app_token")
    with session_scope() as s:
        events = s.scalars(select(ActivityEvent).where(ActivityEvent.entity == "settings")).all()
        assert {e.verb for e in events} >= {"secret_set", "secret_cleared"}
        dump = json.dumps([(e.summary, e.data, e.actor) for e in events], ensure_ascii=False)
    assert APP_TOKEN not in dump and APP_TOKEN[-4:] not in dump

    bad = console.put("/api/settings/secrets/slack_app_token", json={"value": "   "})
    assert bad.status_code == 422 and korean(bad.json()["detail"])
    unknown = console.put("/api/settings/secrets/not_a_secret", json={"value": TOKEN})
    assert unknown.status_code == 404 and korean(unknown.json()["detail"]) and TOKEN not in unknown.text
    assert TOKEN not in caplog.text and APP_TOKEN not in caplog.text


@pytest.mark.parametrize("name, value", [
    ("slack_bot_token", "xoxb-ABCDEFéGHIJ"), ("slack_bot_token", "xoxb-AB\nCD"), ("slack_bot_token", "xoxb-AB CD"),
    ("slack_bot_token", "한글 토큰"), ("slack_bot_token", "xapp-1-A0123-4567890-abcdef"),
    ("slack_app_token", "xoxb-1234567890-ABCDEFGHIJKLMNOP-wxyz"), ("slack_app_token", "xapp-1-Aé"),
])
def test_a_slack_token_of_the_wrong_shape_is_refused_when_saved_and_never_stored(korean_owner, console, name, value):
    r = console.put(f"/api/settings/secrets/{name}", json={"value": value})

    assert r.status_code == 422 and any("가" <= c <= "힣" for c in r.json()["detail"])
    assert value not in r.text
    assert secrets.get(name) is None


def test_slack_tokens_of_the_right_shape_are_saved(console):
    assert console.put("/api/settings/secrets/slack_bot_token", json={"value": f" {TOKEN}\n"}).status_code == 200
    assert console.put("/api/settings/secrets/slack_app_token", json={"value": "xapp-1-A0123-4567890-abcdef"}).status_code == 200
    assert console.put("/api/settings/secrets/github_token", json={"value": "ghp_한글도"}).status_code == 200


def test_secret_names_are_a_fixed_registry_so_a_name_never_becomes_a_path():
    assert set(secrets.REGISTRY) == {"slack_bot_token", "slack_app_token", "gmail_oauth", "github_token",
                                     "cloudflare_api_token"}
    for evil in ("../api_token", "slack_bot_token/../../x", ""):
        with pytest.raises(secrets.SecretError):
            secrets.put(evil, TOKEN)
        with pytest.raises(secrets.SecretError):
            secrets.get(evil)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does not keep POSIX mode bits")
def test_the_secret_file_and_its_folder_are_owner_only():
    secrets.put("github_token", TOKEN)
    assert stat.S_IMODE((settings().var_dir / "secrets" / "github_token").stat().st_mode) == 0o600
    assert stat.S_IMODE((settings().var_dir / "secrets").stat().st_mode) == 0o700


def test_a_secret_write_that_fails_leaves_no_partial_file_and_no_value_in_the_error(monkeypatch):
    def broken(*a, **k):
        raise OSError(28, f"disk full while writing {TOKEN}")
    monkeypatch.setattr(secrets.os, "replace", broken)
    with pytest.raises(secrets.SecretError) as exc:
        secrets.put("github_token", TOKEN)
    assert TOKEN not in str(exc.value) and exc.value.__suppress_context__
    assert not [p for p in (settings().var_dir / "secrets").iterdir() if p.name.startswith(".github_token")]
    assert secrets.get("github_token") is None


def test_settings_writes_need_the_console_header_and_token(api_token):
    bare = TestClient(app, base_url="http://127.0.0.1:8000")
    assert bare.put("/api/settings/usage", json={"day_usd_eq": 1}).status_code == 401
    no_header = TestClient(app, base_url="http://127.0.0.1:8000", headers={"Authorization": f"Bearer {api_token}"})
    assert no_header.put("/api/settings/usage", json={"day_usd_eq": 1}).status_code in (400, 403)
    assert app_settings.stored() == {}


def _put_raw(console, path, raw: str):
    return console.put(path, content=raw, headers={"content-type": "application/json"})


@pytest.mark.parametrize("raw", ['{"day_usd_eq": Infinity}', '{"week_usd_eq": NaN}', '{"day_usd_eq": -Infinity}'])
def test_a_usage_cap_that_is_not_a_finite_number_is_refused(korean_owner, console, raw):
    r = _put_raw(console, "/api/settings/usage", raw)
    assert r.status_code == 422 and korean(r.json()["detail"]), r.text
    assert app_settings.stored() == {}


@pytest.mark.parametrize("body", [{"value": 123}, {"value": [TOKEN]}, [TOKEN], {"other": TOKEN}, TOKEN])
def test_a_malformed_secret_body_is_refused_in_korean_without_echoing_the_input(korean_owner, console, body):
    r = console.put("/api/settings/secrets/github_token", json=body)
    assert r.status_code == 422 and korean(r.json()["detail"]), r.text
    assert TOKEN not in r.text
    assert secrets.get("github_token") is None


def test_a_secret_with_a_lone_surrogate_is_refused_and_leaves_no_temp_file(korean_owner, console):
    r = _put_raw(console, "/api/settings/secrets/github_token", '{"value": "abc\\ud800def"}')
    assert r.status_code == 422 and korean(r.json()["detail"]), r.text
    with pytest.raises(secrets.SecretError):
        secrets.put("github_token", "abc\ud800def")
    assert not list((settings().var_dir / "secrets").glob(".*"))


def test_a_temp_file_is_removed_whatever_stops_the_write(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(secrets.os, "replace", broken)
    with pytest.raises(RuntimeError):
        secrets.put("github_token", TOKEN)
    assert not list((settings().var_dir / "secrets").glob(".*"))


def _corrupt(section, value):
    with session_scope(lease_checked=False) as s:
        s.add(AppSetting(section=section, value=value, updated_by="test"))


def test_a_corrupt_policy_row_does_not_break_reads_and_a_save_repairs_it(korean_owner, s, project, console):
    from justshipit.agents.executor import system_prompt
    from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
    from justshipit.services import requests
    _corrupt("policy", {"overrides": {"external.email_send": "deny", "external.pii_share": "auto",
                                      "dev.merge": "nonsense"}})

    r = console.get("/api/settings")
    assert r.status_code == 200
    problems = r.json()["problems"]
    assert [p["section"] for p in problems] == ["policy"] and korean(problems[0]["message"])
    assert r.json()["policy_modes"]["external.email_send"] == "deny", "a stricter saved entry is kept"
    assert r.json()["policy_modes"]["external.pii_share"] == "deny", "a looser one is dropped"
    assert r.json()["policy_modes"]["dev.merge"] == "strong_approve"

    draft = AgentRequestDraft(type=RequestType.APPROVAL, title="t", summary="s", rationale="r", options=[],
                              recommended_option=None, action_kind="external.survey_distribute",
                              estimated_cost_usd=None, risk_level=RiskLevel.LOW, context=contract())
    assert requests.create(s, draft, from_agent="project_manager", project_id=project.id,
                           origin=by_project_manager(s, project.id)).policy_mode == "approve"
    assert system_prompt("project_manager")

    fixed = console.put("/api/settings/policy", json={"overrides": {}})
    assert fixed.status_code == 200
    assert console.get("/api/settings").json()["problems"] == []


def test_a_corrupt_row_in_one_section_leaves_the_others_alone(console):
    console.put("/api/settings/usage", json={"day_usd_eq": 50})
    _corrupt("connectors", {"email": {"mode": "bogus"}})
    _corrupt("notifications", ["not", "a", "dict"])
    body = console.get("/api/settings").json()
    assert {p["section"] for p in body["problems"]} == {"connectors", "notifications"}
    assert body["effective"]["usage"]["day_usd_eq"] == 50.0
    assert body["effective"]["connectors"]["email"]["mode"] == "outbox"
    assert console.put("/api/settings/connectors", json={"email": {"mode": "off"}}).status_code == 200
    assert gateway._mode("email") == "off"


def test_a_file_value_the_code_does_not_know_never_stops_a_read_or_a_save(console, monkeypatch):
    cc = {**company_config(), "connectors": {"email": {"mode": "outbox"}, "calendar": {"mode": "live"},
                                              "crm": {"mode": "outbox"}}, "usage": {"bogus": 1}}
    monkeypatch.setattr(app_settings, "company_config", lambda: cc)
    body = console.get("/api/settings").json()
    messages = " ".join(p["message"] for p in body["problems"])
    assert {p["section"] for p in body["problems"]} == {"connectors", "usage"}
    assert "calendar" in messages and "crm" in messages and "bogus" in messages
    assert body["effective"]["connectors"]["calendar"]["mode"] == "outbox"
    assert console.put("/api/settings/connectors", json={"email": {"mode": "live"}}).status_code == 200
    assert gateway._mode("email") == "live"


def test_a_settings_read_that_fails_during_a_send_is_not_sent_and_refunded_then_retries(korean_owner, s, project, monkeypatch):
    rid = _approved_email(s, project)
    from sqlalchemy.exc import OperationalError

    def blip(name):
        raise OperationalError("select", {}, Exception("connection reset"))
    real = gateway._mode
    monkeypatch.setattr(gateway, "_mode", blip)

    with pytest.raises(gateway.ConnectorError) as exc:
        gateway.execute_action(rid)
    assert korean(str(exc.value))
    with session_scope() as s2:
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "not_sent"
        assert s2.scalar(select(Grant.uses).where(Grant.request_id == rid)) == 0

    monkeypatch.setattr(gateway, "_mode", real)
    gateway.execute_action(rid)
    with session_scope() as s2:
        assert s2.scalar(select(ActionExecution.status).where(ActionExecution.request_id == rid)) == "done"


def test_a_stored_invalid_connectors_row_does_not_block_a_send(s, project):
    rid = _approved_email(s, project)
    _corrupt("connectors", {"email": {"mode": "bogus"}})
    gateway.execute_action(rid)
    with session_scope() as s2:
        row = s2.scalar(select(ActionExecution).where(ActionExecution.request_id == rid))
        assert (row.status, row.mode) == ("done", "outbox")


def _pinned_email(s, project, pinned) -> int:
    rid = _approved_email(s, project)
    s.execute(Grant.__table__.update().where(Grant.request_id == rid).values(connector_mode=pinned))
    s.commit()
    return rid


def _set_email(console, mode):
    assert console.put("/api/settings/connectors", json={"email": {"mode": mode}}).status_code == 200


def _row(rid):
    with session_scope() as s2:
        row = s2.scalar(select(ActionExecution).where(ActionExecution.request_id == rid))
        return row.status, row.mode, s2.scalar(select(Grant.uses).where(Grant.request_id == rid))


def test_a_grant_pins_the_connector_mode_at_approval(s, project, console):
    from justshipit.contracts.requests import AgentRequestDraft, RequestType, RiskLevel
    from justshipit.services import requests
    _set_email(console, "off")
    draft = AgentRequestDraft(type=RequestType.APPROVAL, title="t", summary="s", rationale="r", options=[],
                              recommended_option=None, action_kind="external.email_send", estimated_cost_usd=None,
                              risk_level=RiskLevel.LOW, context=contract())
    rid = requests.create(s, draft, from_agent="project_manager", project_id=project.id, origin=by_project_manager(s, project.id)).id
    s.commit()
    assert console.post(f"/api/requests/{rid}/respond", json={"decision": "approve", "revision": 1}).status_code == 200
    with session_scope() as s2:
        assert s2.scalar(select(Grant.connector_mode).where(Grant.request_id == rid)) == "off"


@pytest.mark.parametrize("pinned, now", [("outbox", "live"), ("off", "outbox"), ("off", "live")])
def test_a_connector_opened_after_approval_refuses_the_send_and_asks_for_a_new_request(korean_owner,
        s, project, console, pinned, now):
    rid = _pinned_email(s, project, pinned)
    _set_email(console, now)
    with pytest.raises(gateway.ConnectorError) as exc:
        gateway.execute_action(rid)
    assert korean(str(exc.value)) and "다시 요청" in str(exc.value)
    assert _row(rid) == ("not_sent", "?", 0)
    assert not list((settings().var_dir / "outbox" / "email").glob(f"req-{rid}*"))


def test_a_connector_closed_after_approval_wins(s, project, console):
    rid = _pinned_email(s, project, "live")
    _set_email(console, "outbox")
    gateway.execute_action(rid)
    assert _row(rid) == ("done", "outbox", 1)

    rid2 = _pinned_email(s, project, "outbox")
    _set_email(console, "off")
    with pytest.raises(gateway.ConnectorError):
        gateway.execute_action(rid2)
    assert _row(rid2)[0] == "not_sent"


def test_turning_email_live_while_its_rule_is_automatic_warns_and_still_saves(console):
    console.put("/api/settings/policy", json={"overrides": {"external.email_send": "auto"}})

    tried = console.put("/api/settings/connectors", params={"dry_run": True}, json={"email": {"mode": "live"}}).json()
    assert tried["dry_run"] and [w["action"] for w in tried["warnings"]] == ["external.email_send"]
    assert tried["changed"] == [{"key": "connectors/email/mode", "from": "outbox", "to": "live"}]
    assert app_settings.effective().connectors.email.mode == "outbox", "a dry run saves nothing"

    saved = console.put("/api/settings/connectors", json={"email": {"mode": "live"}}).json()
    [w] = saved["warnings"]
    assert (w["kind"], w["connector"]) == ("sends_without_approval", "email") and w["text"]
    assert app_settings.effective().connectors.email.mode == "live", "the warning never blocks the save"

    again = console.put("/api/settings/connectors", json={"email": {"mode": "live"}}).json()
    assert again["warnings"] == [], "only a save that newly opens the way warns"


def test_making_the_email_rule_automatic_while_email_is_live_warns(console):
    console.put("/api/settings/connectors", json={"email": {"mode": "live"}})
    assert console.put("/api/settings/policy", json={"overrides": {"external.email_send": "approve"}}).json()["warnings"] == []
    r = console.put("/api/settings/policy", json={"overrides": {"external.email_send": "auto"}}).json()
    assert [w["action"] for w in r["warnings"]] == ["external.email_send"]


def test_email_live_with_approval_needed_does_not_warn(console):
    assert console.put("/api/settings/connectors", json={"email": {"mode": "live"}}).json()["warnings"] == []
