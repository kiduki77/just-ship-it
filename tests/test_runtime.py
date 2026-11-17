from justshipit.runtime.base import LLMCall
from justshipit.runtime.claude_cli import ClaudeRunner


def _args(**kw):
    r = ClaudeRunner.__new__(ClaudeRunner)
    r.bin = "claude"
    call = LLMCall(agent="a", system_prompt="s", prompt="p", output_schema={"type": "object"}, **kw)
    return r.build_args(call, "sp.md")


def test_business_agents_are_isolated_and_tool_restricted():
    args = _args(capabilities=["web_search"])
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--system-prompt-file"):
        assert flag in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert args[args.index("--tools") + 1] == "WebSearch"
    assert "--permission-mode" not in args


def test_engineering_mode_keeps_skills_but_applies_deny_rules():
    args = _args(mode="engineering", disallowed_tools=["Bash(git push:*)", "Skill(pstack:babysit)"])
    assert "--setting-sources" not in args and "--disable-slash-commands" not in args
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert "Bash(git push:*)" in args[args.index("--disallowedTools") + 1]


def test_engineering_deny_list_targets_pstack_plugin_skills():
    from justshipit.settings import company_config
    deny = company_config()["engineering"]["disallowed_tools"]
    args = _args(mode="engineering", disallowed_tools=deny)
    sent = args[args.index("--disallowedTools") + 1].split(",")
    for skill in ("Skill(pstack:babysit)", "Skill(pstack:fix-ci)", "Skill(pstack:setup-pstack)"):
        assert skill in sent
    assert not any(rule.startswith("Skill(pstack-") for rule in sent)


def test_quota_errors_are_parsed_and_not_retried_as_transient():
    from datetime import datetime

    from justshipit.runtime.base import LLMError
    from justshipit.runtime.quota import is_quota_error, parse_reset
    msg = "Claude usage limit reached. Your limit resets 3am"
    assert is_quota_error(msg)
    now = datetime(2026, 10, 30, 0, 20)
    assert datetime.fromtimestamp(parse_reset(msg, now)) == datetime(2026, 10, 30, 3, 0)
    assert datetime.fromtimestamp(parse_reset("limit reached, resets at 11pm", now)) == datetime(2026, 10, 30, 23, 0)
    assert parse_reset("Claude AI usage limit reached|1761789600") == 1761789600
    e = LLMError("claude call failed: usage limit", transient=True, quota=True)
    assert e.quota and not e.transient


def test_a_call_waits_while_the_claude_quota_is_down_and_never_reaches_another_provider(owner_asked, monkeypatch):
    import time

    import pytest

    from justshipit.agents import executor
    from justshipit.runtime import quota
    ran = []
    monkeypatch.setattr(executor, "_run_once", lambda *a, **kw: ran.append(a) or ("ok", "opus"))
    monkeypatch.setattr(executor.queue, "check_lease", lambda: None)
    assert executor.run_agent("project_manager", "plan", "p", str, project_id=None) == "ok"
    assert executor.last_served() == executor.Served("claude", "opus")
    quota.mark_down("claude", time.time() + 60)
    with pytest.raises(executor.MustWait) as wait:
        executor.run_agent("project_manager", "plan", "p", str, project_id=None)
    assert wait.value.reason == "subscription_limit"
    assert len(ran) == 1
    assert executor.last_served() is None


def test_the_served_model_is_the_one_that_cost_the_most():
    from justshipit.runtime.claude_cli import served_model
    usage = {"claude-haiku-4-5": {"costUSD": 0.01}, "claude-opus-4-5": {"costUSD": 0.9}}
    assert served_model(usage, "opus") == "claude-opus-4-5"
    assert served_model({"claude-haiku-4-5": {}, "claude-opus-4-5": {"costUSD": 0}}, "opus") == "opus"
    assert served_model({"claude-haiku-4-5": {}}, None) == "claude-haiku-4-5"
    assert served_model({}, "sonnet") == "sonnet" and served_model(None, None) is None


