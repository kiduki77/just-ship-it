"""The worker runs the binding of a spec's journeys in the sandbox and keeps what each step printed, cleaned, for the
merge card: no secret, no escape code and no container path reaches the record, only frames sealed for this run
count, and a step that did not run says why. The product is `habit`, a made-up CLI for Mina's habit-app."""
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from justshipit.contracts import secrets_scan
from justshipit.contracts.handoff import JourneyOut, JourneyStepOut
from justshipit.db.models import DevJob, Spec
from justshipit.engineering import journeys, try_it, walkthrough
from justshipit.sandbox import docker
from justshipit.settings import company_config

NONCE = "5" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
HEAD = "c0ffee1234567890abcdef1234567890abcdef12"


def _b(data: bytes | str) -> str:
    return base64.b64encode(data.encode() if isinstance(data, str) else data).decode()


def run_frame(sid: str, out: bytes | str = b"", *, exit: int = 0, nonce: str = NONCE, start: int = 1_000_000,
              ms: int = 40, tail: bytes = b"", lines: int | None = None, show: str = "", show_head: bytes = b"",
              show_size: int = 0, oom: int = 0) -> bytes:
    """One line as the runner prints it after a run step."""
    out = out.encode() if isinstance(out, str) else out
    body = {"kind": "run", "id": _b(sid), "exit": exit, "oom": oom, "ms": ms, "start": start, "end": start + ms,
            "size": len(out) + len(tail), "lines": (out + tail).count(b"\n") if lines is None else lines,
            "out": _b(out), "tail": _b(tail), "show": show, "show_size": show_size, "show_head": _b(show_head)}
    return f"@@{walkthrough.seal(nonce, sid)}@@{json.dumps(body)}\n".encode()


def open_frame(sid: str, *, up: int = 1, shot: int = 1, nonce: str = NONCE) -> bytes:
    body = {"kind": "open", "id": _b(sid), "up": up, "shot": shot, "ms": 900, "start": 1_000_000, "end": 1_000_900}
    return f"@@{walkthrough.seal(nonce, sid)}@@{json.dumps(body)}\n".encode()


def _clean(text: str):
    return walkthrough.clean(text.encode(), ms=5, exit_code=0)


# ---- clean

GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
SLACK = "xoxb-" + "1234567890-abcdefghijKLMNOP"
SECRETS = [  # every shape secrets_scan knows, as the patterns' own comments and the chat scrub tests give them
    ("private_key", ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAABG5vbmUAAAAEbm9uZQ==\n"
                    "-----END OPENSSH PRIVATE KEY-----"), "b3BlbnNzaC1rZXktdjEAAAAA"),
    ("github_token", f"pushed with {GITHUB}", GITHUB),
    ("github_token", "github_pat_" + "a1B2c3D4e5" * 3, "github_pat_" + "a1B2c3D4e5" * 3),
    ("github_token", "my token was ghp_A1b2C3d4E5f6G7h8I9j0\nK1l2M3n4O5p6Q7r8 sorry", "K1l2M3n4O5p6Q7r8"),
    ("slack_token", "posting as " + SLACK, SLACK),
    ("api_key", "using sk-ant-api03-" + "x" * 40, "sk-ant-api03-" + "x" * 40),
    ("api_key", "//registry.npmjs.org/:_authToken=npm_" + "a1B2c3D4" * 4 + "e5F6", "npm_" + "a1B2c3D4" * 4 + "e5F6"),
    ("aws_key", "aws key AKIAIOSFODNN7EXAMPLE in use", "AKIAIOSFODNN7EXAMPLE"),
    ("google_key", "maps key AIza" + "B" * 35, "AIza" + "B" * 35),
    ("bearer", "npm notice fetching with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
     "eyJhbGciOiJIUzI1NiJ9.payload.signature"),
    ("bearer", "Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw=="),
    ("env_secret", "DATABASE_PASSWORD=hunter2-very-secret", "hunter2-very-secret"),
    ("env_secret", "DATABASE_URL=postgres://admin:s3cret-pw@db:5432/ship", "s3cret-pw"),
    ("env_secret", '{"user": "ship", "password": "hunter2-json"}', "hunter2-json"),
    ("env_secret", "deploy --token Zx9-4kq_LmN2pQ7 --env prod", "Zx9-4kq_LmN2pQ7"),
    ("env_secret", "Cookie: session=0a1b2c3d4e5f; theme=dark", "session=0a1b2c3d4e5f"),
]


