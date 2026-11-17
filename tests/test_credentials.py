"""`ship init` gives the compose Postgres a random password, kept owner-only in .env, and moves an install that still
runs on the old default onto it. Running it again changes nothing."""
import secrets as pysecrets

import psycopg
import pytest
from justshipit import fileperm
from justshipit.services import credentials
from justshipit.services.credentials import ensure
from justshipit.settings import Settings, settings
from psycopg import sql
from sqlalchemy.engine import make_url

OLD_ENV = ("SHIP_DATABASE_URL=postgresql+psycopg://ship:ship@127.0.0.1:55432/ship\n"
           "SHIP_SMTP_PASSWORD=keep-me\n")


def _as(role: str, password: str) -> str:
    return make_url(settings().database_url).set(username=role, password=password).render_as_string(False)


def test_init_adds_a_random_password_and_drops_the_old_default_url(tmp_path, monkeypatch):
    for key in ("SHIP_DATABASE_URL", "SHIP_PG_USER", "SHIP_PG_DB", *credentials.PASSWORD_KEYS):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(OLD_ENV)
    said = credentials._write_env(env)
    text = env.read_text()
    s = Settings(_env_file=env)
    assert "SHIP_SMTP_PASSWORD=keep-me" in text and "SHIP_DATABASE_URL" not in text
    assert said == [".env: created SHIP_PG_PASSWORD"], "only the database has a password to create"
    assert len(s.pg_password) >= 32
    assert s.database_url == f"postgresql+psycopg://ship:{s.pg_password}@127.0.0.1:55432/ship"
    assert not any(s.pg_password in line for line in said), "never printed"
    assert fileperm.owner_only(env)
    assert credentials._write_env(env) == [] and env.read_text() == text, "a second init keeps the passwords"


@pytest.fixture
def default_role():
    """A login role on the test server that still has the old default password, as an install before init."""
    role = f"ship_rot_{pysecrets.token_hex(4)}"
    admin = settings().checkpoint_dsn
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'ship'").format(sql.Identifier(role)))
    yield role
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_init_moves_postgres_off_the_default_password_once(default_role):
    new = pysecrets.token_urlsafe(32)
    s = Settings(_env_file=None, pg_password=new, database_url=_as(default_role, new))
    assert "changed the default password" in credentials._rotate_postgres(s)
    psycopg.connect(s.checkpoint_dsn, connect_timeout=5).close()
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(s.checkpoint_dsn, password="ship", connect_timeout=5).close()
    assert "connected with the configured password" in credentials._rotate_postgres(s), "a second init changes nothing"


def test_init_touches_only_the_database(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "_rotate_postgres", lambda s: "Postgres: connected with the configured password")
    try:
        said = ensure(tmp_path / ".env")
    finally:
        settings.cache_clear()
    assert said == [".env: created SHIP_PG_PASSWORD", "Postgres: connected with the configured password"]


def test_the_scram_verifier_is_what_postgres_accepts(default_role):
    with psycopg.connect(settings().checkpoint_dsn, autocommit=True) as c:
        c.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(default_role),
                                                             sql.Literal(credentials.scram_verifier("s3cret"))))
    psycopg.connect(Settings(_env_file=None, database_url=_as(default_role, "s3cret")).checkpoint_dsn,
                    connect_timeout=5).close()


def test_init_rewrites_an_old_env_to_the_new_keys_and_keeps_the_values(tmp_path, monkeypatch):
    for key in ("SHIP_PG_PASSWORD", "SHIP_SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("AITEAM_PG_PASSWORD=pg-old" + chr(10)
                   + "AITEAM_SMTP_PASSWORD=smtp-old" + chr(10)
                   + "AITEAM_DATABASE_URL=postgresql+psycopg://aiteam:aiteam@127.0.0.1:55432/aiteam" + chr(10)
                   + "# AITEAM_NOTE=a comment stays" + chr(10))
    said = credentials._write_env(env)
    text = env.read_text()
    assert said == [], "the password was there, so none is created"
    assert "AITEAM_PG_PASSWORD" not in text and "AITEAM_DATABASE_URL" not in text
    assert "SHIP_PG_PASSWORD=pg-old" in text
    assert "SHIP_SMTP_PASSWORD=smtp-old" in text and "# AITEAM_NOTE=a comment stays" in text
    assert credentials._write_env(env) == [] and env.read_text() == text


def test_a_new_key_wins_over_its_old_twin():
    assert credentials.rename_legacy_keys(["AITEAM_X=old", "SHIP_X=new", "AITEAM_Y=y"]) == ["SHIP_X=new", "SHIP_Y=y"]


def test_settings_still_read_the_old_prefix_when_the_new_one_is_missing(tmp_path, monkeypatch):
    for key in ("SHIP_SMTP_HOST", "SHIP_SMTP_PORT", "SHIP_CLAUDE_BIN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AITEAM_SMTP_HOST", "old.example")
    monkeypatch.setenv("AITEAM_CLAUDE_BIN", "old-claude")
    monkeypatch.setenv("SHIP_CLAUDE_BIN", "new-claude")
    s = Settings(_env_file=None)
    assert s.smtp_host == "old.example", "the old key serves while the new one is missing"
    assert s.claude_bin == "new-claude", "the new key wins"
