"""Every place the backend enqueues a job, read from the source: the kind (when it is written out), whether the call
says `by_owner=True`, and the function it sits in. tests/test_gate_sites.py pins the table, and `owner_reach` finds the
functions an owner's click can reach, so an enqueue nobody listed still has to say whether the click it serves runs.

Run it to print the table:  uv run --no-sync python tests/gate_sites.py
"""
from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend" / "justshipit"
ROUTE_VERBS = {"get", "post", "put", "patch", "delete"}
# The queue's own repair of lapsed jobs reaches almost everything; no owner click is what it serves.
LEAF_MODULES = {"services/queue"}


@dataclass(frozen=True)
class Site:
    where: str            # module path:function
    kind: str | None      # None when the kind is a variable
    by_owner: str         # "True", "False", "passed on" (a variable), "approved" (follows work the owner approved),
                          # "effect" (an Effect row), or "" (not given)


def _by_owner(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "by_owner":
            return repr(kw.value.value) if isinstance(kw.value, ast.Constant) else "passed on"
    for kw in call.keywords:
        if kw.arg == "approved" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return "approved"
    return ""


def _modules() -> dict[str, ast.Module]:
    out: dict[str, ast.Module] = {}
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND).as_posix().removesuffix(".py")
        if not rel.startswith("db/migrations"):
            out[rel] = ast.parse(path.read_text(encoding="utf-8"))
    return out


def _imports(tree: ast.Module, modules: dict[str, ast.Module]) -> tuple[dict[str, str], dict[str, str]]:
    """What a module's imports name: (local name -> "module:function", local name -> module)."""
    funcs: dict[str, str] = {}
    mods: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("justshipit"):
            base = n.module.removeprefix("justshipit").strip(".").replace(".", "/")
            for a in n.names:
                sub = f"{base}/{a.name}".strip("/")
                if sub in modules:
                    mods[a.asname or a.name] = sub
                else:
                    funcs[a.asname or a.name] = f"{base}:{a.name}"
    return funcs, mods


class _Visitor(ast.NodeVisitor):
    def __init__(self, module: str, funcs: dict[str, str], mods: dict[str, str], defs: set[str]) -> None:
        self.module, self.funcs, self.mods, self.defs = module, funcs, mods, defs
        self.stack: list[str] = []
        self.sites: list[Site] = []
        self.edges: dict[str, set[str]] = {}
        self.routes: set[str] = set()
        self.handlers: dict[str, set[str]] = {}   # job kind -> what its handler calls

    def here(self) -> str:
        return f"{self.module}:{self.stack[-1] if self.stack else '<module>'}"

    def _fn(self, node) -> None:
        for d in node.decorator_list:
            f = d.func if isinstance(d, ast.Call) else d
            if isinstance(f, ast.Attribute) and f.attr in ROUTE_VERBS:
                self.routes.add(f"{self.module}:{node.name}")
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _fn

    def _target(self, expr: ast.expr) -> str | None:
        """The backend function a name or `module.name` refers to, when it is one."""
        if isinstance(expr, ast.Name):
            t = self.funcs.get(expr.id) or f"{self.module}:{expr.id}"
        elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id in self.mods:
            t = f"{self.mods[expr.value.id]}:{expr.attr}"
        else:
            return None
        return t if t in self.defs else None

    def _edge(self, src: str, expr: ast.expr) -> None:
        if (t := self._target(expr)) is not None:
            self.edges.setdefault(src, set()).add(t)

    def visit_Dict(self, node: ast.Dict) -> None:
        if self.module == "workers/runner" and self.stack and self.stack[-1] == "_handlers":
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Lambda):
                    for c in ast.walk(v.body):
                        if isinstance(c, ast.Call) and (t := self._target(c.func)) is not None:
                            self.handlers.setdefault(k.value, set()).add(t)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        where = self.here()
        self._edge(where, node.func)
        for a in [*node.args, *(kw.value for kw in node.keywords)]:
            self._edge(where, a)   # a function handed over to be called, as the console's `_act(roadmap.move_bet, ...)`
        if name == "enqueue" and len(node.args) >= 2:
            kind = node.args[1].value if isinstance(node.args[1], ast.Constant) else None
            self.sites.append(Site(where, kind, _by_owner(node)))
        elif name == "Effect" and node.args and isinstance(node.args[0], ast.Constant):
            self.sites.append(Site(where, node.args[0].value, "effect"))
        self.generic_visit(node)


def _scan() -> tuple[list[Site], dict[str, set[str]], set[str], dict[str, set[str]]]:
    modules = _modules()
    defs = {f"{m}:{n.name}" for m, t in modules.items() for n in ast.walk(t)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    sites: list[Site] = []
    edges: dict[str, set[str]] = {}
    routes: set[str] = set()
    handlers: dict[str, set[str]] = {}
    for module, tree in modules.items():
        funcs, mods = _imports(tree, modules)
        v = _Visitor(module, funcs, mods, defs)
        v.visit(tree)
        sites += v.sites
        routes |= v.routes
        for k, vs in v.edges.items():
            edges.setdefault(k, set()).update(vs)
        for k, vs in v.handlers.items():
            handlers.setdefault(k, set()).update(vs)
    return sites, edges, routes, handlers


def sites() -> list[Site]:
    return _scan()[0]


def owner_reach(asked_kinds: set[str]) -> dict[str, list[str]]:
    """Every function an owner's click can reach, with the shortest chain that reaches it. A click starts at an API
    route or at `requests.respond` and follows calls, and functions handed to calls. A job the click enqueues as the
    owner's (`by_owner`, a decision's Effect) is followed into its handler, and so is every job of a kind the owner
    asks for (`asked_kinds`): what such a job enqueues is the click's consequence too."""
    found, edges, routes, handlers = _scan()
    graph = {k: set(v) for k, v in edges.items()}
    for site in found:
        if site.kind is not None and (site.by_owner in ("True", "passed on", "effect") or site.kind in asked_kinds):
            graph.setdefault(site.where, set()).update(handlers.get(site.kind, ()))
    roots = [r for r in routes if r.startswith("api/")] + ["services/requests:respond"]
    roots += [h for kind in asked_kinds for h in handlers.get(kind, ())]
    chain: dict[str, list[str]] = {r: [r] for r in roots}
    todo = deque(roots)
    while todo:
        n = todo.popleft()
        if n.split(":")[0] in LEAF_MODULES:
            continue
        for t in sorted(graph.get(n, ())):
            if t not in chain:
                chain[t] = [*chain[n], t]
                todo.append(t)
    return chain


if __name__ == "__main__":
    for site in sites():
        print(f"{site.where:60} {site.kind or '?':20} {site.by_owner}")