def test_every_kind_of_secret_has_an_example_here():
    assert {kind for kind, _, _ in SECRETS} == set(secrets_scan.KINDS)


@pytest.mark.parametrize("kind, said, secret", SECRETS)
def test_a_secret_the_product_prints_never_reaches_the_capture(kind, said, secret):
    cap = _clean(f"habit: starting\n{said}\nhabit: done\n")

    assert secret not in cap["text"] and f"[secret:{kind}]" in cap["text"]
    assert cap["text"].startswith("habit: starting\n") and cap["text"].endswith("\nhabit: done")
    assert {"kind": kind, "n": 1} in cap["scrubbed"] and secret not in json.dumps(cap)


def test_the_journey_folder_and_work_read_as_here_and_the_sandbox_home_as_the_owners():
    cap = _clean("Wrote /tmp/walk/j2/habit-app/out/spec.md\ncache in /tmp/.npm/_cacache\nread /work/README.md in /work\n"
                 "set up in /tmp/walk/base\n/workspace/habit and /tmpfiles stay\n")

    assert cap["text"] == ("Wrote ./habit-app/out/spec.md\ncache in ~/.npm/_cacache\nread ./README.md in .\nset up in .\n"
                           "/workspace/habit and /tmpfiles stay")


def test_escape_codes_and_control_characters_are_gone_and_a_progress_line_shows_its_last_state():
    cap = _clean("\x1b[32m✔ created\x1b[0m habit-app\n\x1b]0;habit\x07title set\n 10%\r 50%\r100%\r\n"
                 "a\tb\x07\x00\x7f end\n")

    assert cap["text"] == "✔ created habit-app\ntitle set\n100%\na\tb end"


def test_bytes_that_are_not_utf8_are_replaced_not_dropped():
    assert walkthrough.clean(b"caf\xe9 ok\n", ms=1, exit_code=0)["text"] == "caf� ok"


def test_a_long_output_keeps_its_head_and_tail_and_says_where_it_was_cut():
    cap = _clean("".join(f"line {n}\n" for n in range(1, 201)))

    kept = cap["text"].split("\n")
    assert kept == [f"line {n}" for n in (*range(1, 61), *range(181, 201))]
    assert (cap["lines"], cap["cut_after"]) == (200, 60)
    assert _clean("one\ntwo\n")["cut_after"] is None


def test_a_capture_of_long_lines_stays_within_its_characters_with_its_last_line():
    cap = _clean("".join(f"{n:03d} " + "x" * 296 + "\n" for n in range(1, 51)))

    kept = cap["text"].split("\n")
    assert len(cap["text"]) <= 6000 and cap["lines"] == 50 and 0 < cap["cut_after"] < len(kept)
    assert kept[0].startswith("001 ") and kept[cap["cut_after"] - 1].startswith(f"{cap['cut_after']:03d} ")
    assert kept[-1].startswith("050 "), "the last line is kept"


def test_a_secret_across_the_cut_is_removed_whole_before_the_cut():
    key = "-----BEGIN RSA PRIVATE KEY-----\n" + "".join(f"MIIEkey{n}\n" for n in range(20)) + "-----END RSA PRIVATE KEY-----\n"
    cap = _clean("".join(f"line {n}\n" for n in range(1, 59)) + key + "".join(f"tail {n}\n" for n in range(1, 101)))

    assert "MIIEkey" not in cap["text"] and "[secret:private_key]" in cap["text"]
    assert cap["text"].endswith("tail 100"), "the lines after the key are still there"


def test_what_differs_on_every_run_is_marked_on_the_kept_text():
    cap = _clean("Built at 2026-11-13T05:22:40Z in 120ms (3 s total)\n"
                 "run 550e8400-e29b-41d4-a716-446655440000 commit 1a2b3c4d5e at 05:22:40\n"
                 "files: 12, deadbeef and 1.5s\n")

    assert [cap["text"][a:b] for a, b in cap["volatile"]] == [
        "2026-11-13T05:22:40Z", "120ms", "3 s", "550e8400-e29b-41d4-a716-446655440000", "1a2b3c4d5e", "05:22:40",
        "1.5s"]


