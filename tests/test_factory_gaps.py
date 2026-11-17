"""The skills page reads capability gaps from every task's result (`factory.gaps`). A result is the agent's JSON, so
a list, a string or a gap that is not text must be skipped, not crash the page (C1's report on the skills batch)."""
from contextlib import contextmanager

from justshipit.capability import factory


def _rows(monkeypatch, rows):
    class Result:
        def all(self):
            return rows

    class Session:
        def execute(self, _q):
            return Result()

    @contextmanager
    def scope():
        yield Session()

    monkeypatch.setattr(factory, "session_scope", scope)


def test_gaps_skip_results_that_are_not_objects(monkeypatch):
    _rows(monkeypatch, [
        ("research", {"capability_gaps": ["web search", " web search "]}),
        ("research", ["not", "an", "object"]),
        ("marketing", "a plain string"),
        ("marketing", {"capability_gaps": "one string, not a list"}),
        ("finance", {"capability_gaps": [None, 3, "", "spreadsheets"]}),
    ])
    assert factory.gaps() == [{"agent": "research", "gap": "web search", "count": 2},
                              {"agent": "finance", "gap": "spreadsheets", "count": 1}]
