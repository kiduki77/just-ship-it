"""A new product with screens is built from the look the owner picked for its business: the worker puts that DESIGN.md
at the repository root, the engineer is told it is given and never to rewrite it, and the reviewer gets the host's
look check of the branch. The file is read once per run from the commit the tree is made from, so a product keeps the
DESIGN.md it was delivered in. A product that had code, or whose DESIGN.md is its own, keeps its look and gets none of
this."""
from pathlib import Path

from justshipit.contracts.handoff import CodeFindingOut, CodeReviewOut, Handoff
from justshipit.contracts.look import Look
from justshipit.db.models import Business, DevJob, Spec
from justshipit.engineering import style_packs, worker
from justshipit.engineering.style_packs import design_md
from justshipit.services import projects, registry
from justshipit.settings import settings
from people_kit import make_business
from test_engineering import _product_repo, _sh
from test_product_quality import _outputs, _plan_and_build, _told_engineer

LEGACY = "# Design\n\nThe engineer's own tokens: ink #111111, accent #ff00aa.\n"


def _handoff(project: str, repo: str = "demo", *, screens: bool = True, existing_code: bool = False) -> Handoff:
    sha = registry.write_files({"specs/look/prd.md": "p", "specs/look/design-brief.md": "b"}, "look spec")
    return Handoff(spec_id="SPEC-LOOK", project=project, title="t", source={"path": "specs/look", "commit_sha": sha},
                   target={"repository": repo}, workflow={"screens": screens, "existing_code": existing_code},
                   approval={"spec_approved": True, "implementation_authorized": True})


def _project_with_look(s, look: Look | None):
    b = make_business(s)
    b.look = look.model_dump() if look else None
    p = projects.create(s, "주문 정리", name="Look product", business=b, start=False)
    s.commit()
    return p


def _set_look(s, p, look: Look) -> None:
    s.get(Business, p.business_id).look = look.model_dump()
    s.commit()


def _commit(repo: Path, files: dict[str, str], branch: str | None = None) -> str:
    if branch:
        _sh(repo, "git", "checkout", "-qB", branch)
    for rel, text in files.items():
        (repo / rel).write_text(text, encoding="utf-8", newline="")
    _sh(repo, "git", "add", "-A")
    _sh(repo, "git", "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-qm", "delivered")
    sha = _sh(repo, "git", "rev-parse", "HEAD")
    _sh(repo, "git", "checkout", "-q", "main")
    return sha


def _tree(s, p, repo: Path, tmp_path, monkeypatch, *, fix: tuple[int, str] | None = None,
          existing_code: bool = False):
    """prepare_worktree for a build of `p` in `repo`, the way the containerized worker makes it."""
    h = _handoff(p.slug, repo.name, existing_code=existing_code)
    spec = Spec(spec_key=f"SPEC-LOOK-{repo.name.upper()}", project_id=p.id, title="t", registry_path="specs/look",
                approved_commit_sha=h.source.commit_sha, handoff=h.model_dump(mode="json"))
    s.add(spec)
    s.commit()
    monkeypatch.setattr(worker, "_containerized", lambda: True)
    monkeypatch.setattr(worker, "company_config", lambda: {"engineering": {"isolation": "container",
                                                                           "worktrees_dir": "wt"}})
    monkeypatch.setattr(worker, "settings", lambda: settings().model_copy(update={"root": tmp_path}))
    _, wt, _, given = worker.prepare_worktree(spec, h, 1, fix=fix)
    return wt, given


def test_a_first_build_gets_the_design_md_of_its_business_look(s, tmp_path, monkeypatch):
    p = _project_with_look(s, Look(pack="linear", main="#EA580C"))
    repo = _product_repo(f"look-first-{p.id}")

    wt, given = _tree(s, p, repo, tmp_path, monkeypatch)

    rendered = design_md(Look(pack="linear", main="#ea580c"))
    assert given == rendered and (wt / "DESIGN.md").read_bytes().decode("utf-8") == rendered
    accent = style_packs.roles(Look(pack="linear", main="#ea580c"))
    assert f'  primary: "{accent.primary}"' in rendered and '"#5e6ad2"' not in rendered