def test_a_show_file_head_is_cleaned_like_output_and_a_binary_one_is_only_its_size():
    head, scrubbed = walkthrough.file_head("out/spec.md", "".join(f"# screen {n}\n" for n in range(100)).encode()
                                           + f"GITHUB_TOKEN={GITHUB}\n".encode(), 4000)
    assert head["text"].split("\n") == [f"# screen {n}" for n in range(40)] and head["lines"] == 101
    assert (head["binary"], head["size"], scrubbed) == (False, 4000, [{"kind": "github_token", "n": 1}])

    png, _ = walkthrough.file_head("out/logo.png", PNG, 72)
    assert (png["binary"], png["text"], png["size"]) == (True, "", 72)


# ---- frames

def test_a_frame_sealed_for_another_run_is_ignored():
    stdout = run_frame("JN1.1", "forged", nonce="6" * 32) + run_frame("JN1.2", "real")

    assert [(f["id"], f["out"]) for f in walkthrough.frames(stdout, NONCE)] == [("JN1.2", b"real")]


def test_the_first_sealed_frame_of_a_step_is_its_own():
    stdout = run_frame("JN1.1", "forged", nonce="6" * 32) + run_frame("JN1.1", "first") + run_frame("JN1.1", "again")

    assert [f["out"] for f in walkthrough.frames(stdout, NONCE)] == [b"first"]


def test_what_is_not_a_whole_frame_is_ignored():
    good = run_frame("JN1.1", "ok").decode()
    seal = walkthrough.seal(NONCE, "JN1.2")
    stdout = "\n".join([
        "Created habit-app", good.replace('"out": "b2s="', '"out": "b2s"'),  # bad base64
        f"@@{seal}@@{{not json}}", f'@@{seal}@@{{"kind": "run"}}',  # bad JSON, a frame with no fields
        good.replace(_b("JN1.1"), _b("../x")),  # an id no step has
        good.replace('"exit": 0', '"exit": true'), "prefix " + good.strip(),  # an exit that is no number, a late mark
    ]).encode()

    assert walkthrough.frames(stdout, NONCE) == []


# ---- walk

def _doc(*steps: int) -> dict:
    return journeys.number(None, [
        JourneyOut(title=f"Journey {n}", persona="Mina, planning a habit app alone", why="she wants specs",
                   brings="6 features in a text file", success="one spec per screen", priority="P1" if n == 1 else "P2",
                   steps=[JourneyStepOut(do=f"step {i}", why="for her app", see="a spec") for i in range(1, k + 1)])
        for n, k in enumerate(steps, 1)])


DOC = _doc(3, 2, 2)
BINDING = """\
setup:
  - sh scripts/ci-setup.sh
steps:
  JN1.1: { run: "habit init habit-app" }
  JN1.2: { run: "habit gen habit-app/features.md --out out", show: "out/spec.md",
           seed: { "habit-app/features.md": "fixtures/journeys/features.md" } }
  JN1.3: { run: "habit list" }
  JN2.1: { open: "/today" }
  JN2.2: { run: "habit check" }
  JN3.1: { run: "habit check broken.md", expect: error }
"""


def _repo(root: Path, binding: str | None = BINDING, files: dict[str, str] | None = None) -> Path:
    repo = root / "habit"
    every = {"README.md": "# habit\n", "fixtures/journeys/features.md": f"- streaks\nNPM_TOKEN={GITHUB}\n",
             **({walkthrough.BINDING: binding} if binding is not None else {}), **(files or {})}
    for rel, text in every.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "habit"], check=True)
    return repo


class Box:
    """docker.run as the walk calls it: what it was asked, and what the container printed and left in /shots."""

    def __init__(self, stdout: bytes = b"", *, timed_out: bool = False, shots: dict[str, bytes] | None = None):
        self.stdout, self.timed_out, self.shots = stdout, timed_out, shots or {}
        self.spec: docker.ContainerSpec | None = None
        self.stdin = b""

    def run(self, spec, *, stdin=b"", timeout_s=600):
        self.spec, self.stdin = spec, stdin
        out = next(m.host for m in spec.mounts if m.container == "/shots")
        for name, data in self.shots.items():
            (out / name).write_bytes(data)
        return docker.ContainerResult(None if self.timed_out else 0, self.stdout, b"", self.timed_out)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch):
    monkeypatch.setattr(walkthrough, "_nonce", lambda: NONCE)
    monkeypatch.setattr(docker, "docker_ok", lambda: True)
    monkeypatch.setattr(docker, "refused_hosts", lambda since, until: set())


