"""Connector credentials the owner enters in Settings, one file each under var/secrets/. On POSIX the folder is 0700
and each file 0600. On Windows the folder gets a protected DACL for the current user only, as var/ does at startup
(justshipit.fileperm), and each file inherits it.

Only names in REGISTRY are accepted, so a name never becomes a path. A value goes from the API request to the
file and to `get()` for the connector that uses it. It is never returned by the API (only whether it is set and its
last four characters), logged, put in an exception, written to an activity event, or passed on a command line.
"""
from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

from justshipit import fileperm
from justshipit.i18n import DEFAULT, Lang, msg
from justshipit.settings import settings

REGISTRY = ("slack_bot_token", "slack_app_token", "gmail_oauth", "github_token", "cloudflare_api_token")
MAX_LEN = 16384
SLACK_PREFIX = {"slack_bot_token": "xoxb-", "slack_app_token": "xapp-"}
_SHOW_LAST4_FROM = 16


def label(name: str, lang: Lang) -> str:
    """The credential's name as the owner reads it."""
    return msg(f"settings.secret.{name}", lang)


class SecretError(Exception):
    """A refusal for the owner, kept as its catalog key so the route says it in the owner's language (`say`). It
    never carries a value. Its text, for logs, is the English."""

    def __init__(self, key: str, **values: object) -> None:
        self.key, self.values = key, values
        super().__init__(self.say(DEFAULT))

    def say(self, lang: Lang) -> str:
        values = {k: label(str(v), lang) if k == "label" else v for k, v in self.values.items()}
        return msg(f"settings.secret.{self.key}", lang, **values)


def format_problem(name: str, value: str) -> SecretError | None:
    """Why a value cannot be this credential, or None. A Slack token is printable ASCII with no space and starts
    with its prefix; anything else cannot go into an Authorization header, so it is refused when it is saved and
    again before it is sent."""
    prefix = SLACK_PREFIX.get(name)
    if prefix is None:
        return None
    if not value.isascii() or not value.isprintable() or any(c.isspace() for c in value):
        return SecretError("not_text", label=name)
    if not value.startswith(prefix):
        return SecretError("prefix", label=name, prefix=prefix)
    return None


def _dir() -> Path:
    return settings().var_dir / "secrets"


def _path(name: str) -> Path:
    if name not in REGISTRY:
        raise SecretError("unknown")
    return _dir() / name


def _regular(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise SecretError("not_a_file", file=path.name)
    return True


def get(name: str) -> str | None:
    """The stored value for a connector to use, or None when the owner has not set it."""
    path = _path(name)
    if not _regular(path):
        return None
    return path.read_text(encoding="utf-8").strip() or None


def put(name: str, value: str) -> None:
    path = _path(name)
    value = value.strip()
    if not value:
        raise SecretError("empty")
    if len(value) > MAX_LEN or "\x00" in value:
        raise SecretError("too_long")
    if problem := format_problem(name, value):
        raise problem
    try:
        data = value.encode("utf-8")
    except UnicodeEncodeError:
        raise SecretError("unencodable") from None
    directory = _dir()
    if directory.is_symlink():
        raise SecretError("symlink")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        fileperm.restrict(directory)
    except OSError:
        raise SecretError("write_failed") from None  # a folder others can read holds no credential
    tmp = directory / f".{name}.{uuid.uuid4().hex}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException as exc:
        tmp.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise SecretError("write_failed") from None
        raise


def clear(name: str) -> bool:
    path = _path(name)
    if not _regular(path):
        return False
    path.unlink()
    return True


def status(lang: Lang) -> dict[str, dict[str, object]]:
    """What the API shows for each credential: set or not, a label, and the last four characters of a long one."""
    out: dict[str, dict[str, object]] = {}
    for name in REGISTRY:
        try:
            value = get(name)
        except SecretError as exc:
            out[name] = {"label": label(name, lang), "set": False, "last4": None, "problem": exc.say(lang)}
            continue
        out[name] = {"label": label(name, lang), "set": value is not None,
                     "last4": value[-4:] if value and len(value) >= _SHOW_LAST4_FROM else None}
    return out
