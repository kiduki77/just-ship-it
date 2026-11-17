from __future__ import annotations

import hashlib
import os
import socket
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psycopg
import yaml
from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from justshipit import fileperm

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"  # holds the Postgres and SMTP passwords

def _machine() -> str:
    """A short id of the kernel this process runs on, so two that share a hostname but not their pids (Windows and its
    WSL, cloned VMs) never read as one host; empty where the system keeps none."""
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                said = str(winreg.QueryValueEx(key, "MachineGuid")[0])
        else:
            said = next(p.read_text().strip() for p in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))
                        if p.is_file())
    except (OSError, StopIteration):
        return ""
    return hashlib.sha256(said.encode()).hexdigest()[:6] if said else ""


# The host part of a worker id: the hostname, and the kernel's id when there is one (runner._pid_here reads it).
MACHINE = _machine()
HOST = socket.gethostname()[:40] + (f".{MACHINE}" if MACHINE else "")  # ids fit String(80)
# This process as the lease owner of the jobs it runs and the owner of the containers it starts. Unique per
# start, so a restarted worker (same hostname and pid in a container) never inherits a dead one's leases.
PROCESS_ID = f"{HOST}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
DEFAULT_PG_PASSWORD = "ship"
LEGACY_PREFIX = "AITEAM_"  # the env prefix before the rename; read for one release when the SHIP_ key is missing
LEGACY_DEFAULT_DATABASE_URL = "postgresql+psycopg://aiteam:aiteam@127.0.0.1:55432/aiteam"


def _legacy_env() -> dict[str, str]:
    """The AITEAM_ settings of the environment and of .env, keyed by field name, so an install that predates the
    rename keeps working until `ship init` rewrites its .env. The SHIP_ source comes first and wins."""
    out: dict[str, str] = {}
    file = ENV_FILE
    if file.is_file():
        from dotenv import dotenv_values
        out.update({k: v for k, v in dotenv_values(file).items() if v is not None})
    out.update(os.environ)
    found = {k[len(LEGACY_PREFIX):].lower(): v for k, v in out.items() if k.startswith(LEGACY_PREFIX)}
    if found.get("database_url") == LEGACY_DEFAULT_DATABASE_URL:  # the old default names the old database: drop it
        del found["database_url"]
    return found


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHIP_", env_file=ENV_FILE, extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls: type[BaseSettings], init_settings: PydanticBaseSettingsSource,
                                   env_settings: PydanticBaseSettingsSource,
                                   dotenv_settings: PydanticBaseSettingsSource,
                                   file_secret_settings: PydanticBaseSettingsSource):
        legacy = lambda: {k: v for k, v in _legacy_env().items() if k in cls.model_fields}
        return (init_settings, env_settings, dotenv_settings, file_secret_settings, legacy)

    # `ship init` writes random ones to .env (services/credentials.py), and docker-compose.yml reads them there.
    # "ship" is what a compose stack started before init has.
    pg_user: str = "ship"
    pg_db: str = "ship"
    pg_password: str = Field(default=DEFAULT_PG_PASSWORD, repr=False)
    # Empty: the compose Postgres, with the password above.
    database_url: str = Field(default="", repr=False)  # it holds the password
    claude_bin: str = ""
    # Live email (connectors.email.mode: live) and optional OpenTelemetry export; .env keeps these secrets out
    # of the shell, and so out of every process the worker starts.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "ai-company@localhost"
    otel_endpoint: str = ""
    otel_headers: str = ""

    root: Path = ROOT
    config_dir: Path = ROOT / "config"
    prompts_dir: Path = ROOT / "prompts"
    registry_dir: Path = ROOT / "registry"
    products_dir: Path = ROOT / "products"
    var_dir: Path = ROOT / "var"

    _deployment_id: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _service_urls(self) -> Settings:
        if not self.database_url:
            self.database_url = (f"postgresql+psycopg://{self.pg_user}:{quote(self.pg_password, safe='')}"
                                 f"@127.0.0.1:55432/{self.pg_db}")
        return self

    @property
    def deployment_id(self) -> str:
        """One id per database, so a worker only touches containers of its own deployment: a test run or a
        second checkout on the same Docker daemon never reaps the owner's engineer containers. The database
        names itself (cluster identifier plus database name), so two spellings of one URL agree. While the
        database cannot answer there is no id: a container labelled with a guess would escape the reaper, so
        the caller fails instead and asks again next time."""
        if self._deployment_id is None:
            with psycopg.connect(self.checkpoint_dsn, connect_timeout=2) as c:
                sysid, db = c.execute("SELECT system_identifier, current_database() FROM pg_control_system()"
                                      ).fetchone()
            self._deployment_id = hashlib.sha256(f"{sysid}:{db}".encode()).hexdigest()[:12]
        return self._deployment_id

    @property
    def checkpoint_dsn(self) -> str:
        # LangGraph's PostgresSaver takes a plain libpq DSN.
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")


@lru_cache
def settings() -> Settings:
    s = Settings()
    s.var_dir.mkdir(parents=True, exist_ok=True)
    fileperm.restrict(s.var_dir)
    # `ship init` restricts .env once; an editor that saves by rename gives the new file the folder's permissions.
    if ENV_FILE.is_file():
        fileperm.restrict(ENV_FILE)
    return s


def _load_yaml(name: str) -> dict[str, Any]:
    with open(settings().config_dir / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def company_config() -> dict[str, Any]:
    return _load_yaml("company.yaml")


@lru_cache
def staffing_config() -> dict[str, Any]:
    return _load_yaml("staffing.yaml")


@lru_cache
def agents_config() -> dict[str, dict[str, Any]]:
    return _load_yaml("agents.yaml")["agents"]


@lru_cache
def tool_policy() -> dict[str, str]:
    return _load_yaml("tool_policy.yaml")["actions"]


def limits() -> dict[str, Any]:
    return company_config()["limits"]