def test_orphaned_reservations_released_only_when_idle():
    from datetime import datetime, timezone

    from justshipit.db.models import BudgetMonth, Job
    from justshipit.db.session import session_scope
    from justshipit.services import budget

    budget.reserve(None, None, "dev")
    with session_scope() as s:
        job = Job(gate="direct", kind="noop", payload={}, status="running", locked_by="w-live", locked_at=datetime.now(timezone.utc))
        s.add(job)
        s.flush()
        job_id = job.id
    assert budget.release_orphaned_reservations() == 0.0
    with session_scope() as s:
        s.get(Job, job_id).status = "done"
    assert budget.release_orphaned_reservations() >= 4.0
    with session_scope() as s:
        assert s.get(BudgetMonth, budget._month()).reserved_usd == 0.0


def test_cli_children_get_only_the_environment_the_clis_need(monkeypatch):
    import json
    import sys

    from justshipit.runtime.base import run_process

    keep = {"PATH": "/usr/bin:/bin", "HOME": "/home/owner", "LANG": "ko_KR.UTF-8", "LC_ALL": "ko_KR.UTF-8",
            "TMPDIR": "/tmp/x", "https_proxy": "http://proxy:3128", "NO_PROXY": "localhost",
            "CLAUDE_CONFIG_DIR": "/home/owner/.claude",
            "SYSTEMROOT": "C:\\Windows", "APPDATA": "C:\\Users\\o\\AppData"}
    # A long-lived setup-token in the shell would move every host claude call off the login file and stop
    # the engineer's token refresh from ever touching that file.
    drop = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-long-lived",
            "SHIP_SMTP_PASSWORD": "smtp-secret", "SHIP_OTEL_HEADERS": "Authorization=Basic x",
            "SHIP_DATABASE_URL": "postgresql://u:p@h/db", "ANTHROPIC_API_KEY": "sk-ant-api",
            "OPENAI_API_KEY": "sk-openai", "AWS_SECRET_ACCESS_KEY": "aws", "GITHUB_TOKEN": "ghp", "CLAUDECODE": "1"}
    for k, v in {**keep, **drop}.items():
        monkeypatch.setenv(k, v)
    proc = run_process([sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"], "", None, 30)
    env = json.loads(proc.stdout)
    assert {k: env.get(k) for k in keep} == keep
    assert sorted(set(drop) & set(env)) == []


def _var_rules(args):
    return [r for r in args[args.index("--disallowedTools") + 1].split(",") if r.startswith("Read(")]


def test_every_claude_call_is_denied_var_and_the_owners_login():
    from pathlib import Path
    from justshipit.runtime import host_auth
    from justshipit.runtime.claude_cli import _rule_path
    from justshipit.settings import settings
    var = f"Read({_rule_path(settings().var_dir)}/**)"
    login = [f"Read({_rule_path(host_auth.credentials_path())})", f"Read({_rule_path(Path.home() / '.claude.json')})"]
    reviewer = _args(capabilities=["read_workspace"])
    business = _args(capabilities=["web_search"])
    engineer = _args(mode="engineering", disallowed_tools=["Bash(git push:*)"])
    for args in (reviewer, business, engineer):
        assert var in _var_rules(args) and all(r in _var_rules(args) for r in login)
    assert "Bash(git push:*)" in engineer[engineer.index("--disallowedTools") + 1]
    assert reviewer[reviewer.index("--tools") + 1] == "Glob,Grep,Read"


def test_the_deny_rule_is_an_absolute_path_rule(tmp_path):
    from justshipit.runtime.claude_cli import _rule_path
    rule = _rule_path(tmp_path)
    assert rule.startswith("//") and "\\" not in rule and ":" not in rule


def test_a_call_that_reads_code_cannot_read_its_own_environment():
    """A read-only call in the sandbox holds the short-lived access token in its environment. Code it reads (an
    imported product's, for its spec) must not be able to steer it into /proc/self/environ and copy the token into a
    document the registry keeps."""
    reviewer = _args(capabilities=["read_workspace"])
    assert "Read(//proc/**)" in _var_rules(reviewer)