def test_a_business_that_has_not_picked_builds_in_the_default_look(s, tmp_path, monkeypatch):
    p = _project_with_look(s, None)

    _, given = _tree(s, p, _product_repo(f"look-default-{p.id}"), tmp_path, monkeypatch)

    assert given == design_md(style_packs.default_look())


def test_a_fix_round_after_the_owner_changed_the_look_keeps_the_delivered_design_md(s, tmp_path, monkeypatch):
    p = _project_with_look(s, Look(pack="toss", main="#16a34a"))
    repo = _product_repo(f"look-fix-{p.id}")
    delivered = design_md(Look(pack="toss", main="#16a34a"))
    sha = _commit(repo, {"DESIGN.md": delivered}, branch="ai/spec-look-a1")
    _set_look(s, p, Look(pack="stripe", main="#ea580c"))

    wt, given = _tree(s, p, repo, tmp_path, monkeypatch, fix=(1, sha))

    assert given == delivered and (wt / "DESIGN.md").read_text(encoding="utf-8") == delivered


def test_a_later_build_on_a_product_we_built_keeps_the_design_md_on_its_base(s, tmp_path, monkeypatch):
    p = _project_with_look(s, Look(pack="linear", main="#2563eb"))
    repo = _product_repo(f"look-later-{p.id}")
    merged = design_md(Look(pack="linear", main="#2563eb"))
    _commit(repo, {"DESIGN.md": merged})
    _set_look(s, p, Look(pack="toss", main="#ea580c"))

    wt, given = _tree(s, p, repo, tmp_path, monkeypatch)

    assert given == merged and (wt / "DESIGN.md").read_text(encoding="utf-8") == merged


def test_a_design_md_of_its_own_is_never_overwritten_nor_held_to_a_pack(s, tmp_path, monkeypatch):
    """A product delivered before the style packs came with the engineer's own DESIGN.md."""
    p = _project_with_look(s, Look(pack="toss", main="#3182f6"))
    repo = _product_repo(f"look-legacy-{p.id}")
    sha = _commit(repo, {"DESIGN.md": LEGACY}, branch="ai/spec-look-a1")

    wt, given = _tree(s, p, repo, tmp_path, monkeypatch, fix=(1, sha))

    assert given is None and (wt / "DESIGN.md").read_text(encoding="utf-8") == LEGACY


def test_the_acceptance_export_gets_back_only_a_design_md_a_pack_made(s):
    """The CPO reads the delivered branch after its scripts ran: the pack's file is put back as committed, and a
    product's own DESIGN.md, or none, is never replaced by a rendered one."""
    repo = _product_repo("look-accept")
    delivered = design_md(Look(pack="stripe", main="#16a34a"))
    _commit(repo, {"DESIGN.md": delivered}, branch="ai/pack")
    _commit(repo, {"DESIGN.md": LEGACY}, branch="ai/own")

    assert worker.pack_design(repo, "ai/pack") == delivered
    assert worker.pack_design(repo, "ai/own") is None and worker.pack_design(repo, "main") is None


def test_a_product_with_code_or_without_screens_is_given_no_design_md(s, tmp_path, monkeypatch):
    p = _project_with_look(s, Look(pack="stripe", main="#16a34a"))
    repo = _product_repo(f"look-old-{p.id}")

    wt, given = _tree(s, p, repo, tmp_path, monkeypatch, existing_code=True)

    assert given is None and not (wt / "DESIGN.md").exists()
    assert worker.given_design(repo, "main", _handoff(p.slug, repo.name, screens=False)) is None


def test_the_engineer_is_told_design_md_is_given_and_never_to_rewrite_it(s, project, tmp_path, monkeypatch):
    given = design_md(style_packs.default_look())
    plan, build = _plan_and_build(_told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug),
                                                 given=given))

    for told in (plan, build):
        assert "`DESIGN.md` at the repository root is given" in told and "never rewrite or edit it" in told
        assert "archetype" in told
    assert "Write DESIGN.md" not in build

    other = projects.create(s, "주문 정리", name="Its own look", start=False)
    old_plan, old_build = _plan_and_build(_told_engineer(s, other, tmp_path / "old", monkeypatch,
                                                         _handoff(other.slug)))
    assert "is given" not in old_plan + old_build and "keep their current look" in old_build


