"""Prototype of docs/design/cycle-stages/design.md sections 3.1, 4.1, 4.2 and 5.2, transcribed verbatim."""
import sqlite3
from collections import deque

# 3.1 verbatim
STAGE_TRANSITIONS = {
    "planned": {"running", "skipped"}, "running": {"closing", "stopped"}, "closing": {"awaiting_owner", "stopped"},
    "awaiting_owner": {"done", "running"}, "stopped": {"running"}, "done": set(), "skipped": set(),
}
OPEN_STAGE = ("running", "closing")


def reaches_done_avoiding(start, avoid):
    seen, q = {start}, deque([start])
    while q:
        cur = q.popleft()
        if cur == "done":
            return True
        for nxt in STAGE_TRANSITIONS[cur]:
            if nxt not in seen and nxt not in avoid:
                seen.add(nxt)
                q.append(nxt)
    return False


print("PROBE-E stopped -> done without passing 'running' (spend resumes):", reaches_done_avoiding("stopped", {"running"}))
print("PROBE-E stopped -> successors:", STAGE_TRANSITIONS["stopped"])


# 4.2 admit() pseudocode, STAGE_WORK branch
def admit_stage_work(stage_status, managed=True):
    st = {"id": 41, "status": stage_status} if stage_status in OPEN_STAGE else None   # open_stage(): running|closing
    if not managed:
        return ("run", "direct", None)
    return ("run", "stage", st["id"]) if st else ("park", "stage", None)


def claim_ok(gate, stage_id, stages):   # 5.2 claim predicate
    return gate in ("none", "direct") or (gate == "stage" and stage_id in [i for i, v in stages.items() if v in OPEN_STAGE])


adm = admit_stage_work("closing")
print("PROBE-G run_project enqueued while stage 41 is 'closing' ->", adm, "| claimable:", claim_ok(adm[1], adm[2], {41: "closing"}))

# 4.1 partial unique index; 7 step 3a adopted row; 7 step 5: the adopted stage settles before the owner presses Start
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE cycle_stages (id INTEGER PRIMARY KEY, cycle_id INT, business_id INT, key TEXT, status TEXT)")
db.execute("CREATE UNIQUE INDEX ix_cycle_stages_one_holding ON cycle_stages (business_id) "
           "WHERE status IN ('running','closing','awaiting_owner')")
db.execute("INSERT INTO cycle_stages VALUES (1, 9, 7, 'adopted', 'running')")          # migration 3a
db.execute("UPDATE cycle_stages SET status='closing' WHERE id=1")                       # tick: settled
db.execute("UPDATE cycle_stages SET status='awaiting_owner' WHERE id=1")                # store_summary
# owner presses Start on proposal v1 (cycle still 'draft'): _materialize + _start_stage(first non-past planned)
db.execute("INSERT INTO cycle_stages VALUES (2, 9, 7, 'validate', 'planned')")
try:
    db.execute("UPDATE cycle_stages SET status='running' WHERE id=2")
    print("PROBE-F StartCycle started stage 2")
except sqlite3.IntegrityError as exc:
    print("PROBE-F StartCycle -> _start_stage(planned) while the adopted stage is awaiting_owner:", exc)
print("PROBE-F other branch (relabel + start the adopted row in place): awaiting_owner -> running exists only as",
      "the retry_stage edge:", "running" in STAGE_TRANSITIONS["awaiting_owner"])
