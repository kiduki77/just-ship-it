"""A product the owner already has, brought into products/<repo> so the company works on its own copy.

The server clones the owner's repository with `git clone --no-hardlinks` and never writes to the owner's folder: the
clone has no `origin`, so no push or merge the company makes can reach it, and merging into the owner's real repo
stays the owner's own step. A clone carries only what git tracks, so untracked and ignored files (`.env`, `var/`)
never arrive. Before the clone is kept, its tracked files are scanned for obvious secrets, and a hit refuses the
import naming the file, never the secret.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from justshipit.contracts.handoff import branch_name
from justshipit.contracts.product import ExistingProduct
from justshipit.contracts.secrets_scan import SECRET_PATTERNS
from justshipit.db.models import Business, Project
from justshipit.i18n import LANGS, Lang, msg
from justshipit.i18n.said import Refusal, said
from justshipit.sandbox import docker, hostgit
from justshipit.settings import company_config

log = logging.getLogger(__name__)
LEGACY_SOURCE_KEY = "aiteam.source"  # what clones imported before the rename carry; read for one release
SOURCE_KEY = "ship.source"  # the clone's git config: which folder it was cloned from, so an import is repeatable

_GITHUB = re.compile(r"(?:https?://|git@)?(?:www\.)?github\.com[/:][^\s]+", re.IGNORECASE)

_ENV_FILE = re.compile(r"\.env(\..+)?")
_ENV_TEMPLATE = {"example", "sample", "template", "dist", "defaults"}  # the last part of .env.<...>.<part>
# A fake key in a test is a fixture, not a secret: tests need a TLS key or a token-shaped string to run.
_TEST_DIRS = {"test", "tests", "__tests__", "testdata", "test-data", "fixtures", "__fixtures__", "spec", "specs", "e2e"}
_TEST_FILE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|_spec\.)")


@dataclass(frozen=True)
class Source:
    """A local git repository the owner pointed at, parsed at the boundary."""
    given: str      # as the owner typed it
    path: Path      # the repository's top folder
    repo: str       # the folder name under products/


@dataclass(frozen=True)
class SecretHit:
    path: str
    line: int | None
    kind: str       # env_file or a SECRET_PATTERNS key


def repo_name(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:60] or "product"


def _git(cwd: Path, *args: str) -> str | None:
    p = hostgit.run(cwd, *args, attr_source=False)
    return p.stdout.strip() if p.returncode == 0 else None


def parse_source(given: str, products_dir: Path) -> Source:
    """The owner's repository, or a Refusal saying what to give instead. A GitHub link needs the GitHub connection
    live, which it cannot be yet (GitHub supports only off and outbox), so a link is refused with what to do."""
    given = given.strip().strip('"').strip("'")
    if not given:
        raise Refusal(said("product.import.need_source"))
    if _GITHUB.fullmatch(given):
        raise Refusal(said("product.import.github_not_live"))
    path = Path(given).expanduser()
    if not path.is_absolute():
        raise Refusal(said("product.import.not_absolute"))
    if not path.is_dir():
        raise Refusal(said("product.import.not_found", path=given))
    if path.resolve().is_relative_to(products_dir.resolve()):
        raise Refusal(said("product.import.inside_products"))
    top = _git(path, "rev-parse", "--show-toplevel")
    if top is None:
        raise Refusal(said("product.import.not_git", path=given))
    top_path = Path(top).resolve()
    if top_path != path.resolve():
        raise Refusal(said("product.import.not_top", top=str(top_path)))
    if _git(top_path, "rev-parse", "--verify", "-q", "HEAD^{commit}") is None:
        raise Refusal(said("product.import.no_commits"))
    if _git(top_path, "symbolic-ref", "-q", "HEAD") is None:
        raise Refusal(said("product.import.detached"))
    return Source(given=given, path=top_path, repo=repo_name(top_path.name or "product"))


def _in_tests(path: str) -> bool:
    p = PurePosixPath(path)
    return any(part.lower() in _TEST_DIRS for part in p.parts[:-1]) or bool(_TEST_FILE.search(p.name.lower()))


def _env_file(path: str) -> bool:
    m = _ENV_FILE.fullmatch(PurePosixPath(path).name)
    return bool(m) and (m.group(1) or ".").rsplit(".", 1)[-1].lower() not in _ENV_TEMPLATE


def _kind(text: str) -> str:
    return next((k for k, p in SECRET_PATTERNS.items() if re.search(p, text)), "token")


def _must(p) -> str:
    """git's output, or a refusal: a scan that could not run passes nothing."""
    if p.returncode != 0:
        raise Refusal(said("product.import.scan_failed"))
    return p.stdout