def _reviews(*verdicts: CodeReviewOut):
    left = iter(verdicts)
    return lambda model: next(left) if model is CodeReviewOut else _outputs(model)


_FIX = CodeReviewOut(verdict="fix_required", summary="off", spec_coverage=[], findings=[CodeFindingOut(
    severity="blocking", file="src/app.css", location=None, issue="off-palette", suggestion="use the token")])
_PASS = CodeReviewOut(verdict="pass", summary="ok", spec_coverage=[], findings=[])


def test_the_reviewer_and_the_fix_round_get_the_look_check_and_the_tree_gets_the_file_back(s, project, tmp_path,
                                                                                              monkeypatch):
    given = design_md(style_packs.default_look())

    def export(repo, branch, dest: Path) -> None:
        (dest / "src").mkdir(parents=True, exist_ok=True)
        (dest / "DESIGN.md").write_text(given.replace("---\n", "---\n# edited\n", 1), encoding="utf-8")
        (dest / "src" / "app.css").write_text(".x { background: #abcdef; }\n", encoding="utf-8")

    (tmp_path / "DESIGN.md").write_text("the engineer's edit", encoding="utf-8")
    told = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug), export=export,
                          outputs=_reviews(_FIX, _PASS), given=given)

    review = next(p for p in told if p.startswith("Review the current branch"))
    fix = next(p for p in told if p.startswith("Fix the problems below"))
    for prompt in (review, fix):
        assert "## Look check" in prompt and "It read 1 files of screen code." in prompt
        assert "src/app.css:1: #abcdef is not a colour in DESIGN.md" in prompt
        assert "DESIGN.md is not the one this build was given" in prompt
    assert (tmp_path / "DESIGN.md").read_text(encoding="utf-8") == given, "put back before the fix round"


def test_a_design_md_made_a_folder_is_a_finding_for_the_fix_round_not_a_failed_job(s, project, tmp_path,
                                                                                   monkeypatch):
    given = design_md(style_packs.default_look())

    def export(repo, branch, dest: Path) -> None:
        (dest / "DESIGN.md").mkdir(parents=True, exist_ok=True)

    (tmp_path / "DESIGN.md").mkdir()
    told = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug), export=export,
                          outputs=_reviews(_FIX, _PASS), given=given)

    fix = next(p for p in told if p.startswith("Fix the problems below"))
    assert "DESIGN.md is a link or a folder, not the file this build was given" in fix
    assert "It read no screen code" in fix
    assert (tmp_path / "DESIGN.md").is_dir(), "the host never writes through what the branch made of it"
    assert s.query(DevJob).filter(DevJob.error.is_not(None)).count() == 0


def test_a_branch_that_keeps_to_its_design_md_tells_the_reviewer_the_check_passed(s, project, tmp_path, monkeypatch):
    given = design_md(style_packs.default_look())

    def export(repo, branch, dest: Path) -> None:
        (dest / "src").mkdir(parents=True, exist_ok=True)
        (dest / "DESIGN.md").write_text(given, encoding="utf-8", newline="")
        (dest / "src" / "app.css").write_text(".x { color: var(--primary-text); }\n", encoding="utf-8")

    told = _told_engineer(s, project, tmp_path, monkeypatch, _handoff(project.slug), export=export, given=given)

    review = next(p for p in told if p.startswith("Review the current branch"))
    assert "## Look check" in review and "Passed: DESIGN.md is as given" in review
    assert "It read 1 files of screen code." in review
    other = projects.create(s, "주문 정리", name="Its own look", start=False)
    old = _told_engineer(s, other, tmp_path / "old", monkeypatch, _handoff(other.slug))
    assert "## Look check" not in next(p for p in old if p.startswith("Review the current branch"))
