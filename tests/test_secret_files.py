"""The files that hold credentials are owner-only or not written: a secrets folder that cannot be restricted takes no
credential, and .env is restricted again each time the settings load."""
import pytest
from justshipit import fileperm
from justshipit import settings as settings_module
from justshipit.services import secrets
from justshipit.settings import settings

TOKEN = "ghp_" + "x" * 36


def test_a_secret_is_not_written_when_its_folder_cannot_be_restricted(monkeypatch):
    def refused(path):
        raise PermissionError(13, "cannot change permissions")
    monkeypatch.setattr(secrets.fileperm, "restrict", refused)

    with pytest.raises(secrets.SecretError) as exc:
        secrets.put("cloudflare_api_token", TOKEN)

    assert TOKEN not in str(exc.value)
    assert secrets.get("cloudflare_api_token") is None
    assert not list((settings().var_dir / "secrets").glob(".cloudflare_api_token*"))


@pytest.fixture
def reloaded_settings():
    yield
    settings.cache_clear()


def test_env_is_restricted_again_when_the_settings_load(tmp_path, monkeypatch, reloaded_settings):
    env = tmp_path / ".env"  # as an editor's save-by-rename leaves it: with the folder's permissions
    env.write_text("SHIP_SMTP_FROM=owner@example.com\n")
    env.chmod(0o644)
    assert not fileperm.owner_only(env)
    monkeypatch.setattr(settings_module, "ENV_FILE", env, raising=False)

    settings.cache_clear()
    settings()

    assert fileperm.owner_only(env)


def test_settings_load_without_an_env_file(tmp_path, monkeypatch, reloaded_settings):
    monkeypatch.setattr(settings_module, "ENV_FILE", tmp_path / ".env", raising=False)

    settings.cache_clear()

    assert settings().var_dir.is_dir()
