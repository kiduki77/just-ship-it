"""A business's look: the accent roles derived from one main colour meet their contrast floors on every pack for any
main, and the DESIGN.md a look makes is its pack's STYLE.md with only the accent changed."""
import difflib

import pytest
import yaml
from justshipit.contracts.look import Look
from justshipit.engineering.accent import contrast, from_oklch
from justshipit.engineering.style_packs import default_look, design_md, frontmatter, packs, read_pack, roles
from pydantic import ValidationError

ROLES = ("primary", "primary-hover", "primary-press", "on-primary", "primary-text", "primary-wash",
         "primary-wash-strong", "primary-border", "highlight")
# The neutral grounds accent text and marks sit on, and the inks that sit on a wash, as each pack names them. The
# first two inks also sit on the strong wash.
GROUNDS = {"linear": ("surface", "canvas", "surface-2", "surface-active"),
           "toss": ("surface", "canvas", "surface-sunk"),
           "stripe": ("canvas", "canvas-soft")}
INKS = {"linear": ("text", "text-2"), "toss": ("text", "text-2", "text-3"), "stripe": ("ink", "ink-2", "ink-3")}


def pack_file(pack: str) -> str:
    return read_pack(packs()[pack].path)


def colors(text: str) -> dict:
    return yaml.safe_load(frontmatter(text)[0].removeprefix("---\n"))["colors"]


def pairs(pack: str, r: dict[str, str]) -> list[tuple[str, str, str, float]]:
    c = {k: v.lower() for k, v in colors(pack_file(pack)).items() if isinstance(v, str)}
    grounds = [(g, c[g]) for g in GROUNDS[pack]] + [("primary-wash", r["primary-wash"])]
    out = [(f"on-primary/{k}", r["on-primary"], r[k], 4.5) for k in ("primary", "primary-hover", "primary-press")]
    for name, ground in grounds:
        out += [(f"primary-text/{name}", r["primary-text"], ground, 4.5),
                (f"primary-border/{name}", r["primary-border"], ground, 3.0),
                (f"highlight/{name}", r["highlight"], ground, 3.0)]
    for i, ink in enumerate(INKS[pack]):
        out.append((f"{ink}/primary-wash", c[ink], r["primary-wash"], 4.5))
        if i < 2:
            out.append((f"{ink}/primary-wash-strong", c[ink], r["primary-wash-strong"], 4.5))
    out.append(("surface/highlight", "#ffffff", r["highlight"], 3.0))
    return out


SWEEP = [from_oklch(L, C, H) for H in range(0, 360, 10) for L in (0.35, 0.5, 0.62, 0.75, 0.88) for C in (0.06, 0.12, 0.2)]


@pytest.mark.parametrize("pack", ["linear", "toss", "stripe"])
def test_every_role_meets_its_floor_for_every_main_in_the_sweep(pack):
    failing = []
    for main in SWEEP:
        r = roles(Look(pack=pack, main=main)).tokens()
        failing += [(main, name, round(contrast(a, b), 2)) for name, a, b, floor in pairs(pack, r)
                    if contrast(a, b) < floor]

    assert len(SWEEP) == 540
    assert failing == []


def test_a_light_main_keeps_its_fill_under_a_dark_label_and_its_pressed_state_stays_readable():
    r = roles(Look(pack="toss", main="#ff7caa"))

    assert (r.primary, r.on_primary) == ("#ff7caa", packs()["toss"].ink)
    assert contrast(r.on_primary, r.primary_press) >= 4.5
    assert r.primary_press != r.primary


def test_highlight_is_the_main_when_it_shows_on_the_canvas_and_the_border_otherwise():
    assert roles(Look(pack="toss", main="#0f2e4f")).highlight == "#0f2e4f"
    pale = roles(Look(pack="toss", main="#facc15"))
    assert pale.highlight == pale.primary_border != "#facc15"


@pytest.mark.parametrize("pack", ["linear", "toss", "stripe"])
def test_the_default_main_gives_back_the_pack_file_byte_for_byte(pack):
    assert design_md(Look(pack=pack, main=packs()[pack].main)) == pack_file(pack)


@pytest.mark.parametrize("pack", ["linear", "toss", "stripe"])
def test_a_new_main_changes_the_nine_accent_values_and_the_derive_main_and_nothing_else(pack):
    original = pack_file(pack)
    made = design_md(Look(pack=pack, main="#ea580c"))

    changed = [line for line in difflib.ndiff(original.splitlines(), made.splitlines()) if line.startswith("+ ")]
    keys = {line[2:].split(":")[0].strip() for line in changed}
    assert keys <= {*ROLES, "accent-derive"} and "accent-derive" in keys and "primary" in keys
    assert len(made.splitlines()) == len(original.splitlines())
    want = roles(Look(pack=pack, main="#ea580c")).tokens()
    got = colors(made)
    assert {k: got[k].lower() for k in ROLES} == want
    assert {k: v for k, v in got.items() if k not in ROLES} == {k: v for k, v in colors(original).items()
                                                                  if k not in ROLES}
    assert yaml.safe_load(frontmatter(made)[0].removeprefix("---\n"))["accent-derive"]["main"].lower() == "#ea580c"


def test_a_business_without_a_pick_gets_the_toss_style_with_its_own_main():
    assert default_look() == Look(pack="toss", main=packs()["toss"].main)


@pytest.mark.parametrize("given,kept", [("#EA580C", "#ea580c"), ("ea580c", "#ea580c"), (" #abc ", "#aabbcc")])
def test_a_main_colour_is_kept_as_lowercase_six_digit_hex(given, kept):
    assert Look(pack="stripe", main=given).main == kept


@pytest.mark.parametrize("bad", [{"pack": "toss", "main": "orange"}, {"pack": "toss", "main": "#ea580"},
                                 {"pack": "material", "main": "#ea580c"}])
def test_a_look_refuses_an_unknown_pack_or_a_colour_that_is_not_hex(bad):
    with pytest.raises(ValidationError):
        Look.model_validate(bad)


@pytest.mark.parametrize("pack", ["linear", "toss", "stripe"])
def test_every_pack_carries_the_shared_rules_as_written(pack):
    shared = read_pack(packs()[pack].path.parents[1] / "SHARED.md").strip("\n")
    text = pack_file(pack)
    start, end = "<!-- shared:start -->", "<!-- shared:end -->"

    assert text[text.index(start) + len(start):text.index(end)].strip("\n") == shared
