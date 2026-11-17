"""Git-backed registry for documents: reports, minutes, PRDs, handoffs, skills.

Git owns document content and versions; PostgreSQL owns status, ownership and approvals.
Approved specs are pinned by commit SHA so later edits cannot leak into an approved handoff.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from justshipit.sandbox import hostgit
from justshipit.settings import settings

_lock = threading.Lock()  # serialize git index operations across worker threads

_LAYOUT_README = """# AI Company Registry

Versioned documents produced by the AI company. Status lives in PostgreSQL.

- `projects/<slug>/` — context, research outputs, meeting minutes, decisions, reports, specs
- `projects/<slug>/specs/<SPEC>/` — prd.md, requirements.yaml, acceptance-criteria.md, design-notes.md, handoff.yaml
- `skills/candidates/`, `skills/approved/` — Skill Factory outputs (Agent Skills format)
- `templates/` — document templates
"""


def root() -> Path:
    return settings().registry_dir


def _git(*args: str, cwd: Path | None = None) -> str:
    """Registry documents carry what agents wrote (a delivery doc holds the engineer's test log, a skill package
    names its own files), so none of the owner's filters, hooks or LFS runs on them. write_files never writes a
    file git reads as settings, so on a git without --attr-source the registry runs without it."""
    out = hostgit.run(cwd or root(), *args, attr_source=hostgit.knows_attr_source())
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def ensure_repo() -> None:
    r = root()
    if (r / ".git").exists():
        return
    r.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main")
    _git("config", "user.name", "AI Company Registry")
    _git("config", "user.email", "registry@ship.local")
    for d in ("projects", "skills/candidates", "skills/approved", "templates"):
        (r / d).mkdir(parents=True, exist_ok=True)
        (r / d / ".gitkeep").touch()
    (r / "README.md").write_text(_LAYOUT_README, encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-m", "Initialize registry")


# Names git reads as its own settings from the tree. Compared the way a case-insensitive file system, or Windows,
# which drops trailing dots and spaces, would open them. A name is ASCII without `:` or `~`, since NTFS opens
# `.git::$INDEX_ALLOCATION` and `GIT~1` as .git, and case folding (dotless i, long s) or a code point HFS+ ignores
# turns a non-ASCII name into one of these.
_GIT_SETTINGS = {".git", ".gitattributes", ".gitmodules", ".lfsconfig"}
# Windows opens these as devices whatever the folder or the extension (`nul.md`, `logs/COM1`).
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"{port}{n}" for port in ("com", "lpt") for n in range(1, 10))}


def _refuse_path(rel: str) -> None:
    """A segment is a name every file system opens as written. A skill package names its own files, so `rel` can be
    an agent's raw output: on Windows `\\` is a separator (`x\\.gitattributes` is a file in `x`), a device name opens
    the device, and a trailing dot or space is dropped."""
    path = PurePosixPath(rel)
    names = {part.casefold().rstrip(". ") or part for part in path.parts}
    devices = {part.casefold().split(".", 1)[0].rstrip(" ") for part in path.parts}
    if path.is_absolute() or ".." in path.parts or names & _GIT_SETTINGS or devices & _WINDOWS_DEVICES \
            or any(part != part.rstrip(". ") for part in path.parts) \
            or not rel.isascii() or ":" in rel or "~" in rel or "\\" in rel:
        raise ValueError(f"registry path {rel}: not saved, since it points outside the registry, is a file git reads as "
                         "settings (.git, .gitattributes, .gitmodules, .lfsconfig), or has a name that could open as one "
                         "or as something else on Windows (non-ASCII characters, `:`, `~`, `\\`, a device name such as "
                         "`nul` or `com1`, a segment that ends in a dot or a space).")


def write_files(files: dict[str, str], message: str, *, remove: Iterable[str] = ()) -> str:
    """Write files (paths relative to registry root) and commit. Returns the commit SHA. `remove` names files the
    same commit takes out, for a document written again that no longer has them; one the registry does not hold is
    left alone."""
    gone = [rel for rel in remove if rel not in files]
    for rel in (*files, *gone):
        _refuse_path(rel)
    from justshipit.services import queue
    queue.check_lease()  # a document of a job another worker owns now is that worker's to write
    ensure_repo()
    with _lock:
        for rel, content in files.items():
            p = root() / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        _git("add", "--", *files.keys())
        gone = _git("ls-files", "--", *gone).splitlines() if gone else []
        if gone:
            _git("rm", "-q", "--", *gone)
        paths = [*files, *gone]
        if not _git("status", "--porcelain", "--", *paths):
            return head()
        _git("commit", "-m", message, "--", *paths)
        return head()


def head() -> str:
    return _git("rev-parse", "HEAD")


def read_at(rel_path: str, commit_sha: str) -> str:
    return _git("show", f"{commit_sha}:{rel_path}")


def read(rel_path: str) -> str:
    """A file inside the registry. A path that resolves outside it (`..`, an absolute path, a planted link) is
    refused, because Artifact.path and the paths built from it come from the database, not from this module."""
    base = root().resolve()
    path = (base / rel_path).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"registry path {rel_path}: not read, since it points outside the registry.")
    return path.read_text(encoding="utf-8")


def tree_at(rel_dir: str, commit_sha: str) -> list[str]:
    out = _git("ls-tree", "-r", "--name-only", commit_sha, "--", rel_dir)
    return [line for line in out.splitlines() if line]
