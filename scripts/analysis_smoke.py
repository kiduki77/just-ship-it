"""Live smoke test of the data-analysis capability on the [TEST] project (P-2 by default).

Creates one analyst task and runs the company graph's work node on it directly (analysis loop →
server-side fetch and offline execution → reproduction → independent review). No approvals involved.

    uv run python scripts/analysis_smoke.py [project_id]
"""
from __future__ import annotations

import json
import sys
import time

from justshipit.db.models import Evidence, Project, Task
from justshipit.db.session import session_scope
from justshipit.orchestration.company_graph import work


def main(project_id: int) -> int:
    with session_scope() as s:
        p = s.get(Project, project_id)
        assert p.name.startswith("[TEST]"), "analysis smoke runs only on a [TEST] project"
        t = Task(project_id=project_id, key=f"analysis_smoke_{int(time.time())}", owner="analyst", status="READY",
                 title="[TEST] 시도별 고령인구 비율 원자료 집계",
                 objective="공개 원자료 파일(통계청·행정안전부·공공데이터포털 등에서 직접 내려받을 수 있는 CSV/XLSX)로 "
                           "전국 17개 시도별 65세 이상 인구 비율을 계산하고, 전라남도의 순위와 값을 제시하라.",
                 acceptance_criteria=["원자료 파일 URL과 기준 시점 명시", "17개 시도별 65세 이상 비율 표",
                                      "전라남도 값과 순위", "계산 방법(시트·열·집계) 설명"],
                 origin="smoke_test", budget_usd=8.0, max_iterations=1)
        s.add(t)
        s.flush()
        tid = t.id
    print(f"T-{tid} started", flush=True)
    work({"project_id": project_id, "task_id": tid})
    with session_scope() as s:
        t = s.get(Task, tid)
        a = (t.result or {}).get("analysis", {})
        print(json.dumps({"status": t.status, "error": t.error, "spent": round(t.spent_usd, 2),
                          "analysis": {k: a.get(k) for k in ("path", "steps", "reproduced")},
                          "inputs": [(m["filename"], m["url"], m["bytes"]) for m in a.get("inputs", [])],
                          "review": {k: (t.review or {}).get(k) for k in ("verdict", "served_by", "independence",
                                                                          "criteria_unmet")}},
                         ensure_ascii=False, indent=1))
        for eid in (t.result or {}).get("evidence_ids", []):
            e = s.get(Evidence, eid)
            print(f"EV-{e.id} [{e.kind}/{e.verification_status}] {e.claim[:90]} | quote={str(e.quote)[:30]}")
        print("\nSUMMARY:", (t.result or {}).get("summary", "")[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
