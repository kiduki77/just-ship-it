"""Where owner-facing Korean still sits outside the catalogs, and the catalogs' own shape. test_i18n.py asserts on it.

Run it to see what is left, file by file:  uv run python tests/i18n_kit.py
A lane that finishes a file deletes its line from i18n_hangul_allowlist.txt; the test fails until it does.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"
BACKEND = ROOT / "backend" / "justshipit"
ALLOWLIST = Path(__file__).with_name("i18n_hangul_allowlist.txt")

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")
# A locale pinned to Korean is the same leak as a Korean word: the console's language comes from i18n's locale().
KOREAN_LOCALE = re.compile(r"""["'`]ko-KR["'`]|(?:Intl\.\w+|localeCompare)\([^)]*["'`]ko["'`]""")
_JS_TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _web_code(text: str) -> str:
    """The source without its comments: a comment in Korean is not text the owner reads."""
    return _JS_TOKEN.sub(lambda m: "" if m.group(0).startswith(("//", "/*")) else m.group(0), text)


def web_lines(path: Path) -> int:
    code = _web_code(path.read_text(encoding="utf-8"))
    return sum(1 for line in code.splitlines() if HANGUL.search(line) or KOREAN_LOCALE.search(line))


def _docstrings(tree: ast.AST) -> set[int]:
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body
            and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}


def python_lines(path: Path) -> int:
    """Lines holding a Korean string literal. Docstrings and comments are for the next engineer, not the owner."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    return len({n.lineno for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docs and HANGUL.search(n.value)})


def scanned() -> dict[str, int]:
    """Every file outside the catalogs that still holds owner-facing Korean, with its line count."""
    found: dict[str, int] = {}
    for path in sorted(WEB.rglob("*.ts*")):
        if "i18n" not in path.relative_to(WEB).parts and (n := web_lines(path)):
            found[path.relative_to(ROOT).as_posix()] = n
    for path in sorted(BACKEND.rglob("*.py")):
        parts = path.relative_to(BACKEND).parts
        if parts[0] != "i18n" and parts[:2] != ("db", "migrations") and (n := python_lines(path)):
            found[path.relative_to(ROOT).as_posix()] = n
    return found


_LISTED = re.compile(r"^(\S+)\s+#\s*(\d+)\s*$")


def allowlist() -> dict[str, int]:
    """Each listed file and the most Korean lines it may hold: the `# N` after it, which only goes down."""
    found = {}
    for ln in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        if ln.split("#", 1)[0].strip():
            m = _LISTED.match(ln.strip())
            if m is None:
                raise ValueError(f"{ALLOWLIST.name}: '{ln}' needs its count, as in 'path  # 12'")
            found[m.group(1)] = int(m.group(2))
    return found


def over_count(found: dict[str, int], allowed: dict[str, int]) -> dict[str, tuple[int, int]]:
    """The listed files holding more Korean lines than their count, with (lines now, count)."""
    return {name: (n, allowed[name]) for name, n in found.items() if name in allowed and n > allowed[name]}


# ------------------------------------------------------------------ the console's catalogs

_STR = r'"(?:\\.|[^"\\])*"'
_MSG = rf'(?:{_STR}|\{{\s*one:\s*{_STR},\s*other:\s*{_STR}\s*,?\s*\}})'
_ENTRY = re.compile(rf'^\s*("[^"\n]+"):\s*\{{\s*en:\s*({_MSG}),\s*ko:\s*({_MSG})\s*,?\s*\}},', re.MULTILINE | re.DOTALL)
_KEY_LINE = re.compile(r'^\s*"[^"\n]+":\s*\{', re.MULTILINE)


def _message(src: str) -> str | dict[str, str]:
    if src.startswith('"'):
        return json.loads(src)
    one, other = re.findall(_STR, src)
    return {"one": json.loads(one), "other": json.loads(other)}


def web_catalog() -> dict[str, dict[str, dict[str, str | dict[str, str]]]]:
    """Each area file's entries as {key: {"en": message, "ko": message}}. An entry written in any other shape is
    reported as a key with no messages, so the test names it."""
    out = {}
    for path in sorted((WEB / "i18n" / "areas").glob("*.ts")):
        text = path.read_text(encoding="utf-8")
        entries = {json.loads(k): {"en": _message(en), "ko": _message(ko)} for k, en, ko in _ENTRY.findall(text)}
        for line in _KEY_LINE.findall(text):
            key = json.loads(line.strip().split(":")[0]) if line.strip().startswith('"') else None
            entries.setdefault(key or line, {})
        out[path.stem] = entries
    return out


if __name__ == "__main__":
    left = scanned()
    for name, n in sorted(left.items(), key=lambda x: -x[1]):
        sys.stdout.write(f"{n:5d}  {name}\n")
    sys.stdout.write(f"{sum(left.values()):5d}  lines in {len(left)} files\n")