def _walk(tmp_path, monkeypatch, box: Box | None, *, binding: str | None = BINDING, job_id: int = 7, **files):
    if box is not None:
        monkeypatch.setattr(docker, "run", box.run)
    return walkthrough.walk(_repo(tmp_path, binding, files), "HEAD", DOC, job_id=job_id, head=HEAD, language="ko")


def _why(walked) -> dict[str, str | None]:
    return {sid: r["not_run"] for sid, r in [*((r["id"], r) for r in walked["setup"]), *walked["steps"].items()]}


def test_each_step_has_its_capture_or_says_why_it_did_not_run(tmp_path, monkeypatch):
    box = Box(run_frame("setup.1", "installed\n", ms=900) + run_frame("JN1.1", "Created /tmp/walk/j1/habit-app\n")
              + run_frame("JN1.2", "habit: no such file habit-app/features.md\n", exit=1, ms=60)
              + open_frame("JN2.1", up=0))

    walked = _walk(tmp_path, monkeypatch, box)

    assert _why(walked) == {"setup.1": None, "JN1.1": None, "JN1.2": None, "JN1.3": "earlier_failed",
                            "JN2.1": "unreachable", "JN2.2": "earlier_failed", "JN3.1": "out_of_time",
                            "JN3.2": "unbound"}
    first = walked["steps"]["JN1.1"]
    assert (first["kind"], first["action"], first["expect"]) == ("run", "habit init habit-app", "ok")
    assert first["capture"]["text"] == "Created ./habit-app" and first["capture"]["exit"] == 0
    assert walked["steps"]["JN1.2"]["capture"]["exit"] == 1
    assert walked["steps"]["JN2.1"]["action"] == "/today" and walked["steps"]["JN2.1"]["capture"] is None
    assert (walked["setup_ms"], walked["ms"]) == (900, 100)
    assert walked["lint"] == [("These journey steps have no entry under `steps:` in `scripts/walkthrough.yaml`: JN3.2. "
                              "Bind each to the `run` line or the `open` path a person uses for it.")]


def test_a_failed_setup_line_stops_every_step(tmp_path, monkeypatch):
    two = BINDING.replace("  - sh scripts/ci-setup.sh\n", "  - sh scripts/ci-setup.sh\n  - npm run build\n")
    box = Box(run_frame("setup.1", "npm ERR! 404 Not Found - GET https://registry.npmjs.org/habbit\n", exit=1))

    walked = _walk(tmp_path, monkeypatch, box, binding=two)

    assert walked["setup"][0]["capture"]["exit"] == 1
    assert {sid: why for sid, why in _why(walked).items() if sid != "setup.1"} == {
        "setup.2": "setup_failed", "JN1.1": "setup_failed", "JN1.2": "setup_failed", "JN1.3": "setup_failed",
        "JN2.1": "setup_failed", "JN2.2": "setup_failed", "JN3.1": "setup_failed", "JN3.2": "unbound"}


def test_a_container_stopped_at_its_time_cap_leaves_the_rest_out_of_time(tmp_path, monkeypatch):
    walked = _walk(tmp_path, monkeypatch, Box(run_frame("setup.1") + run_frame("JN1.1"), timed_out=True))

    assert [_why(walked)[sid] for sid in ("JN1.1", "JN1.2", "JN2.1", "JN3.1")] == [None, "out_of_time", "out_of_time",
                                                                                   "out_of_time"]


def test_a_step_expected_to_fail_that_fails_lets_its_journey_go_on(tmp_path, monkeypatch):
    stdout = b"".join(run_frame(sid) for sid in ("setup.1", "JN1.1", "JN1.2", "JN1.3", "JN2.2")) + open_frame("JN2.1") \
        + run_frame("JN3.1", "broken.md line 2: unknown state 'lol'\n", exit=3)

    walked = _walk(tmp_path, monkeypatch, Box(stdout, shots={"JN2.1.png": PNG}))

    assert {sid for sid, why in _why(walked).items() if why} == {"JN3.2"}


