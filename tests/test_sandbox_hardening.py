"""The pre-release security review (roadmap step 0.8): what a sandboxed agent writes never takes a host read out of
its directory, the call's access token never reaches what the server stores, and a branch name or an image name
from a spec is never read as an option."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from justshipit.analysis import runner
from justshipit.contracts.handoff import HandoffTarget, SpecDraftOut, image_ref
from justshipit.engineering import worker
from justshipit.runtime import claude_cli, host_auth
from justshipit.runtime.base import LLMCall, LLMError
from justshipit.sandbox import docker, plainfile
from justshipit.settings import company_config

SECRET = "HOST-ONLY api_token 4f1c"


def _link(link: Path, target: Path | str) -> None:
    try:
        os.symlink(target, link, target_is_directory=Path(target).is_dir())
    except OSError:
        pytest.skip("this machine cannot make symbolic links")


def _secret(tmp_path: Path) -> Path:
    secret = tmp_path / "host" / "api_token"
    secret.parent.mkdir()
    secret.write_text(SECRET, encoding="utf-8")
    return secret


def _container_writes(monkeypatch, write) -> None:
    """Stand in for the analysis container: `write(out)` leaves in the writable mount what the script would."""
    def run(spec, **kw):
        [out] = [m.host for m in spec.mounts if m.container == "/work/out"]
        write(out)
        return docker.ContainerResult(0, b"", b"", False)
    monkeypatch.setattr(runner.docker, "run", run)


# ---------------------------------------------------------------- 1. the analysis out/ directory

def test_the_analysis_results_and_tables_are_still_read_from_plain_files(tmp_path, monkeypatch):
    def write(out: Path) -> None:
        (out / "results.json").write_text('{"n": 3}', encoding="utf-8")
        (out / "table.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    _container_writes(monkeypatch, write)

    code, _, _, _, results, files = runner.execute(tmp_path, "print(1)", tmp_path / "step01")
    rec = runner.AnalysisRecord(workdir=tmp_path, final_script="print(1)", final_results=results)

    assert code == 0 and json.loads(results) == {"n": 3} and files == ["results.json", "table.csv"]
    assert runner.artifact_files(rec, "a")["a/table.csv"] == "a,b\n1,2\n"


def test_a_linked_results_json_is_never_read(tmp_path, monkeypatch):
    secret = tmp_path / "host.json"
    secret.write_text(json.dumps({"token": SECRET}), encoding="utf-8")
    _container_writes(monkeypatch, lambda out: _link(out / "results.json", secret))

    _, _, _, _, results, files = runner.execute(tmp_path / "job", "print(1)", tmp_path / "job" / "step01")

    assert results is None and files == []


def test_a_linked_table_is_never_committed_to_the_registry(tmp_path, monkeypatch):
    secret = _secret(tmp_path)

    def write(out: Path) -> None:
        (out / "results.json").write_text("{}", encoding="utf-8")
        (out / "real.csv").write_text("a\n1\n", encoding="utf-8")
        _link(out / "stolen.csv", secret)
    _container_writes(monkeypatch, write)
    job = tmp_path / "job"
    _, _, _, _, results, _ = runner.execute(job, "print(1)", job / "step01")

    files = runner.artifact_files(runner.AnalysisRecord(workdir=job, final_script="print(1)", final_results=results),
                                  "a")

    assert "a/real.csv" in files and "a/stolen.csv" not in files
    assert SECRET not in "".join(files.values())


def test_a_linked_out_directory_is_never_read(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / "results.json").write_text('{"token": 1}', encoding="utf-8")
    (host / "notes.csv").write_text(SECRET, encoding="utf-8")
    job = tmp_path / "job"
    (job / "step01").mkdir(parents=True)
    (job / "step01" / "analysis.py").write_text("print(1)", encoding="utf-8")
    _link(job / "step01" / "out", host)

    files = runner.artifact_files(runner.AnalysisRecord(workdir=job, final_script="print(1)", final_results="{}"), "a")

    assert plainfile.read_plain(job, "step01/out/results.json", 1000, whole=True) is None
    assert plainfile.plain_files(job, "step01/out") == []
    assert SECRET not in "".join(files.values())


def test_a_plain_file_is_read_and_a_missing_or_oversized_one_is_not(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("0123456789", encoding="utf-8")

    assert plainfile.read_plain(tmp_path, "d/f.txt", 100, whole=True) == "0123456789"
    assert plainfile.read_plain(tmp_path, "d/f.txt", 4, whole=False) == "6789"
    assert plainfile.read_plain(tmp_path, "d/f.txt", 4, whole=True) is None
    assert plainfile.read_plain(tmp_path, "d/none.txt", 100, whole=True) is None
    assert plainfile.read_plain(tmp_path, "d", 100, whole=True) is None, "a directory is no file"
    assert plainfile.plain_files(tmp_path, "d") == ["f.txt"] and plainfile.plain_files(tmp_path, "none") == []


# ---------------------------------------------------------------- 2. an export for a reader holds no link

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=o", "-c", "user.email=o@x", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_every_export_of_a_branch_drops_the_links_it_committed(tmp_path):
    """The first read of an imported repo and the code reviewer read an export nobody called drop_links on: a
    committed `notes -> /proc/self/environ` would let the repo steer the reader into quoting its own token."""
    _link(tmp_path / "probe", tmp_path)  # the export below only holds a link where the host can make one
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.symlinks", "true")
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="/proc/self/environ", check=True,
                          capture_output=True, text=True).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},notes")
    _git(repo, "commit", "-qm", "a link out of the repo")
    dest = tmp_path / "export"
    dest.mkdir()

    worker._export_branch(repo, "HEAD", dest)

    assert (dest / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not os.path.lexists(dest / "notes"), "the committed link is gone before any reader starts"
    assert [p for p in dest.rglob("*") if p.is_symlink()] == []


# ---------------------------------------------------------------- 3. the call's access token

TOKEN = "sk-ant-oat01-EXAMPLE_value-0123456789"


def _container_call(monkeypatch, tmp_path, stdout: bytes, stderr: bytes = b""):
    cfg = company_config()
    monkeypatch.setattr(claude_cli, "company_config",
                        lambda: {**cfg, "engineering": {**cfg["engineering"], "isolation": "container"}})
    monkeypatch.setattr(host_auth, "access_token", lambda min_valid_s: TOKEN)
    monkeypatch.setattr(claude_cli.docker, "run", lambda spec, **kw: docker.ContainerResult(0, stdout, stderr, False))
    engineer = claude_cli.ClaudeRunner.__new__(claude_cli.ClaudeRunner)
    engineer.bin = "claude"
    return engineer.run(LLMCall(agent="engineer", system_prompt="s", prompt="p", output_schema={"type": "object"},
                                cwd=tmp_path, mode="engineering"))


def _kept(call_dir: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(Path(call_dir).iterdir()) if p.is_file())


def test_the_access_token_is_redacted_from_what_an_engineer_call_returns_and_keeps(monkeypatch, tmp_path):
    said = json.dumps({"structured_output": {"summary": f"env says CLAUDE_CODE_OAUTH_TOKEN={TOKEN}"}, "usage": {}})

    result = _container_call(monkeypatch, tmp_path, said.encode(), f"debug: {TOKEN}\n".encode())

    assert result.output == {"summary": f"env says CLAUDE_CODE_OAUTH_TOKEN={claude_cli.REDACTED}"}
    kept = _kept(result.raw_path)
    assert TOKEN not in kept and claude_cli.REDACTED in kept
    assert claude_cli.REDACTED in (Path(result.raw_path) / "stderr.txt").read_text(encoding="utf-8")


def test_the_access_token_is_redacted_from_a_failed_call_too(monkeypatch, tmp_path):
    with pytest.raises(LLMError) as failed:
        _container_call(monkeypatch, tmp_path, f"not json {TOKEN}".encode(), f"boom {TOKEN}".encode())

    assert TOKEN not in str(failed.value) and TOKEN not in (failed.value.raw or "")
    assert claude_cli.REDACTED in str(failed.value)


# ---------------------------------------------------------------- 5. the base branch

@pytest.mark.parametrize("name", ["main", "master", "feat/safety-and-install", "release/2.0", "v1.2_x", "개발"])
def test_a_safe_base_branch_is_kept(name):
    assert HandoffTarget(repository="r", base_branch=name).base_branch == name


@pytest.mark.parametrize("name", [
    "--output=/tmp/x", "-b", "--upload-pack=touch x", "", "a..b", "a b", "a\tb", "a\x07b", "a\x7fb", "a~1", "a^",
    "a:b", "a?b", "a*", "a[b", "a\\b", "/a", "a/", "a//b", "a.lock", "a.lock/b", ".a", "a/.b", "a.", "@", "a@{1}",
])
def test_a_base_branch_git_could_read_as_an_option_or_a_revision_is_refused(name):
    with pytest.raises(ValidationError):
        HandoffTarget(repository="r", base_branch=name)


# ---------------------------------------------------------------- 6. the test image

GOOD_IMAGES = ["python:3.13-slim", "node:22", "ship/analysis:1", "ghcr.io/astral-sh/uv:python3.13-bookworm-slim",
               "localhost:5000/team/img:1", "mcr.microsoft.com/playwright:v1.49.0-jammy", "ubuntu",
               "python@sha256:" + "a" * 64, "registry.example.com/a/b__c/d-e.f:tag_1"]
BAD_IMAGES = ["--privileged", "-v", "--network=host", "python:3.13 --privileged", " python", "python\n", "img;id",
              "$(id)", "`id`", "a|b", "a&b", "python:", "python:-x", ":tag", "Python:3.13", "a//b", "a/", "/a",
              "python@sha256:abc", "x" * 300, ""]


@pytest.mark.parametrize("image", GOOD_IMAGES)
def test_a_docker_image_reference_is_kept(image):
    assert image_ref(image) == image
    assert HandoffTarget(repository="r", test_image=image).test_image == image


@pytest.mark.parametrize("image", BAD_IMAGES)
def test_a_test_image_that_is_no_image_reference_names_no_image(image):
    """The build then stops at "The spec names no test image" (worker.run_tests): the host never pulls it."""
    assert image_ref(image) is None
    assert HandoffTarget(repository="r", test_image=image).test_image is None
    draft = SpecDraftOut(title="t", prd_markdown="p", requirements=[], acceptance_criteria=[],
                         design_notes_markdown="d", suggested_target_repository="r", suggested_test_command=None,
                         suggested_test_image=image)
    assert draft.suggested_test_image is None


def test_run_tests_never_pulls_an_image_the_contract_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **k: pytest.fail(f"host docker call: {a}"))
    monkeypatch.setattr(worker.docker, "docker_ok", lambda: pytest.fail("no image, so Docker is never asked"))
    target = HandoffTarget(repository="r", test_command="pytest", test_image="--privileged")

    with pytest.raises(docker.SandboxUnavailable) as refused:
        worker.run_tests(tmp_path, target.test_command, target.test_image)

    assert refused.value.said.key =="build.error.no_test_image"
