"""Reads pytest's junit xml and prints the pass/fail summary the runner ends on.

usage: summary.py JUNIT_XML FAILURES_TSV PYTEST_EXIT_CODE
Writes FAILURES_TSV as `status<TAB>test id` lines, one per failed or errored test.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def node_id(case: ET.Element) -> str:
    cls = case.get("classname", "")
    parts = cls.split(".")
    # junit classname is dotted module path (+ class); rebuild a pytest node id.
    module_end = next((i for i, p in enumerate(parts) if p.startswith("test_")), None)
    if module_end is None:
        return f"{cls}::{case.get('name')}"
    path = "/".join(parts[: module_end + 1]) + ".py"
    klass = "::".join(parts[module_end + 1:])
    return "::".join(x for x in (path, klass, case.get("name")) if x)


def main(junit: str, out: str, rc: int) -> int:
    path = Path(junit)
    print("\n" + "=" * 70)
    if not path.exists():
        print(f"NO RESULT: pytest exited {rc} without writing a report (killed, timed out, or crashed at startup).")
        Path(out).write_text("")
        return 2
    counts = dict(passed=0, failed=0, errors=0, skipped=0)
    failures: list[tuple[str, str]] = []
    for case in ET.parse(path).getroot().iter("testcase"):
        tid = node_id(case)
        if case.find("failure") is not None:
            counts["failed"] += 1
            failures.append(("failed", tid))
        elif case.find("error") is not None:
            counts["errors"] += 1
            failures.append(("error", tid))
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    Path(out).write_text("".join(f"{s}\t{t}\n" for s, t in sorted(failures, key=lambda x: x[1])))
    total = sum(counts.values())
    print(f"RESULT: {counts['passed']} passed, {counts['failed']} failed, {counts['errors']} errors, "
          f"{counts['skipped']} skipped ({total} tests, pytest exit {rc})")
    for status, tid in sorted(failures, key=lambda x: x[1]):
        print(f"  {status.upper():7} {tid}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