@pytest.mark.parametrize("box", ["down", "refused", "silent"])
def test_a_sandbox_that_cannot_run_leaves_every_bound_step_no_docker(tmp_path, monkeypatch, box):
    if box == "down":
        monkeypatch.setattr(docker, "docker_ok", lambda: False)
        monkeypatch.setattr(docker, "run", lambda *a, **k: pytest.fail("no container without Docker"))
    elif box == "refused":
        def refuse(*a, **k):
            raise docker.SandboxUnavailable("build.sandbox.egress_down", proxy=docker.EGRESS_PROXY)
        monkeypatch.setattr(docker, "run", refuse)
    walked = _walk(tmp_path, monkeypatch, Box(b"docker: Error response from daemon\n") if box == "silent" else None)

    assert set(_why(walked).values()) == {"no_docker", "unbound"} and _why(walked)["JN3.2"] == "unbound"


def test_a_branch_with_no_binding_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(docker, "run", lambda *a, **k: pytest.fail("nothing is bound, so no container"))

    walked = _walk(tmp_path, monkeypatch, None, binding=None)

    assert set(_why(walked).values()) == {"unbound"} and len(walked["steps"]) == 7 and walked["setup"] == []
    assert walked["lint"][0].startswith("There is no `scripts/walkthrough.yaml`")


def test_a_spec_with_no_journeys_walks_nothing(tmp_path):
    assert walkthrough.walk(tmp_path, "HEAD", {"about": None, "journeys": []}, job_id=1, head=HEAD, language=None) == {
        "setup": [], "steps": {}, "setup_ms": 0, "ms": 0, "lint": []}


def test_a_step_that_only_prints_never_runs_and_branch_text_never_reaches_the_script_raw(tmp_path, monkeypatch):
    sly = BINDING.replace('JN1.3: { run: "habit list" }', "JN1.3: { run: 'echo \"3 habits\"' }").replace(
        'run: "habit check" }', 'run: "habit check \\"$(id)\\"; rm -rf ~/habit-app #" }')
    box = Box(run_frame("setup.1"))

    walked = _walk(tmp_path, monkeypatch, box, binding=sly)
    script = box.stdin.decode()

    assert walked["steps"]["JN1.3"]["not_run"] == "unbound" and walked["steps"]["JN1.3"]["action"] == ""
    assert _b('echo "3 habits"') not in script and "3 habits" not in script
    assert walked["steps"]["JN2.2"]["action"] == 'habit check "$(id)"; rm -rf ~/habit-app #'
    assert "rm -rf" not in script and "habit check" not in script and "$(id)" not in script
    assert _b('habit check "$(id)"; rm -rf ~/habit-app #') in script
    assert NONCE not in script and walkthrough.seal(NONCE, "JN2.2") in script


def test_the_container_gets_the_core_flows_sandbox_and_nothing_else(tmp_path, monkeypatch):
    box = Box(run_frame("setup.1"))

    _walk(tmp_path, monkeypatch, box)

    spec = box.spec
    assert (spec.image, spec.network, spec.memory, spec.cpus) == (
        company_config()["engineering"]["sandbox"]["image"], "sandbox", "4g", "2")
    assert spec.env == {"HOME": "/tmp"} and spec.command == ["sh", "-s"]
    assert [(m.container, m.read_only) for m in spec.mounts] == [("/work", True), ("/shots", False)]
    assert box.stdin.decode().endswith("\nmain\n")


def test_the_server_names_a_full_memory_and_a_refused_host_the_step_itself_hit(tmp_path, monkeypatch):
    windows = []
    monkeypatch.setattr(docker, "refused_hosts", lambda since, until: windows.append((since, until)) or {
        "registry.npmjs.org", "http-intake.logs.us5.datadoghq.com"})
    box = Box(run_frame("setup.1", "npm ERR! request to https://registry.npmjs.org/habit failed\n", exit=0,
                        start=2_000_000, ms=500)
              + run_frame("JN1.1", "Killed\n", exit=137, oom=1)
              + run_frame("JN3.1", "Killed\n", exit=137))

    walked = _walk(tmp_path, monkeypatch, box)

    assert walked["setup"][0]["signals"] == ["egress:registry.npmjs.org"]
    assert walked["steps"]["JN1.1"]["signals"] == ["oom"], "the container counted a process it killed for memory"
    assert walked["steps"]["JN3.1"]["signals"] == [], "exit 137 alone is also timeout -k or kill -9: not memory"
    assert windows[0] == (1_999_000, 2_001_500), "the step's own time, a second either side"


