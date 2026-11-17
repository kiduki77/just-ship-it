"""The path view of one project (docs/design/work-path.md 6): the nodes and edges the console draws, from the same graph
the card and the your-turn list read. The server decides what exists, what is folded and what is too big to draw; the
client decides where each box goes, so no coordinates are sent. Ids come from row ids (`t:41`, `r:6`), so they survive
a revision, and `rev` is a hash of the graph's own facts, so it changes only when the graph does."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from justshipit.contracts import owner_text
from justshipit.contracts.requests import RequestStatus
from justshipit.contracts.work import TaskStatus
from justshipit.contracts.work_path import HoldPhase, HoldScope, Rel, TargetKind
from justshipit.contracts.your_turn import Edge, Node, PathLimits, PathView
from justshipit.db.models import AgentRequest, Project
from justshipit.i18n import msg
from justshipit.services import app_settings, requests, work_path, your_turn
from justshipit.services.control import NotFound

LIMITS = PathLimits()
NEIGHBOURHOOD_HOPS = 2
_ENDED = (TaskStatus.CANCELLED.value, TaskStatus.REPLACED.value, TaskStatus.REJECTED.value)
_DONE = TaskStatus.DONE.value
_SPINE_ID = {"goal": "g", "cycle": "c", "stage": "s", "experiment": "b", "project": "p"}


@dataclass
class _Draft:
    """The whole graph before folding: nodes with their priority (lower is drawn first, then by when they were added)
    and the edges between them."""
    nodes: dict[str, Node] = field(default_factory=dict)
    priority: dict[str, tuple[int, int]] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)

    def node(self, node: Node, prio: int) -> None:
        if node.id not in self.nodes:
            self.nodes[node.id] = node
            self.priority[node.id] = (prio, len(self.nodes))
        else:
            self.promote(node.id, prio)

    def promote(self, nid: str, prio: int) -> None:
        if nid in self.priority and prio < self.priority[nid][0]:
            self.priority[nid] = (prio, self.priority[nid][1])

    def edge(self, kind: str, source: str, target: str, label_key: str | None = None, **kw: Any) -> None:
        if source not in self.nodes or target not in self.nodes or source == target:
            return
        key = ":".join(str(x) for x in (kind, source, target, kw.get("phase") or kw.get("effect") or ""))
        self.edges.setdefault(key, Edge(id=f"e:{key}", kind=kind, source=source, target=target,  # type: ignore[arg-type]
                                        label_key=label_key or f"path.edge.{kind}", **kw))


def _group_of(n: Node) -> str:
    if n.kind == "task":
        return "f:tasks_done" if n.state == _DONE else "f:tasks_ended" if n.state in _ENDED else "f:tasks"
    return {"decision": "f:decisions", "project_manager": "f:rounds", "todo": "f:todos"}.get(n.kind, "f:origins")


def _task_node(t: work_path.Row, held: bool) -> Node:
    return Node(id=f"t:{t['id']}", kind="task", ref=f"T-{t['id']}", ref_id=t["id"], label_key="path.node.task",
                vars={"title": owner_text.title(t["title"], 80)}, state=t["status"], held=held)


def _decision_node(g: work_path.WorkGraph, rid: int) -> Node:
    r = g.requests[rid]
    pending = r["status"] == RequestStatus.PENDING.value
    reach = work_path.reach_of(g, r) if pending else None
    return Node(id=f"r:{rid}", kind="decision", ref=requests.display_id(rid), ref_id=rid, label_key="path.node.decision",
                vars={"title": owner_text.title(r["title"], 80)}, state=r["status"], waiting=pending,
                scope=HoldScope(r["hold_scope"]) if pending else None,
                frees=reach.frees if reach else None, behind=reach.behind if reach else None)


def _asker_node(g: work_path.WorkGraph, link: work_path.Row) -> Node | None:
    """The node for what raised a card, or None when the records do not say (the edge is then "not recorded")."""
    step = work_path.asker(g, link)
    kind, rid = link["target_kind"], link["target_id"]
    if step.kind == "origin":
        return None
    if kind == TargetKind.TASK.value:
        return _task_node(g.tasks[rid], False)
    if kind == TargetKind.REQUEST.value:
        return _decision_node(g, rid)
    if step.kind == "round" or (step.kind == "call" and step.label_key != "path.step.call.other"):
        return Node(id=f"{'m' if step.kind == 'round' else 'k'}:{rid}", kind="project_manager", ref_id=rid,
                    label_key=step.label_key, vars=step.vars, known=step.known)
    return Node(id=f"x:{kind}:{rid}", kind="origin", ref=step.ref, ref_id=rid, label_key=step.label_key,
                vars=step.vars, known=step.known)


def build(g: work_path.WorkGraph, pid: int, focus: int | None) -> _Draft:
    """Every node and edge of the project, with the order they are drawn in when room is short: the chain from the goal
    to the focused card (always drawn), the tasks it holds, the other pending cards with theirs, a done neighbour of
    each, what comes straight after the held tasks, then failed tasks, askers and to-dos, other tasks, done ones, ended
    ones."""
    d = _Draft()
    previous = None
    for step in work_path.above(g, {"project_id": pid}):
        nid = "g" if step.kind == "goal" else f"{_SPINE_ID[step.kind]}:{step.ref_id}"
        d.node(Node(id=nid, kind=step.kind, ref=step.ref, ref_id=step.ref_id, label_key=step.label_key, vars=step.vars,
                    state=step.state, known=step.known, chain=True), 0)
        if previous:
            d.edge("contains", previous, nid)
        previous = nid
    project = f"p:{pid}"
    live: dict[int, set[int]] = defaultdict(set)
    for h in g.holds:
        live[h.request_id].add(h.task_id)
    held = {tid for tids in live.values() for tid in tids}
    tasks = {t["id"]: t for t in g.tasks.values() if t["project_id"] == pid}
    cards = your_turn.cards(g, [r["id"] for r in g.requests.values()
                                if r["project_id"] == pid and r["status"] == RequestStatus.PENDING.value])
    shown = [c.id for c in cards] + ([focus] if focus and focus not in {c.id for c in cards} else [])

    for c in cards:
        d.node(_decision_node(g, c.id), 2)
        for tid in sorted(live.get(c.id, ())):
            if tid in tasks:
                d.node(_task_node(tasks[tid], True), 1 if c.id == focus else 2)
    if focus:
        d.node(_decision_node(g, focus), 0)
        d.nodes[f"r:{focus}"].focus = d.nodes[f"r:{focus}"].chain = True
    newest_done_first = lambda x: (x[1]["status"] == _DONE, -x[0] if x[1]["status"] == _DONE else x[0])
    for tid, t in sorted(tasks.items(), key=newest_done_first):
        d.node(_task_node(t, tid in held),
               5 if t["status"] == TaskStatus.FAILED.value else 7 if t["status"] == _DONE
               else 9 if t["status"] in _ENDED else 6)

    for rid in shown:
        _card_edges(d, g, rid, rid == focus, project, live)
    for tid, t in tasks.items():
        deps = [x for x in t["depends_on"] or [] if x in tasks]
        for dep in deps:
            d.edge("depends_on", f"t:{dep}", f"t:{tid}")
        if not deps:
            d.edge("contains", project, f"t:{tid}")
    for o in sorted(g.todos.values(), key=lambda x: x["id"]):
        if o["project_id"] == pid:
            d.node(Node(id=f"o:{o['id']}", kind="todo", ref=f"OT-{o['id']}", ref_id=o["id"], label_key="path.node.todo",
                        vars={"title": owner_text.title(o["title"], 80), "kind": o["kind"]}, state="OPEN"), 5)
            by = f"r:{o['request_id']}"
            if by in d.nodes:
                d.edge("followed", by, f"o:{o['id']}", "path.edge.followed_todo")
            else:
                d.edge("contains", project, f"o:{o['id']}")
    _near(d, tasks, live, shown, g)
    return d


def _card_edges(d: _Draft, g: work_path.WorkGraph, rid: int, focused: bool, project: str,
                live: dict[int, set[int]]) -> None:
    """What raised the card, what it holds and, once decided, what it started."""
    r = g.requests[rid]
    links = [x for x in g.links.get(rid, ()) if x["ended_at"] is None]
    askers = [(x, n) for x in links if x["rel"] == Rel.RAISED_BY.value and (n := _asker_node(g, x))]
    for x, n in askers:
        d.node(n, 0 if focused else 5)
        if focused:
            d.nodes[n.id].chain = True
        d.edge("raised_by", n.id, f"r:{rid}", known=x["known"])
        if n.kind not in ("task", "decision"):
            d.edge("contains", project, n.id)
    if not askers:
        d.edge("raised_by", project, f"r:{rid}", "path.edge.raised_by_unknown", known="unknown")
    if r["status"] == RequestStatus.PENDING.value and r["hold_scope"] in (HoldScope.RUN.value, HoldScope.PLANNING.value):
        d.edge("holds", f"r:{rid}", project, f"path.edge.holds_{r['hold_scope']}")
    for h in sorted((h for h in g.holds if h.request_id == rid), key=lambda h: (h.task_id, h.phase.value)):
        d.edge("holds", f"r:{rid}", f"t:{h.task_id}", "path.edge.holds_result" if h.phase == HoldPhase.FINISH else None,
               phase=h.phase.value)
    for x in (x for x in links if x["rel"] == Rel.FOLLOWED.value):
        target = (f"t:{x['target_id']}" if x["target_kind"] == TargetKind.TASK.value
                  else project if x["target_kind"] == TargetKind.PROJECT.value else None)
        if target:
            d.edge("followed", f"r:{rid}", target, effect=x["effect"])


def _near(d: _Draft, tasks: dict[int, work_path.Row], live: dict[int, set[int]], cards: list[int],
          g: work_path.WorkGraph) -> None:
    """Move up what sits beside a pending card: one done neighbour each (a done task a held task waited on, else the
    task that raised it, else the latest done one), and the tasks that come straight after the held ones."""
    done = sorted((t for t in tasks.values() if t["status"] == _DONE),
                  key=lambda t: (t["finished_at"] or t["updated_at"], t["id"]))
    for rid in cards:
        before = {x for tid in live.get(rid, ()) for x in tasks[tid]["depends_on"] or []
                  if x in tasks and tasks[x]["status"] == _DONE}
        raised = {x["target_id"] for x in g.links.get(rid, ()) if x["rel"] == Rel.RAISED_BY.value
                  and x["target_kind"] == TargetKind.TASK.value and tasks.get(x["target_id"], {}).get("status") == _DONE}
        pick = max(before) if before else max(raised) if raised else done[-1]["id"] if done else None
        if pick is not None:
            d.promote(f"t:{pick}", 3)
    held = {tid for tids in live.values() for tid in tids}
    for t in tasks.values():
        if t["status"] not in _ENDED and t["status"] != _DONE and held & set(t["depends_on"] or []):
            d.promote(f"t:{t['id']}", 4)


def _hood(d: _Draft, focus: str) -> set[str]:
    """The nodes within `NEIGHBOURHOOD_HOPS` of the focused card along the links between work, never through the
    project, which touches everything. The chain above the card stays."""
    near: dict[str, set[str]] = defaultdict(set)
    for e in d.edges.values():
        if e.kind != "contains":
            near[e.source].add(e.target)
            near[e.target].add(e.source)
    seen, todo = {focus}, deque([(focus, 0)])
    while todo:
        here, hops = todo.popleft()
        for nxt in sorted(near[here]) if hops < NEIGHBOURHOOD_HOPS and not here.startswith("p:") else ():
            if nxt not in seen:
                seen.add(nxt)
                todo.append((nxt, hops + 1))
    return seen | {i for i, n in d.nodes.items() if n.chain}


def _rev(d: _Draft) -> str:
    """A hash of the graph's own facts, not of the focus, the folding or the time, so it changes only with the graph."""
    facts = {"n": sorted((n.id, n.kind, n.state, n.label_key, n.vars, n.known, n.waiting, n.held, n.scope, n.frees,
                          n.behind) for n in d.nodes.values()),
             "e": sorted((e.id, e.label_key, e.known) for e in d.edges.values())}
    return hashlib.sha1(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _folded(d: _Draft, hidden: Iterable[str]) -> dict[str, list[str]]:
    """The hidden nodes by the group that folds them, in the order they would be drawn."""
    groups: dict[str, list[str]] = defaultdict(list)
    for nid in sorted(hidden, key=lambda i: d.priority[i]):
        groups[_group_of(d.nodes[nid])].append(nid)
    return groups


def _fit(d: _Draft, base: set[str], pool: list[str], rest: list[str], limit: int) -> int:
    """How many of `pool`, in order, are drawn beside `base`: the most that keeps the nodes and the groups that fold
    the rest (`pool` after that, and `rest`) within `limit`."""
    for k in range(len(pool), 0, -1):
        if len(base) + k + len({_group_of(d.nodes[i]) for i in (*pool[k:], *rest)}) <= limit:
            return k
    return 0


def path_view(s: Session, project_id: int, *, focus: int | None = None, neighbourhood: bool = False,
              unfold: Iterable[str] = (), now: datetime | None = None) -> PathView:
    """The project's path, opened on `focus` (a request id) when given. At most `LIMITS.default` nodes are drawn and
    the rest fold into groups; `unfold` names the groups to open (`all` for every one), up to `LIMITS.unfolded` nodes.
    A project past `LIMITS.too_big` nodes comes back as `too_big` with its outline and no graph, unless `neighbourhood`
    narrows it to the focused card's. Raises NotFound for a missing project, or a focus that is not a request of it."""
    lang = app_settings.language()
    p = s.get(Project, project_id)
    if p is None:
        raise NotFound(msg("console.no_project", lang, id=project_id))
    if focus is not None and ((r := s.get(AgentRequest, focus)) is None or r.project_id != project_id):
        raise NotFound(msg("console.no_request", lang, id=focus))
    g = work_path.load(s, p.business_id, [project_id], cards=[focus] if focus else [])
    d = build(g, project_id, focus)
    narrowed = neighbourhood and focus is not None
    if narrowed:
        keep = _hood(d, f"r:{focus}")
        d.nodes = {i: n for i, n in d.nodes.items() if i in keep}
        d.edges = {k: e for k, e in d.edges.items() if e.source in keep and e.target in keep}
    pending = [r["id"] for r in g.requests.values()
               if r["project_id"] == project_id and r["status"] == RequestStatus.PENDING.value]
    head: dict[str, Any] = {
        "project_id": project_id, "focus": f"r:{focus}" if focus else None, "rev": _rev(d), "neighbourhood": narrowed,
        "can_narrow": focus is not None and not narrowed, "total": len(d.nodes), "limits": LIMITS,
        "outline": your_turn.outline(g, your_turn.cards(g, pending), only=project_id), "as_of": now or datetime.now(UTC)}
    if len(d.nodes) > LIMITS.too_big:
        return PathView(**head, too_big=True, unfolded=[], shown=0, nodes=[], edges=[])

    base = {i for i in d.nodes if d.priority[i][0] == 0}
    optional = sorted((i for i in d.nodes if i not in base), key=lambda i: d.priority[i])
    k = _fit(d, base, optional, [], LIMITS.default)
    drawn = base | set(optional[:k])
    groups = _folded(d, optional[k:])
    opened = [gid for gid in dict.fromkeys(unfold) if gid in groups or gid == "all"]
    if opened:
        extra = [i for gid in (list(groups) if "all" in opened else opened) for i in groups.get(gid, ())]
        pool = [*optional[:k], *extra]
        rest = [i for i in optional[k:] if i not in extra]
        drawn = base | set(pool[:_fit(d, base, pool, rest, LIMITS.unfolded)])
        groups = _folded(d, set(optional) - drawn)
    return _assemble(d, drawn, groups, head, [x for x in opened if x != "all"] or opened)


def _assemble(d: _Draft, drawn: set[str], groups: dict[str, list[str]], head: dict[str, Any],
              opened: list[str]) -> PathView:
    """The drawn nodes and one node per group; a link to a folded node ends at its group instead."""
    nodes = [d.nodes[i] for i in sorted(drawn, key=lambda i: d.priority[i][1])]
    nodes += [Node(id=gid, kind="group", label_key=f"path.group.{gid[2:]}", vars={"n": len(m)}, count=len(m), members=m)
              for gid, m in sorted(groups.items())]
    stand = {nid: gid for gid, members in groups.items() for nid in members}
    edges: dict[str, Edge] = {}
    for e in d.edges.values():
        src, tgt = stand.get(e.source, e.source), stand.get(e.target, e.target)
        if src != tgt:
            key = f"{e.kind}:{src}:{tgt}:{e.phase or e.effect or ''}"
            edges.setdefault(key, e.model_copy(update={"id": f"e:{key}", "source": src, "target": tgt,
                                                       "via_group": (src, tgt) != (e.source, e.target)}))
    return PathView(**head, too_big=False, unfolded=opened, shown=len(nodes), nodes=nodes, edges=list(edges.values()))
