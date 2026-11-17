"""How the owner can see a delivered build before deciding its merge: the screens the acceptance check captured, and
the commands that run the branch's own preview on the owner's computer. The owner is not a developer, so a merge decided
from text alone is a guess ([평가3] 이준호: "화면을 직접 볼 링크도 없어요").

What was there is recorded at the acceptance check, from the export of the delivered branch (`facts`); the merge card
reads it (`card`). The commands are fixed (clone the branch into its own folder, then scripts/ci-setup.sh and
scripts/preview.sh, which the capture already ran in the sandbox), never words an agent wrote from files the engineer
controls; the card says they run the build's code outside the sandbox. How people use the product once it is merged
stays the after-merge decision's (usable.py). When there is nothing to look at or run, the card says so plainly.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from justshipit.contracts import owner_text
from justshipit.db.models import DevJob
from justshipit.settings import settings

PREVIEW = "scripts/preview.sh"
CI_SETUP = "scripts/ci-setup.sh"
PREVIEW_ADDRESS = "http://127.0.0.1:4173"  # where scripts/preview.sh serves, and what the probes in the sandbox open
# The same server as the owner's browser opens it. A browser sends a cookie to every port of its host, and the console
# answers on 127.0.0.1 only (api/access.py), so under this name the product's code never receives the console's cookie.
OWNER_PREVIEW_ADDRESS = "http://localhost:4173"
SHOT_NAME = re.compile(r"[A-Za-z0-9_-]+\.png")
SHOT_AT = "-1440"   # a capture's file name ends in its width: the planned screens' captures are `{id}-1440.png`
SHOT_ALSO = "-1280"  # the second desktop width of the first open, which repeats the first


def unseen_list(unseen: list[str]) -> str:
    """The unseen screens as one list: an item may hold its own commas, so items are set apart by semicolons."""
    return "; ".join(" ".join(x.split()).rstrip(".;, ") for x in unseen)


def facts(export: Path) -> dict:
    """What the delivered branch has for running it, read from its export before its own scripts ran."""
    return {"preview": (export / PREVIEW).is_file(), "ci_setup": (export / CI_SETUP).is_file()}


def shot(job_id: int, file: str) -> dict:
    """A capture as the console lists it."""
    return {"name": file.removesuffix(".png"), "url": f"/api/dev-jobs/{job_id}/screens/{file}"}


def screen_file(job: DevJob, name: str) -> Path | None:
    """A capture the acceptance check kept for the owner, or None for a name it did not take."""
    files = ((job.result or {}).get("screens") or {}).get("files") or []
    if name not in files or not SHOT_NAME.fullmatch(name):
        return None
    path = settings().var_dir / "dev-screens" / f"J-{job.id}" / name
    return path if path.is_file() else None


WALK_SHOT = re.compile(r"JN\d+\.\d+\.png")
_HEAD = re.compile(r"[0-9a-f]{7,40}")


def walk_dir(job_id: int, head: str) -> Path:
    """Where the walkthrough of the delivered commit `head` keeps its open steps' captures (engineering/walkthrough.py)."""
    return settings().var_dir / "dev-walk" / f"J-{job_id}" / head[:12]


def walk_file(job_id: int, head: str, name: str) -> Path | None:
    """A capture the walkthrough kept, or None for any other name: a step's `<id>.png` under the folder of a head
    given as 7 to 40 hex characters, and a plain file there."""
    if not (WALK_SHOT.fullmatch(name) and _HEAD.fullmatch(head)):
        return None
    path = walk_dir(job_id, head) / name
    try:
        return path if stat.S_ISREG(os.lstat(path).st_mode) else None
    except OSError:
        return None


def clone_steps(job: DevJob, repository: str) -> list[str]:
    """The commands that copy the delivered branch into a folder of its own and enter it, so the product folder stays
    as it is."""
    folder = f"try-{repository}"
    source = (settings().products_dir / repository).as_posix()
    return [f'git clone --branch {job.branch} "{source}" {folder}', f"cd {folder}"]


def card(job: DevJob, repository: str) -> dict:
    """The merge card's way to try the build: {"screens": [{"name", "url"}], "steps": [...], "address", "sample",
    "unseen", "code"}. No screens and no steps means nobody can click it yet. `unseen` names the core screens the
    product agent could not see, which the console says before the screens it did. `code` is the branch, its PR and the
    changed files, which the console opens for an owner who builds software and folds for one who does not."""
    result = job.result or {}
    acceptance = result.get("acceptance") or {}
    found = result.get("try") or {}
    # The desktop width first: the 1280 copy of the first screen repeats the 1440 one.
    files = sorted(((result.get("screens") or {}).get("files") or []), key=lambda f: f.endswith(f"{SHOT_ALSO}.png"))
    screens = [shot(job.id, f) for f in files if SHOT_NAME.fullmatch(f)]
    run = [*([f"sh {CI_SETUP}"] if found.get("ci_setup") else []), f"sh {PREVIEW}"] if found.get("preview") else []
    steps = [*clone_steps(job, repository), *run] if run and job.branch else []
    return {"screens": screens, "steps": steps, "address": OWNER_PREVIEW_ADDRESS if steps else None,
            "sample": acceptance.get("sample_output"),
            "unseen": [said for x in acceptance.get("unseen") or [] if (said := owner_text.scrub(x, line=True).strip())],
            "code": {"branch": job.branch, "pr_url": job.pr_url, "changes": result.get("changes")}
            if job.branch else None}