def scan_secrets(repo: Path, rev: str = "HEAD") -> list[SecretHit]:
    """Files at `rev` that hold an obvious secret: a committed .env, a private key or a token. Test fixtures are
    left out. The matched text is classified here and never returned."""
    names = _must(hostgit.run(repo, "ls-tree", "-r", "-z", "--name-only", rev, attr_source=False)).split("\0")
    hits = [SecretHit(path, None, "env_file") for path in names if path and _env_file(path)]
    args = [a for pattern in SECRET_PATTERNS.values() for a in ("-e", pattern)]
    out = hostgit.run(repo, "grep", "-I", "-n", "-z", "-E", *args, rev, "--", attr_source=False)
    if out.returncode not in (0, 1):  # 1 is "no match"
        raise Refusal(said("product.import.scan_failed"))
    for row in out.stdout.splitlines():
        ref_path, line, text = (row.split("\0", 2) + ["", ""])[:3]
        path = ref_path.split(":", 1)[-1]
        if not _in_tests(path):
            hits.append(SecretHit(path, int(line) if line.isdigit() else None, _kind(text)))
    return hits


def scan_history(repo: Path) -> list[tuple[SecretHit, str]]:
    """What the clone carries in its history though no longer at HEAD: every .env ever added, and every added line
    of every commit on any branch, with the commit it came in. A clone copies all of it."""
    found: list[tuple[SecretHit, str]] = []
    commit = ""
    for row in _must(hostgit.run(repo, "log", "--all", "--diff-filter=A", "--name-only", "--format=%x00%h",
                                 attr_source=False)).splitlines():
        if row.startswith("\0"):
            commit = row[1:]
        elif row and _env_file(row):
            found.append((SecretHit(row, None, "env_file"), commit))
    pattern = re.compile("|".join(f"(?:{p})" for p in SECRET_PATTERNS.values()))
    path, line = "", 0
    for row in _must(hostgit.run(repo, "log", "--all", "-p", "-U0", "--no-color", "--no-ext-diff", "--no-textconv",
                                 "--format=%x00%h", attr_source=False)).splitlines():
        if row.startswith("\0"):
            commit = row[1:]
        elif row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else ""
        elif row.startswith("@@"):
            m = re.search(r"\+(\d+)", row)
            line = int(m.group(1)) if m else 0
        elif row.startswith("+") and path:
            if not _in_tests(path) and pattern.search(row):
                found.append((SecretHit(path, line, _kind(row)), commit))
            line += 1
    return found


def _refuse_secrets(repo: Path, rev: str = "HEAD", history: bool = True) -> None:
    hits = scan_secrets(repo, rev)
    if hits:
        first = hits[0]
        where = said("product.import.where_line", path=first.path, line=first.line) if first.line else first.path
    elif history and (old := scan_history(repo)):
        hits = [hit for hit, _ in old]
        first, commit = old[0]
        where = said("product.import.where_commit", path=first.path, commit=commit)
    else:
        return
    # Worded per language here: a Said keeps its values as text, and a plural needs the count as a number.
    more = {lang: msg("product.import.more", lang, n=len(hits) - 1) for lang in LANGS} if len(hits) > 1 else ""
    raise Refusal(said("product.import.secret", where=where, kind=said(f"product.secret.{first.kind}"), more=more))


