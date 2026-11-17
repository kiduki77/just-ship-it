"""Compare API latency between two servers on the same database.

    uv run python scripts/bench_api.py --server trunk=http://127.0.0.1:8101 --server head=http://127.0.0.1:8102 \
        --path /api/overview --path /api/projects --path head:/api/businesses/5/roadmap=150 --n 50

Each round calls every path on every server once, so the two servers see the same cache state. The
report is the median per server and path. The run fails when a head median exceeds the trunk median by
more than --tolerance (default 20 percent), or when a path with an absolute budget (`=ms`) exceeds it.
A path prefixed `head:` runs only on the server named head.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request


def _token() -> str:
    from justshipit.settings import settings
    return (settings().var_dir / "api_token").read_text(encoding="utf-8").strip()


def _call(url: str, token: str) -> float:
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    return (time.perf_counter() - t0) * 1000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", action="append", required=True, help="name=base_url")
    ap.add_argument("--path", action="append", required=True, help="[server:]path[=budget_ms]")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--tolerance", type=float, default=0.20)
    ap.add_argument("--json", help="write the samples and medians here")
    a = ap.parse_args()
    servers = dict(x.split("=", 1) for x in a.server)
    paths = []
    for spec in a.path:
        only, budget = None, None
        if ":" in spec.split("/", 1)[0]:
            only, spec = spec.split(":", 1)
        if "=" in spec:
            spec, b = spec.rsplit("=", 1)
            budget = float(b)
        paths.append((spec, only, budget))
    token = _token()
    samples: dict[tuple[str, str], list[float]] = {}
    for _ in range(a.n):
        for path, only, _b in paths:
            for name, base in servers.items():
                if only and name != only:
                    continue
                samples.setdefault((name, path), []).append(_call(base.rstrip("/") + path, token))
    medians = {k: statistics.median(v) for k, v in samples.items()}
    failures = []
    print(f"{'path':40} {'server':8} {'median ms':>10} {'p95 ms':>8}")
    for (name, path), v in samples.items():
        p95 = sorted(v)[int(len(v) * 0.95) - 1] if len(v) > 1 else v[0]
        print(f"{path:40} {name:8} {medians[(name, path)]:10.1f} {p95:8.1f}")
    for path, only, budget in paths:
        if budget is not None:
            for name in servers:
                if (name, path) in medians and medians[(name, path)] > budget:
                    failures.append(f"{name} {path}: median {medians[(name, path)]:.1f} ms over budget {budget:.0f} ms")
        if not only and "trunk" in servers and "head" in servers:
            t, h = medians[("trunk", path)], medians[("head", path)]
            if h > t * (1 + a.tolerance):
                failures.append(f"{path}: head {h:.1f} ms exceeds trunk {t:.1f} ms by more than {a.tolerance:.0%}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"medians": {f"{n} {p}": m for (n, p), m in medians.items()},
                       "samples": {f"{n} {p}": v for (n, p), v in samples.items()}, "failures": failures}, f, indent=1)
    for f in failures:
        print("FAIL", f)
    print("PASS" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
