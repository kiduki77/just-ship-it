"""A business's look: the style pack its new products are built from and the one main colour the owner picked
(`Look`, kept on the business), and the nine accent roles derived from that colour (`AccentRoles`,
`engineering/accent.py`)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

PackSlug = Literal["linear", "toss", "stripe"]

_HEX = re.compile(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})")


def hex_color(value: str) -> str:
    """`#rrggbb` in lowercase, from `#RGB`, `#RRGGBB` or either without the `#`."""
    m = _HEX.fullmatch(value.strip())
    if m is None:
        raise ValueError(f"{value!r} is not a hex colour like #3182f6")
    digits = m.group(1).lower()
    return "#" + ("".join(c * 2 for c in digits) if len(digits) == 3 else digits)


HexColor = Annotated[str, AfterValidator(hex_color)]


class Look(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pack: PackSlug
    main: HexColor


@dataclass(frozen=True, slots=True)
class AccentRoles:
    """Exactly the roles a pack's frontmatter names for its accent, each a lowercase `#rrggbb`. The STYLE.md and CSS
    names are these with hyphens (`primary-hover`)."""
    primary: str
    primary_hover: str
    primary_press: str
    on_primary: str
    primary_text: str
    primary_wash: str
    primary_wash_strong: str
    primary_border: str
    highlight: str

    def tokens(self) -> dict[str, str]:
        return {name.replace("_", "-"): value for name, value in asdict(self).items()}
