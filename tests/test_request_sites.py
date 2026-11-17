"""Every place in the backend that files a card names where it came from (docs/design/work-path.md 2.1), and every
origin and every request type has its row of rules, so a new call site or a new type cannot ship without one."""
import ast
from pathlib import Path

from justshipit.contracts.origins import ORIGIN_RULES, TYPE_RULES, AgentRule, OriginKind, ServerRule
from justshipit.contracts.requests import RequestType
from justshipit.contracts.work_path import ALLOWED_PAIRS, ExpiryPolicy, HoldKind, Rel

BACKEND = Path(__file__).resolve().parents[1] / "backend" / "justshipit"
CALLS = ("create", "from_agent_drafts")


def _sites() -> list[tuple[str, int, bool]]:
    """(file, line, passes origin=) for every call of requests.create / requests.from_agent_drafts, and for the bare
    calls inside services/requests.py itself."""
    found = []
    for path in sorted(BACKEND.rglob("*.py")):
        own = path.name == "requests.py" and path.parent.name == "services"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            named = (isinstance(f, ast.Attribute) and f.attr in CALLS and isinstance(f.value, ast.Name)
                     and f.value.id == "requests") or (own and isinstance(f, ast.Name) and f.id in CALLS)
            if named:
                found.append((str(path.relative_to(BACKEND)), node.lineno, any(k.arg == "origin" for k in node.keywords)))
    return found


def test_every_call_that_files_a_card_names_its_origin():
    sites = _sites()

    assert len(sites) >= 25, "the scan finds the call sites"
    assert [f"{f}:{n}" for f, n, ok in sites if not ok] == []


def test_every_origin_and_every_request_type_has_its_rules():
    assert set(ORIGIN_RULES) == set(OriginKind)
    assert set(TYPE_RULES) == set(RequestType)


def test_every_origin_links_what_the_database_accepts_and_no_server_row_defaults():
    for kind, rule in ORIGIN_RULES.items():
        assert rule.target in ALLOWED_PAIRS[Rel.RAISED_BY], kind
        if isinstance(rule, ServerRule):
            assert rule.expiry != ExpiryPolicy.DEFAULT, f"{kind}: the server never answers for the owner"
            assert rule.waits is None or rule.waits[1] in ALLOWED_PAIRS[Rel.HOLDS], kind
        else:
            assert isinstance(rule, AgentRule) and HoldKind.NOTHING in rule.may_hold, kind
