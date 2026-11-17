"""Code outside the staffing package imports only what `justshipit/staffing/__init__.py` exports, and writes no crew or
turn row itself, so the two tables keep one writer (design section 5)."""
import ast
from pathlib import Path

import justshipit.staffing as staffing

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "backend" / "justshipit" / "staffing"
INNER = {p.stem for p in PACKAGE.glob("*.py") if p.stem != "__init__"}
PACKAGE_NAMES = ("staffing", "justshipit.staffing")
ROWS = {"Crew", "CrewTurn"}
WRITES = {"insert", "update", "delete"}


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _dotted(node.value)) is not None:
        return f"{base}.{node.attr}"
    return None


def _package_aliases(tree: ast.AST) -> set[str]:
    """The names a module binds to the package itself: `from justshipit import staffing`, `import ... as x`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").lstrip(".") in ("", "justshipit"):
            names |= {a.asname or a.name for a in node.names if a.name == "staffing"}
        elif isinstance(node, ast.Import):
            names |= {a.asname for a in node.names if a.name == "justshipit.staffing" and a.asname}
    return names


def inner_imports(source: str) -> list[str]:
    """The ways `source` reaches a staffing file or a name the package does not export: an import, an attribute of the
    package (`staffing.ledger`), or `importlib.import_module`."""
    tree = ast.parse(source)
    aliases = _package_aliases(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith("justshipit.staffing.")]
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            tail = module.lstrip(".")
            if tail.startswith("staffing.") or tail.startswith("justshipit.staffing."):
                found.append(module)
            elif tail in PACKAGE_NAMES:
                found += [f"{module}.{a.name}" for a in node.names if a.name not in staffing.__all__]
        elif isinstance(node, ast.Attribute) and node.attr not in staffing.__all__ and not node.attr.startswith("__"):
            base = _dotted(node.value)
            if base in aliases or base == "justshipit.staffing":
                found.append(f"{base}.{node.attr}")
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and (_dotted(node.func) or "").rsplit(".", 1)[-1] in ("import_module", "__import__")
              and str(node.args[0].value).startswith("justshipit.staffing.")):
            found.append(node.args[0].value)
    return found


def row_writes(source: str) -> list[str]:
    """The crew or turn rows `source` makes or changes: a `Crew(...)` built, or an insert, update or delete of the
    table. A name used only in an annotation, as `engineering/build_crew.py` does for its return type, is no call."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        called = _dotted(node.func) or ""
        if called.rsplit(".", 1)[-1] in ROWS:
            found.append(called)
        elif called.rsplit(".", 1)[-1] in WRITES and (
                called.split(".", 1)[0] in ROWS
                or any((_dotted(a) or "").rsplit(".", 1)[-1] in ROWS for a in node.args[:1])):
            found.append(f"{called}({ast.unparse(node.args[0]) if node.args else ''})")
    return found


def _outside(*dirs: str) -> list[Path]:
    return [p for d in dirs for p in (ROOT / d).rglob("*.py") if PACKAGE not in p.parents]


def test_nothing_outside_the_staffing_package_imports_one_of_its_files():
    hits = []
    for path in _outside("backend", "tests"):
        hits += [f"{path.relative_to(ROOT)}: {name}" for name in inner_imports(path.read_text(encoding="utf-8"))]

    assert hits == [], "import from justshipit.staffing, which exports the public names"


def test_nothing_outside_the_staffing_package_writes_a_crew_or_a_turn():
    hits = []
    for path in _outside("backend"):
        if path.name == "models.py" and path.parent.name == "db":
            continue   # where the two tables are declared
        hits += [f"{path.relative_to(ROOT)}: {name}" for name in row_writes(path.read_text(encoding="utf-8"))]

    assert hits == [], "write crews and turns through staffing.ensure, amend and record_turn"


def test_the_lint_sees_each_way_of_reaching_an_inner_file():
    assert inner_imports("from justshipit.staffing.crew import place") == ["justshipit.staffing.crew"]
    assert inner_imports("import justshipit.staffing.ledger") == ["justshipit.staffing.ledger"]
    assert inner_imports("from justshipit.staffing import crew") == ["justshipit.staffing.crew"]
    assert inner_imports("from ..staffing.turns import Turn") == ["..staffing.turns"]
    assert inner_imports("from justshipit import staffing\nstaffing.ledger.amend") == ["staffing.ledger"]
    assert inner_imports("import justshipit.staffing as st\nst.solo.OWNER_CARD") == ["st.solo"]
    assert inner_imports("import justshipit\njustshipit.staffing.turns.Turn") == ["justshipit.staffing.turns"]
    assert inner_imports("import importlib\nimportlib.import_module('justshipit.staffing.ledger')") == [
        "justshipit.staffing.ledger"]
    assert inner_imports("from importlib import import_module\nimport_module('justshipit.staffing.crew')") == [
        "justshipit.staffing.crew"]
    assert inner_imports("from justshipit.staffing import place, ensure") == []
    assert inner_imports("from justshipit import staffing\nstaffing.ensure(s, 1, f, l)") == []
    assert inner_imports("from justshipit import staffing") == []


def test_the_lint_sees_each_way_of_writing_a_row():
    assert row_writes("s.add(Crew(subject_kind='task'))") == ["Crew"]
    assert row_writes("s.add(models.CrewTurn(crew_id=1))") == ["models.CrewTurn"]
    assert row_writes("s.execute(insert(Crew).values(x=1))") == ["insert(Crew)"]
    assert row_writes("s.execute(sa.update(CrewTurn).values(x=1))") == ["sa.update(CrewTurn)"]
    assert row_writes("s.execute(Crew.__table__.delete())") == ["Crew.__table__.delete()"]
    assert row_writes("def crew(s, job_id: int) -> Crew:\n    return ensure(s, job_id)") == []
    assert row_writes("s.scalars(select(Crew).where(Crew.id == 1))") == []


def test_every_inner_file_is_an_actual_module_the_lint_knows_about():
    assert {"crew", "ledger", "turns", "config", "solo"} <= INNER