def test_a_run_that_started_but_sent_no_step_frame_is_out_of_time_never_docker_down(tmp_path, monkeypatch):
    start = f'@@{walkthrough.seal(NONCE, "start")}@@{{"kind":"start"}}\n'.encode()
    box = Box(b"\n" + start + b"x" * 5000)  # the product garbled or starved every frame after the start

    walked = _walk(tmp_path, monkeypatch, box)

    assert set(_why(walked).values()) == {"out_of_time", "unbound"}, "a fix round can repair it, so it fails the gate"
    script = box.stdin.decode()
    assert "printf '@@" not in script and script.count("printf '\\n@@") == 3, "every frame starts on a line of its own"


def test_a_key_written_into_a_bound_line_runs_but_is_never_stored_or_shown(tmp_path, monkeypatch):
    keyed = BINDING.replace('"habit init habit-app"', f'"habit init habit-app --token {GITHUB}"')
    box = Box(run_frame("setup.1") + run_frame("JN1.1"))

    walked = _walk(tmp_path, monkeypatch, box, binding=keyed)

    assert walked["steps"]["JN1.1"]["action"] == "habit init habit-app --token [secret:github_token]"
    assert _b(f"habit init habit-app --token {GITHUB}") in box.stdin.decode(), "the sandbox runs what the branch wrote"


def test_a_frame_after_a_line_the_product_left_open_is_still_read():
    said = b"progress 40%" + b"\n" + run_frame("JN1.1", "done\n")

    assert [f["id"] for f in walkthrough.frames(said, NONCE)] == ["JN1.1"]


def test_a_screen_is_kept_only_when_a_frame_names_it_and_it_is_a_png(tmp_path, monkeypatch):
    from justshipit.settings import settings
    stdout = b"".join(run_frame(sid) for sid in ("setup.1", "JN1.1", "JN1.2", "JN1.3")) + open_frame("JN2.1")
    box = Box(stdout, shots={"JN2.1.png": PNG, "JN2.2.png": PNG, "planted.png": PNG})

    walked = _walk(tmp_path, monkeypatch, box, job_id=41)

    kept = settings().var_dir / "dev-walk" / "J-41" / HEAD[:12]
    assert walked["steps"]["JN2.1"]["capture"]["shot"] == "JN2.1.png" and walked["steps"]["JN2.1"]["capture"]["exit"] is None
    assert sorted(p.name for p in kept.iterdir()) == ["JN2.1.png"] and (kept / "JN2.1.png").read_bytes() == PNG

    walked = _walk(tmp_path / "again", monkeypatch, Box(stdout, shots={"JN2.1.png": b"<svg onload=alert(1)>"}), job_id=41)
    assert walked["steps"]["JN2.1"]["not_run"] == "unreachable" and not kept.exists(), \
        "a file that is not a PNG is no capture, and the earlier walk of this head is gone"


def test_a_screen_the_product_replaced_with_a_link_is_not_followed(tmp_path, monkeypatch):
    outside = tmp_path / "host-secret.png"
    outside.write_bytes(PNG)

    def linking(spec, *, stdin=b"", timeout_s=600):
        shots = next(m.host for m in spec.mounts if m.container == "/shots")
        try:
            os.symlink(outside, shots / "JN2.1.png")
        except OSError:
            pytest.skip("this system makes no symbolic links")
        return docker.ContainerResult(0, b"".join(run_frame(s) for s in ("setup.1", "JN1.1", "JN1.2", "JN1.3"))
                                      + open_frame("JN2.1"), b"", False)
    monkeypatch.setattr(docker, "run", linking)

    walked = _walk(tmp_path, monkeypatch, None)

    assert walked["steps"]["JN2.1"]["not_run"] == "unreachable"


