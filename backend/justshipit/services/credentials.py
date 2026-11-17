"""The Postgres password of this install: random, kept in .env (owner-only), never printed.

`ship init` runs ensure(). It adds the password to .env when it is missing, then moves the compose Postgres onto it
by ALTER ROLE, only while the server still takes the old default password "ship". docker-compose.yml reads the same
.env line, so a restarted service keeps the new password. A second run finds everything in place and changes
nothing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

import psycopg
from psycopg import sql

from justshipit import fileperm
from justshipit.settings import DEFAULT_PG_PASSWORD, Settings, settings

PASSWORD_KEYS = ("SHIP_PG_PASSWORD",)
# What .env held before passwords existed. Such a line only restates the default and would hide the new password.
_OLD_DEFAULT_LINES = {"SHIP_DATABASE_URL=postgresql+psycopg://ship:ship@127.0.0.1:55432/ship",
                      "AITEAM_DATABASE_URL=postgresql+psycopg://aiteam:aiteam@127.0.0.1:55432/aiteam"}


def rename_legacy_keys(lines: list[str]) -> list[str]:
    """.env lines from before the rename with their AITEAM_ keys spelled SHIP_. A key the file already has in the new
    spelling wins and its old twin is dropped. Values are carried as they are and never looked at."""
    have = {line.partition("=")[0].strip() for line in lines if "=" in line and not line.lstrip().startswith("#")}
    out = []
    for line in lines:
        key, eq, rest = line.partition("=")
        if eq and key.strip().startswith("AITEAM_") and not line.lstrip().startswith("#"):
            new = "SHIP_" + key.strip()[len("AITEAM_"):]
            if new in have:
                continue
            line = f"{new}={rest}"
        out.append(line)
    return out


def ensure(env_path: Path) -> list[str]:
    """Lines for the owner saying what changed, none of them carrying a password."""
    out = _write_env(env_path)
    settings.cache_clear()
    s = settings()
    out.append(_rotate_postgres(s))
    return out


def _write_env(env_path: Path) -> list[str]:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    kept = rename_legacy_keys([line for line in lines if line.strip() not in _OLD_DEFAULT_LINES])
    present = {line.partition("=")[0].strip() for line in kept if "=" in line and not line.lstrip().startswith("#")}
    added = [key for key in PASSWORD_KEYS if key not in present]
    kept += [f"{key}={secrets.token_urlsafe(32)}" for key in added]
    if kept != lines:
        tmp = env_path.with_name(f".env.{secrets.token_hex(4)}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(kept) + "\n")
        fileperm.restrict(tmp)
        os.replace(tmp, env_path)
    elif env_path.exists():
        fileperm.restrict(env_path)
    return [f".env: created {key}" for key in added]


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores (RFC 5802/7677), made here as psql's \\password does, so the
    password itself never reaches the server or its statement log."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")
    salt64, stored64, server64 = (base64.b64encode(b).decode() for b in (salt, stored, server))
    return f"SCRAM-SHA-256${iterations}:{salt64}${stored64}:{server64}"


def _rotate_postgres(s: Settings) -> str:
    try:
        psycopg.connect(s.checkpoint_dsn, connect_timeout=5).close()
        return "Postgres: connected with the configured password"
    except psycopg.OperationalError:
        pass
    with psycopg.connect(s.checkpoint_dsn, password=DEFAULT_PG_PASSWORD, connect_timeout=5, autocommit=True) as c:
        c.execute(sql.SQL("ALTER ROLE CURRENT_USER PASSWORD {}").format(sql.Literal(scram_verifier(s.pg_password))))
    psycopg.connect(s.checkpoint_dsn, connect_timeout=5).close()
    return "Postgres: changed the default password to the one in .env"

