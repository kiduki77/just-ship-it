"""The accent roles of a style pack, derived from the one main colour the owner picked.

Hue is kept; lightness moves in OKLCH until each role meets its contrast floor against the pack's darkest ground
(`canvas`) and its body ink. A role never fails its floor, for any sRGB main: tests/test_look.py sweeps the grid.
"""
from __future__ import annotations

import math

from justshipit.contracts.look import AccentRoles

WHITE = "#ffffff"
TEXT_FLOOR, MARK_FLOOR = 4.5, 3.0
_STEP = 0.005  # of OKLCH lightness per try


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb: tuple[float, ...]) -> str:
    return "#" + "".join(f"{round(min(1, max(0, c)) * 255):02x}" for c in rgb)


def to_oklch(h: str) -> tuple[float, float, float]:
    r, g, b = (_lin(c) for c in _rgb(h))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _oklch_rgb(L: float, C: float, H: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-4 <= c <= 1 + 1e-4 for c in rgb)


def from_oklch(L: float, C: float, H: float) -> str:
    """Chroma is reduced into the sRGB gamut rather than channels clipped, so the hue holds."""
    rgb = _oklch_rgb(L, C, H)
    if not _in_gamut(rgb):
        lo, hi = 0.0, C
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _in_gamut(_oklch_rgb(L, mid, H)) else (lo, mid)
        rgb = _oklch_rgb(L, lo, H)
    return _hex(tuple(_gam(max(0, c)) for c in rgb))


def _luminance(h: str) -> float:
    r, g, b = (_lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _darken_until(h: str, against: str, floor: float) -> str:
    L, C, H = to_oklch(h)
    out = h
    while contrast(out, against) < floor and 0 < L < 1:
        L -= _STEP
        out = from_oklch(L, C, H)
    return out


def _state(fill: str, on: str, shift: float) -> str:
    """The fill `shift` darker in lightness, kept as dark as its label allows. Darkening only helps a white label;
    a dark label on a light main loses contrast, so the state backs off toward the fill, which passes."""
    L, C, H = to_oklch(fill)
    out = from_oklch(L + shift, C, H)
    while contrast(on, out) < TEXT_FLOOR and shift < 0:
        shift = min(0.0, shift + _STEP)
        out = from_oklch(L + shift, C, H) if shift else fill
    return out


def derive(main: str, canvas: str, ink: str) -> AccentRoles:
    """The nine roles for `main` (lowercase `#rrggbb`) in a pack whose darkest ground is `canvas` and body ink `ink`."""
    L, C, H = to_oklch(main)
    # The button keeps the owner's exact colour when some label can sit on it: white, or the ink on a light main.
    if contrast(WHITE, main) >= TEXT_FLOOR:
        fill, on = main, WHITE
    elif contrast(ink, main) >= TEXT_FLOOR and L >= 0.72:
        fill, on = main, ink
    else:
        fill, on = _darken_until(main, WHITE, TEXT_FLOOR), WHITE
    shift = -0.05 if on == WHITE else -0.06
    wash = from_oklch(0.965, min(C * 0.22, 0.03), H)
    border = _darken_until(main, canvas, MARK_FLOOR)
    return AccentRoles(
        primary=fill,
        primary_hover=_state(fill, on, shift),
        primary_press=_state(fill, on, 2 * shift),
        on_primary=on,
        primary_text=_darken_until(_darken_until(main, canvas, TEXT_FLOOR), wash, TEXT_FLOOR),
        primary_wash=wash,
        primary_wash_strong=from_oklch(0.925, min(C * 0.4, 0.06), H),
        primary_border=border,
        highlight=main if contrast(main, canvas) >= MARK_FLOOR else border,
    )