def test_a_show_file_joins_its_capture_and_a_link_one_is_refused(tmp_path, monkeypatch):
    spec_md = b"# Today\n- streaks: a daily check\n"
    box = Box(run_frame("setup.1") + run_frame("JN1.1")
              + run_frame("JN1.2", "wrote out/spec.md\n", show="ok", show_head=spec_md, show_size=len(spec_md))
              + run_frame("JN1.3", "listed\n", show="link"))

    walked = _walk(tmp_path, monkeypatch, box)

    assert walked["steps"]["JN1.2"]["capture"]["file"] == {
        "path": "out/spec.md", "text": "# Today\n- streaks: a daily check", "lines": 2, "volatile": [],
        "binary": False, "size": len(spec_md)}
    assert walked["steps"]["JN1.3"]["capture"]["file"] is None


def test_a_seed_is_offered_as_committed_with_its_secrets_removed(tmp_path, monkeypatch):
    walked = _walk(tmp_path, monkeypatch, Box(run_frame("setup.1")))

    assert walked["steps"]["JN1.2"]["seed"] == [
        {"path": "habit-app/features.md", "text": "- streaks\nNPM_TOKEN=[secret:github_token]\n"}]
    assert walked["steps"]["JN1.1"]["seed"] == []


def test_an_output_cut_in_the_container_counts_every_line_and_drops_a_half_key():
    out = "".join(f"line {n}\n" for n in range(1, 100)).encode()
    tail = b"key body 1\nkey body 2\n-----END RSA PRIVATE KEY-----\nlast 1\nlast 2\n"
    [frame] = walkthrough.frames(run_frame("JN1.1", out, tail=b"partial line\n" + tail, lines=9000), NONCE)

    ran = walkthrough._captured("JN1.1", {"run": "habit dump"}, {"JN1.1": frame}, "out_of_time", [], frozenset())

    assert "key body" not in ran["capture"]["text"] and ran["capture"]["text"].endswith("last 1\nlast 2")
    assert ran["capture"]["lines"] == 9000 and ran["capture"]["cut_after"] == 60


# ---- the screens route

def _job(s, project) -> DevJob:
    spec = Spec(spec_key=f"SPEC-WALK-{project.id}", project_id=project.id, title="t", registry_path="specs/walk")
    s.add(spec)
    s.flush()
    job = DevJob(spec_id=spec.id, status="DELIVERED", attempt=1)
    s.add(job)
    s.commit()
    return job


def test_a_kept_screen_is_served_as_an_image_and_nothing_else_is(s, project, api_token):
    from justshipit.api.app import app
    job = _job(s, project)
    folder = try_it.walk_dir(job.id, HEAD)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "JN2.1.png").write_bytes(PNG)
    (folder / "notes.txt").write_text("x")
    client = TestClient(app, base_url="http://127.0.0.1:8000",
                        headers={"X-Ship": "1", "Authorization": f"Bearer {api_token}"})

    got = client.get(f"/api/dev-jobs/{job.id}/walk/{HEAD[:12]}/JN2.1.png")
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png" and got.headers["x-content-type-options"] == "nosniff"
    for path in (f"{HEAD[:12]}/JN9.9.png", f"{HEAD[:12]}/notes.txt", f"{HEAD[:12].upper()}/JN2.1.png",
                 f"{HEAD[:12]}x/JN2.1.png", f"{HEAD[:6]}/JN2.1.png"):
        assert client.get(f"/api/dev-jobs/{job.id}/walk/{path}").status_code == 404, path
    assert client.get(f"/api/dev-jobs/{job.id + 1000}/walk/{HEAD[:12]}/JN2.1.png").status_code == 404
    for path in (f"..%2F..%2F{HEAD[:12]}/JN2.1.png", f"{HEAD[:12]}/..%2FJN2.1.png"):
        assert client.get(f"/api/dev-jobs/{job.id}/walk/{path}").content != PNG, path


def test_the_walk_route_names_only_its_own_files():
    assert try_it.walk_file(3, HEAD, "../../secrets/github_token") is None
    assert try_it.walk_file(3, "../" + HEAD, "JN1.1.png") is None
    assert try_it.walk_file(3, HEAD, "JN1.1.png") is None, "no such capture"


# ---- the real runner, once, in the engineer image

_IMAGE = company_config()["engineering"]["sandbox"]["image"]


