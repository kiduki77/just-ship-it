"""One writer each: only services/cycle.py moves a stage, and the stage a job or project runs for is written only there
and in services/queue.py; only queue.enqueue builds a job row, so every job passes the gate."""
import re

from gate_sites import BACKEND

STATUS = re.compile(r"\b(?:st|stage|adopted|rest|nxt|first)\.status\s*=(?!=)|CycleStage\.status\s*=(?!=)")
STAGE_ID = re.compile(r"\.stage_id\s*=(?!=)")
JOB_ROW = re.compile(r"(?<![A-Za-z_])Job\(")


def _hits(pattern: re.Pattern, allowed: set[str]) -> list[str]:
    out = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in allowed or rel.startswith("db/"):
            continue
        out += [f"{rel}:{n}" for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                if pattern.search(line)]
    return out


def test_only_the_cycle_module_moves_a_stage():
    assert _hits(STATUS, {"services/cycle.py"}) == []


def test_only_the_cycle_module_and_the_queue_write_which_stage_work_belongs_to():
    assert _hits(STAGE_ID, {"services/cycle.py", "services/queue.py"}) == []


def test_only_enqueue_builds_a_job_row():
    assert _hits(JOB_ROW, {"services/queue.py"}) == []