def _remove(path: Path) -> None:
    """rmtree that also removes git's read-only object files on Windows. A folder it cannot remove (a file lock) is
    left behind rather than hiding the refusal the owner should read; its leading dot keeps it off every list."""
    def writable(fn, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        fn(p)
    try:
        shutil.rmtree(path, onexc=writable)
    except OSError:
        log.warning("could not remove %s", path)


def _refresh(dest: Path, source: Path) -> None:
    """Bring a clone made earlier up to the owner's branch, when that is a fast-forward and nothing in it changed.
    Otherwise it stays as it is: the company's own branches live there."""
    branch = _git(dest, "symbolic-ref", "--short", "HEAD")
    if not branch or _git(dest, "status", "--porcelain") != "":
        return
    if _git(dest, "fetch", "--no-tags", str(source), branch) is not None:
        _refuse_secrets(dest, "FETCH_HEAD", history=False)
        _git(dest, "merge", "--ff-only", "FETCH_HEAD")


def _clone_source(dest: Path) -> str:
    return _git(dest, "config", "--get", SOURCE_KEY) or _git(dest, "config", "--get", LEGACY_SOURCE_KEY)


def _base_branch(repo: Path) -> str:
    """The branch checked out in `repo`, which the company builds on. A name the handoff contract refuses (one git
    could read as an option) stops the import, before the paid calls that would draft a spec for it."""
    base = _git(repo, "symbolic-ref", "--short", "HEAD") or "main"
    if branch_name(base) is None:
        raise Refusal(said("product.import.branch_name"))
    return base


def clone(src: Source, products_dir: Path) -> ExistingProduct:
    """products/<repo> as a clone of the owner's repository. Repeating an import of the same folder reuses the clone
    made before; a folder of that name from anywhere else is refused, never overwritten."""
    products_dir.mkdir(parents=True, exist_ok=True)
    dest = products_dir / src.repo
    if dest.exists():
        if not (dest / ".git").exists() or _clone_source(dest) != str(src.path):
            raise Refusal(said("product.import.taken", repo=src.repo))
        _refresh(dest, src.path)
        _refuse_secrets(dest, history=False)
    else:
        staging = products_dir / f".import-{uuid.uuid4().hex[:8]}"
        try:
            p = hostgit.run(products_dir, "clone", "--no-hardlinks", "-c", "core.autocrlf=false", str(src.path),
                            staging.name, attr_source=False)
            if p.returncode != 0:
                raise Refusal(said("product.import.clone_failed"))
            _refuse_secrets(staging)
            _git(staging, "remote", "remove", "origin")
            if _git(staging, "remote") != "":  # with a remote left, a delivery would push into the owner's folder
                raise Refusal(said("product.import.clone_failed"))
            _git(staging, "config", SOURCE_KEY, str(src.path))
            _base_branch(staging)  # refused here, the copy never takes the product's name
            try:
                staging.rename(dest)
            except OSError:
                raise Refusal(said("product.import.taken", repo=src.repo)) from None
        finally:
            if staging.exists():
                _remove(staging)
    return ExistingProduct(repo=src.repo, source=src.given, base_branch=_base_branch(dest),
                           commit=_git(dest, "rev-parse", "--short", "HEAD") or "")


def render(product: ExistingProduct) -> str:
    """The product and the CPO's report as agents read it: the repo and commit, then the report's fields."""
    head = (f"Repository: products/{product.repo} (branch {product.base_branch}, started from commit "
            f"{product.commit}). Build on this code: specify and make changes to it, never a first version.")
    r = product.report
    if r is None:
        return head + ("\nThe CPO could not write the report, and no agent can open the code until it is written. Don't "
                       "plan tasks that read the repository, fetch it from GitHub or ask the owner to make it public. "
                       "The owner can have the code read again from the kickoff screen. Meanwhile ask the owner "
                       "briefly what it does."
                       if product.report_failed else "\nThe CPO's report is not written yet.")
    out = [head, f"Summary: {r.summary}", f"For the owner: {r.for_owner}", f"What it does: {r.what_it_does}",
           "Features:", *(f"- [{f.state}] {f.name} ({f.evidence})" for f in r.features),
           f"Stack: {', '.join(r.stack)}", f"Tests [{r.tests.state}]: {r.tests.summary} ({r.tests.evidence})",
           "Risks:", *(f"- {x.text} ({x.evidence})" for x in r.risks),
           "Unfinished:", *(f"- {x.text} ({x.evidence})" for x in r.unfinished),
           "README against the code:",
           *(f"- [{c.verdict}] {c.claim}: {c.reality} ({c.evidence})" for c in r.readme_vs_reality)]
    return "\n".join(out)


def waiting(product: ExistingProduct, lang: Lang) -> str | None:
    """Why a reading in progress cannot move right now, in the owner's words: the sandbox it reads in is not there.
    Asked of the PC as it is now; a job's last error may hold a failure of the code's own."""
    if product.report_state != "reading":
        return None
    down = docker.unavailable(company_config()["engineering"]["sandbox"]["image"])
    return down.words(lang) if down is not None else None


def view(product: ExistingProduct | None, lang: Lang) -> dict | None:
    """What the console shows of it: where the company's copy is, where it came from, why a reading is held up, and
    the report once written."""
    if product is None:
        return None
    return {"repo": product.repo, "folder": f"products/{product.repo}", "source": product.source,
            "commit": product.commit, "base_branch": product.base_branch, "report_state": product.report_state,
            "waiting": waiting(product, lang),
            "report": product.report.model_dump(mode="json") if product.report else None}


def existing(business: Business | None) -> ExistingProduct | None:
    """The product the business brought, or None for a business that started from an idea."""
    return ExistingProduct.model_validate(business.product) if business is not None and business.product else None


def of_project(s: Session, project_id: int) -> ExistingProduct | None:
    p = s.get(Project, project_id)
    return existing(s.get(Business, p.business_id)) if p is not None and p.business_id is not None else None


def pinned_by(s: Session, repo: str) -> int | None:
    """The live business that already works on products/<repo>."""
    return s.scalar(select(Business.id).where(Business.status == "ACTIVE",
                                              Business.product["repo"].astext == repo).limit(1))