def _engineer_image_sees_this_filesystem() -> bool:
    """False without Docker or the image, and in a container that talks to the host's daemon: a path it binds names a
    host folder (tests/sandbox_kit.py)."""
    if not (docker.docker_ok() and docker.image_present(_IMAGE)):
        return False
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "marker").write_text("x")
        try:
            probe = subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{Path(d).resolve()}:/probe:ro",
                                    _IMAGE, "test", "-f", "/probe/marker"], capture_output=True, timeout=120,
                                   check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0


PRODUCT = r"""#!/bin/sh
case "$1" in
  init) mkdir -p "$2" && echo "Created $2 in $(pwd) at $(date -u +%Y-%m-%dT%H:%M:%SZ)" ;;
  gen) mkdir -p "$3" && { echo "# spec"; cat "$2"; } > "$3/spec.md" && printf '\033[32mwrote %s\033[0m\n' "$3/spec.md" ;;
  listen) ( sleep 0.3; timeout 20 cat /proc/1/fd/1 > /tmp/stolen ) < /dev/null > /dev/null 2>&1 & echo listening ;;
  link) ln -s /etc/passwd leak && echo linked ;;
  check) echo "$2 line 2: unknown state 'lol'" >&2; exit 3 ;;
  *) echo "usage: habit init|gen|listen|link|check"; exit 2 ;;
esac
"""
REAL = """\
setup:
  - chmod +x habit
steps:
  JN1.1: { run: "./habit listen" }
  JN1.2: { run: "./habit init habit-app" }
  JN1.3: { run: "./habit gen in.md out", show: "out/spec.md", seed: { "in.md": "fixtures/journeys/features.md" } }
  JN2.1: { run: "./habit link", show: "leak" }
  JN2.2: { open: "/" }
  JN3.1: { run: "./habit check broken.md", expect: error }
  JN3.2: { run: "./habit nope" }
"""


@pytest.mark.skipif(not _engineer_image_sees_this_filesystem(),
                    reason=f"needs Docker, {_IMAGE}, and bind mounts that reach this filesystem")
def test_the_runner_runs_in_the_engineer_image_and_its_frames_come_back_whole(tmp_path, monkeypatch):
    real_run = docker.run
    seen = {}

    def offline(spec, **kw):  # the egress proxy is no part of this check
        spec.network = "none"
        res = real_run(spec, **kw)
        seen["res"] = res
        return res
    monkeypatch.setattr(docker, "run", offline)
    monkeypatch.setattr(walkthrough, "_nonce", lambda: os.urandom(16).hex())
    files ={"habit": PRODUCT, "scripts/preview.sh": "cd site && exec python3 -m http.server 4173 --bind 127.0.0.1\n",
             "site/index.html": "<h1>Today</h1>"}

    walked = walkthrough.walk(_repo(tmp_path, REAL, files), "HEAD", DOC, job_id=55, head=HEAD, language="en")

    steps = walked["steps"]
    assert seen["res"].exit_code == 0 and walked["lint"] == [], seen["res"].stderr[-2000:]
    assert walked["setup"][0]["capture"]["exit"] == 0
    assert steps["JN1.2"]["capture"]["text"].startswith("Created habit-app in . at ")
    assert steps["JN1.2"]["capture"]["volatile"], "the time it printed is marked"
    assert steps["JN1.3"]["capture"]["text"] == "wrote out/spec.md"
    assert steps["JN1.3"]["capture"]["file"]["text"] == "# spec\n- streaks\nNPM_TOKEN=[secret:github_token]"
    assert steps["JN2.1"]["capture"]["text"] == "linked" and steps["JN2.1"]["capture"]["file"] is None
    assert steps["JN2.2"]["capture"]["shot"] == "JN2.2.png"
    assert (try_it.walk_dir(55, HEAD) / "JN2.2.png").read_bytes().startswith(b"\x89PNG")
    assert steps["JN3.1"]["capture"]["exit"] == 3 and "unknown state" in steps["JN3.1"]["capture"]["text"]
    assert steps["JN3.2"]["capture"]["exit"] == 2, "a step after one that failed as expected still runs"
    assert all(r["capture"] for r in [*walked["setup"], *steps.values()]), \
        "a step listening on the container's output stole no frame"
