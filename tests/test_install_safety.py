"""Step 0.8 follow-ups: a password never shows in a Settings repr, and an import whose branch name git could read as
an option is refused before any paid call."""
from pathlib import Path

import pytest

from justshipit.i18n.said import Refusal
from justshipit.services import existing_product
from justshipit.settings import Settings


def test_the_settings_repr_holds_no_password():
    s = Settings(pg_password="s3cret-for-this-test", database_url="")
    assert "s3cret-for-this-test" in s.database_url  # the value is in use
    assert "s3cret-for-this-test" not in repr(s)
    assert "s3cret-for-this-test" not in str(s)


@pytest.mark.parametrize("branch", ["--output=planted", "-x", "a..b", "feat/.hidden", "x.lock"])
def test_an_import_on_a_branch_git_could_misread_is_refused(monkeypatch, branch):
    monkeypatch.setattr(existing_product, "_git", lambda _repo, *_args: branch)
    with pytest.raises(Refusal):
        existing_product._base_branch(Path("."))


@pytest.mark.parametrize("branch", ["main", "feat/login", "release-1.2"])
def test_an_import_on_a_plain_branch_keeps_its_name(monkeypatch, branch):
    monkeypatch.setattr(existing_product, "_git", lambda _repo, *_args: branch)
    assert existing_product._base_branch(Path(".")) == branch
