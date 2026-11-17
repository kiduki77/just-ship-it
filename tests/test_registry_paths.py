"""The registry's path check on names Windows opens as something else. A skill package names its own files, so a
path can be an agent's raw output (capability/factory.py)."""
import pytest
from justshipit.services import registry


@pytest.mark.parametrize("rel", [
    # `\` separates folders on Windows, so these are .gitattributes in x and config in .git.
    "x\\.gitattributes", ".git\\config", "skills\\candidates\\x\\SKILL.md", "skills/x/..\\..\\escape.md",
    # Device names, in any folder, any case, with or without an extension.
    "nul", "skills/x/CON", "skills/x/prn.md", "aux.tar.gz", "skills/com1/SKILL.md", "LPT9.txt", "skills/x/nul .md",
    # Windows drops a trailing dot or space, so two names open one file.
    "skills/x/SKILL.md.", "skills/x /SKILL.md", "skills/x./SKILL.md", "skills/x/SKILL.md ",
])
def test_a_name_windows_opens_as_something_else_is_refused_and_nothing_is_written(rel):
    with pytest.raises(ValueError, match="registry path"):
        registry.write_files({"projects/ok-windows.md": "ok\n", rel: "* filter=owner\n"}, "planted")

    assert not (registry.root() / "projects/ok-windows.md").exists(), "nothing of the refused write lands"
    assert not (registry.root() / "x" / ".gitattributes").exists()


@pytest.mark.parametrize("rel", [
    "skills/candidates/console-notes/v1/SKILL.md", "projects/p/con-notes.md", "projects/p/com10.md",
    "projects/p/nullable.md", "projects/p/.hidden/notes.v2.md", "projects/p/auxiliary/lpt.md",
])
def test_an_ordinary_name_that_only_looks_like_one_is_still_saved(rel):
    registry._refuse_path(rel)
