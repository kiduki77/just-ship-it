"""The analyst reads its own project's ledger and task outputs (no owner upload), but never another project's."""
from pathlib import Path

import pytest

from justshipit.analysis import runner
from justshipit.db.models import Artifact, Evidence, Task
from justshipit.services import projects, registry


def test_ledger_exports_own_records_only(s, project, tmp_path: Path):
    other = projects.create(s, "다른 목표", name="Other", budget_usd=1, start=False)
    s.add(Evidence(project_id=project.id, agent="finance", claim="객단가 $29 가정", kind="assumption", quote="29"))
    s.add(Evidence(project_id=other.id, agent="finance", claim="남의 기록", kind="assumption"))
    rel = f"projects/{project.slug}/tasks/T-ledger-test.md"
    registry.write_files({rel: "# 손익분기\n캐시 비중 0.6"}, "test ledger artifact")
    art = Artifact(project_id=project.id, kind="task_output", title="t", path=rel)
    s.add(art)
    s.flush()
    t = Task(project_id=project.id, key="be", title="be", owner="finance", objective="o", acceptance_criteria=[],
             status="DONE", artifact_id=art.id)
    t_other = Task(project_id=other.id, key="x", title="x", owner="finance", objective="o", acceptance_criteria=[],
                   status="DONE", artifact_id=art.id)
    s.add_all([t, t_other])
    s.commit()

    assert runner.ledger_files(project.id) == ["evidence.json", f"T-{t.id}.md"]
    meta = runner._ledger("evidence.json", project.id, tmp_path / "ev.json")
    body = (tmp_path / "ev.json").read_text(encoding="utf-8")
    assert "객단가 $29 가정" in body and "남의 기록" not in body and meta["url"] == "ledger:evidence.json"
    runner._ledger(f"T-{t.id}.md", project.id, tmp_path / "t.md")
    assert "캐시 비중 0.6" in (tmp_path / "t.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        runner._ledger(f"T-{t_other.id}.md", project.id, tmp_path / "x.md")


def test_values_from_ledger_are_not_primary_evidence(tmp_path: Path):
    rec = runner.AnalysisRecord(workdir=tmp_path)
    rec.manifest = [{"filename": "ev.json", "url": "ledger:evidence.json", "sha256": "0" * 64, "bytes": 1}]
    rec.final_results, rec.reproduced = '{"breakeven_users": 412}', True
    status, note = runner.classify(rec, "ledger:evidence.json", "412", lang="ko")
    assert status == "derived" and "원자료 근거가 아니에요" in note


def test_a_table_the_script_named_in_korean_is_kept_under_a_name_the_registry_takes(tmp_path: Path):
    """The registry takes ASCII names only, so a table the analyst's script saved as out/매출.csv would fail the
    whole analysis write."""
    out = tmp_path / "step1" / "out"
    out.mkdir(parents=True)
    script = "print(1)\n"
    (tmp_path / "step1" / "analysis.py").write_text(script, encoding="utf-8")
    (out / "매출.csv").write_text("월,매출\n1,100\n", encoding="utf-8")
    (out / "costs.csv").write_text("m,c\n1,40\n", encoding="utf-8")
    rec = runner.AnalysisRecord(workdir=tmp_path, final_script=script, final_results="{}")

    files = runner.artifact_files(rec, "projects/s13/analysis/T-1-i1")
    sha = registry.write_files(files, "analysis")

    tables = {rel: body for rel, body in files.items() if rel.endswith(".csv")}
    assert sorted(tables.values()) == ["m,c\n1,40\n", "월,매출\n1,100\n"]
    assert "projects/s13/analysis/T-1-i1/costs.csv" in tables
    assert all(registry.read_at(rel, sha) == body.strip() for rel, body in tables.items())
