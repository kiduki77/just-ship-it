"""Host reads and writes in a directory a sandboxed agent could write (the engineer's clone, an analysis `out/`).

A container can leave a link or a FIFO where the host expects a file. Followed, a link hands the agent any file the
server's OS user can read (var/api_token, .env) and a FIFO hangs the read. So the host follows nothing there: every
part of a path must be a plain directory or file.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


class NotPlain(Exception):
    """A part of the path is a link, a FIFO or a device. `rel` is the path asked for, in POSIX form."""

    def __init__(self, rel: str) -> None:
        super().__init__(rel)
        self.rel = rel


def plain_path(root: Path, rel: str) -> Path:
    """`root / rel`, once every existing part under `root` is a plain directory or file. `root` itself is the
    host's and is not checked."""
    path = root
    for part in Path(rel).parts:
        path = path / part
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            continue
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise NotPlain(Path(rel).as_posix())
    return path


def read_plain(root: Path, rel: str, kept: int, *, whole: bool) -> str | None:
    """The text of the plain file `root / rel`, or None when it is missing, is not a plain file, or sits under a
    link. A file longer than `kept` bytes is None when the `whole` of it is needed; else its last `kept` bytes."""
    try:
        path = plain_path(root, rel)
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or (whole and st.st_size > kept):
                return None
            f.seek(max(0, st.st_size - kept))
            return f.read(kept).decode("utf-8", "replace")
    except (NotPlain, OSError):
        return None


def plain_files(root: Path, rel: str) -> list[str]:
    """The names of the plain files directly in the directory `root / rel`, sorted. Empty when the directory is
    missing, is a link, or sits under one."""
    try:
        with os.scandir(plain_path(root, rel)) as entries:
            return sorted(e.name for e in entries if e.is_file(follow_symlinks=False))
    except (NotPlain, OSError):
        return []
