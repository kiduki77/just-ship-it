"""The host's check that a new product keeps to the DESIGN.md its build was given: the file is as given, every hex
colour in its screen code is one of the file's, and no text is coloured with the accent's fill role.

Runs on the host-made export of the branch (`worker.test_and_review`), never in the engineer's clone. Its findings go
to the reviewer and the fix round beside the test log. A build given no DESIGN.md (a product that keeps its own look)
is never checked."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from justshipit.engineering.style_packs import frontmatter
from justshipit.sandbox import plainfile

DESIGN = "DESIGN.md"
# Where screen code lives: a Vite product's index.html and src/, and the usual roots of a server-rendered one.
_ROOTS = ("src", "app", "templates", "static", "public")
_SOURCES = (".css", ".ts", ".tsx", ".js", ".jsx", ".html")
_SKIP_DIRS = {"node_modules", "vendor", "__tests__", "dist", "build"}
_FILE_BYTES = 1_000_000  # a source file bigger than this is generated or vendored, not the engineer's
_MAX_FINDINGS = 40
_HEX_BODY = r"#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])"
_HEX = re.compile(r"(?<![&\w])" + _HEX_BODY)
# A hex counts only where it is a colour: the value of a colour-bearing CSS property, in a stylesheet, a style
# attribute or a style object (`color: #fff`, `backgroundColor: '#fff'`, `--surface: #fff`); an SVG paint attribute
# (`fill="#fff"`); a theme-color meta. So "Order #1042", href="#add", a #face selector and url(#grad) do not count.
# An unquoted value ends a declaration, so `.border:hover #bad {` is a selector, not one.
_COLOUR_PROP = re.compile(r"(?i)(?<![\w-])(?:--[\w-]+|[\w-]*(?:colou?r|background|border|outline|shadow|fill|stroke|"
                          r"decoration|filter)[\w-]*)\s*:\s*(\"[^\"]*\"|'[^']*'|`[^`]*`|[^;{}\"'`]*(?=[;}\"'`]|$))")
_PAINT_ATTR = re.compile(r"(?i)(?<![\w-])(?:fill|stroke|stop-?color|flood-?color|lighting-?color|color|bgcolor)\s*=\s*"
                         r"\{?\s*[\"'`]\s*(#[0-9a-fA-F]{3,8})(?![\w-])")
_THEME_COLOR = re.compile(r"(?i)theme-color")
_CONTENT = re.compile(r"(?i)(?<![\w-])content\s*=\s*[\"']\s*(#[0-9a-fA-F]{3,8})(?![\w-])")
_URL = re.compile(r"(?i)url\([^)]*\)")
_TEXT_VAR = re.compile(r"(?<![\w-])color\s*:\s*[\"'`]?\s*var\(\s*--([\w-]+)")


@dataclass(frozen=True)
class Result:
    files: int  # how many screen-code files the check read
    found: list[str]  # one line per place the product leaves its DESIGN.md; empty when it keeps to it


def _six(digits: str) -> str:
    """The colour as `#rrggbb`, its alpha dropped: a token at some opacity is still the token's colour."""
    digits = digits.lstrip("#").lower()
    return "#" + ("".join(c * 2 for c in digits[:3]) if len(digits) <= 4 else digits[:6])


def palette(design: str) -> set[str]:
    return {_six(m[1]) for m in _HEX.finditer(frontmatter(design)[0])}


def colours(line: str) -> list[str]:
    """The hex colours a line of screen code sets, as written."""
    found = [m[0] for decl in _COLOUR_PROP.finditer(line) for m in _HEX.finditer(_URL.sub("", decl[1]))]
    found += [m[1] for m in _PAINT_ATTR.finditer(line)]
    if _THEME_COLOR.search(line):
        found += [m[1] for m in _CONTENT.finditer(line)]
    return found


def _fill_role(name: str) -> bool:
    """`--primary` or a prefixed name for it (`--color-primary`), not `--on-primary` or `--primary-text`."""
    return name == "primary" or (name.endswith("-primary") and not name.endswith("on-primary"))


def _screen_code(path: Path) -> bool:
    return (path.suffix in _SOURCES and ".test." not in path.name and ".spec." not in path.name
            and not path.is_symlink() and path.stat().st_size <= _FILE_BYTES)


def _sources(root: Path) -> list[Path]:
    """The product's screen code. A link is never followed: the export holds what the branch committed, and a link
    in it could point anywhere on the host."""
    index = root / "index.html"
    found = [index] if index.is_file() and _screen_code(index) else []
    for top_dir in (root / name for name in _ROOTS):
        if top_dir.is_symlink() or not top_dir.is_dir():
            continue
        for top, dirs, files in os.walk(top_dir):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not Path(top, d).is_symlink())
            found += [p for f in sorted(files) if _screen_code(p := Path(top, f))]
    return found


def _design_finding(root: Path, design: str) -> str | None:
    given = root / DESIGN
    if given.is_symlink() or (given.exists() and not given.is_file()):
        return (f"{DESIGN} is a link or a folder, not the file this build was given (the owner's style). Make it a "
                "plain file again: the host puts the given one back before the next fix.")
    if not given.is_file() or given.read_bytes().decode("utf-8", "replace").splitlines() != design.splitlines():
        return (f"{DESIGN} is not the one this build was given (the owner's style). The host puts the given one back "
                "in the work tree before the fix: commit it as it is, and change the tokens to match it, never the "
                "file.")
    return None


def check(root: Path, design: str) -> Result:
    """Where the product at `root` leaves `design`, the DESIGN.md its build was given."""
    found = [f] if (f := _design_finding(root, design)) else []
    allowed = palette(design)
    sources = _sources(root)
    for path in sources:
        rel = path.relative_to(root).as_posix()
        # Opened without following a link, whatever `_sources` saw a moment ago.
        text = plainfile.read_plain(root, rel, _FILE_BYTES, whole=True) or ""
        for n, line in enumerate(text.splitlines(), 1):
            if off := sorted({c.lower() for c in colours(line) if _six(c) not in allowed}):
                found.append(f"{rel}:{n}: {', '.join(off)} is not a colour in {DESIGN}; use its token.")
            found += [f"{rel}:{n}: text coloured with the fill role --{m[1]}; text takes primary-text."
                      for m in _TEXT_VAR.finditer(line) if _fill_role(m[1])]
    if len(found) > _MAX_FINDINGS:
        found[_MAX_FINDINGS:] = [f"... and {len(found) - _MAX_FINDINGS} more of the same kinds."]
    return Result(len(sources), found)
