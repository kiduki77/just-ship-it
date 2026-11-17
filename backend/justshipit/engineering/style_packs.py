"""The style packs a new product is built from (`prompts/styles/<slug>/STYLE.md`) and the DESIGN.md a business's look
makes of one: the pack's file with its accent roles derived from the owner's main colour.

Each pack's file is the single source of its accent inputs: its frontmatter's `accent-derive: {main, canvas, ink}`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import get_args

import yaml
from sqlalchemy import select

from justshipit.contracts.look import AccentRoles, Look, PackSlug, hex_color
from justshipit.db.models import Business, Project
from justshipit.engineering.accent import derive
from justshipit.settings import settings

DEFAULT_PACK: PackSlug = "toss"
_FRONT_END = "\n---"
_HEX = r"#[0-9a-fA-F]{6}"


@dataclass(frozen=True)
class Pack:
    slug: PackSlug
    path: Path
    main: str
    canvas: str
    ink: str

    def roles(self, main: str) -> AccentRoles:
        return derive(main, self.canvas, self.ink)


def frontmatter(text: str) -> tuple[str, str]:
    """The frontmatter (from the opening `---`) and the rest of the file, the closing `---` on."""
    if not text.startswith("---\n"):
        raise ValueError("a style pack starts with a --- frontmatter")
    end = text.index(_FRONT_END, 3)
    return text[:end], text[end:]


def from_pack(text: str) -> bool:
    """Whether a DESIGN.md is one a style pack made: its frontmatter names the pack's `accent-derive` inputs. An
    engineer-written DESIGN.md (a product built before the packs) has none."""
    try:
        front = yaml.safe_load(frontmatter(text)[0].removeprefix("---\n"))
    except (ValueError, yaml.YAMLError):
        return False
    return isinstance(front, dict) and "accent-derive" in front


def read_pack(path: Path) -> str:
    """The file with its lines ended by LF, whatever the checkout did to them."""
    return path.read_text(encoding="utf-8")


def _load(slug: PackSlug) -> Pack:
    path = settings().prompts_dir / "styles" / slug / "STYLE.md"
    inputs = yaml.safe_load(frontmatter(read_pack(path))[0].removeprefix("---\n"))["accent-derive"]
    return Pack(slug, path, *(hex_color(inputs[k]) for k in ("main", "canvas", "ink")))


@cache
def packs() -> dict[PackSlug, Pack]:
    return {slug: _load(slug) for slug in get_args(PackSlug)}


def default_look() -> Look:
    return Look(pack=DEFAULT_PACK, main=packs()[DEFAULT_PACK].main)


def roles(look: Look) -> AccentRoles:
    return packs()[look.pack].roles(look.main)


def _cased(value: str, like: str) -> str:
    return value.upper() if like != like.lower() else value


def _colors_block(front: str) -> tuple[int, int]:
    """Where the frontmatter's top-level `colors:` block runs: its children are the indented lines after it."""
    start = re.search(r"(?m)^colors:[ \t]*$", front)
    if start is None:
        raise ValueError("a style pack's frontmatter has no colors: block")
    nxt = re.compile(r"(?m)^\S").search(front, start.end() + 1)
    return start.end(), nxt.start() if nxt else len(front)


def design_md(look: Look) -> str:
    """The pack's STYLE.md with `accent-derive.main` set to the look's main and the nine accent values in `colors:`
    derived from it, each in the case the file writes it. Every other byte is the file's."""
    pack = packs()[look.pack]
    front, rest = frontmatter(read_pack(pack.path))
    front, n = re.subn(rf'(?m)^(accent-derive:.*?\bmain:\s*")({_HEX})"',
                       lambda m: f'{m[1]}{_cased(look.main, m[2])}"', front, count=1)
    if n != 1:
        raise ValueError(f"{pack.path}: no accent-derive main")
    a, b = _colors_block(front)
    block = front[a:b]
    for role, value in pack.roles(look.main).tokens().items():
        block, n = re.subn(rf'(?m)^([ \t]+{re.escape(role)}:[ \t]*")({_HEX})"',
                           lambda m, v=value: f'{m[1]}{_cased(v, m[2])}"', block)
        if n != 1:
            raise ValueError(f"{pack.path}: colors: names {role} {n} times, not once")
    return front[:a] + block + front[b:] + rest


def business_look(b: Business | None) -> Look:
    """The look the owner picked for the business, the default one until they pick."""
    return Look.model_validate(b.look) if b is not None and b.look else default_look()


def project_look(s, project_slug: str) -> Look:
    """The look of the business a project belongs to; a project with no business gets the default."""
    return business_look(s.scalar(select(Business).join(Project, Project.business_id == Business.id)
                                  .where(Project.slug == project_slug)))
