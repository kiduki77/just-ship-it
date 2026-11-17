"""The merge card's walkthrough: the spec's user journeys (engineering/journeys.py) run step by step on the delivered
build, the way a person would, with what each step really printed.

The engineer binds each journey step to an action in the branch's `scripts/walkthrough.yaml` (`lint` holds it to the
journeys). At the acceptance check the worker runs the binding in the sandbox from a runner script the server writes
(`runner`, `walk`), never one the branch supplies, and cleans what each step printed before anything stores it
(`clean`). The product agent judges each capture against the step's `see`, and `held` has the last word on what
counts as passed, not run, and whose fault. The record is kept per delivered commit in `DevJob.result["walkthroughs"]`
(`store`), so a card reads the run of its own head.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

import yaml

from justshipit.contracts import secrets_scan
from justshipit.contracts.handoff import StepVerdictOut, language_tag
from justshipit.engineering import journeys, try_it
from justshipit.i18n.said import SaidError
from justshipit.sandbox import docker
from justshipit.settings import company_config

BINDING = "scripts/walkthrough.yaml"  # under scripts/: the worker clears whatever the branch puts in .ship/
SEEDS = "fixtures/journeys/"          # where a step's `seed` sample inputs live in the branch
KEPT_HEADS = 3                        # runs kept on a job, newest last

StepState = Literal["passed", "differs", "not_run"]
Fault = Literal["product", "scenario", "environment"]
NotRun = Literal["unbound", "no_docker", "setup_failed", "earlier_failed", "out_of_time", "needs", "unreachable"]
Kind = Literal["run", "open"]
Expect = Literal["ok", "error"]


class FileHead(TypedDict):
    """The first lines of the file a step's `show` names, cleaned like its output (40 lines, 3000 characters). A
    binary file has no text, only its size."""
    path: str
    text: str
    lines: int
    volatile: list[tuple[int, int]]
    binary: bool
    size: int


class Capture(TypedDict):
    """What one step printed, cleaned (`clean`): 60 head and 20 tail lines, at most 6000 characters. `lines` counts
    the lines before cutting. An `open` step has no exit; its `shot` is the file name `<step id>.png` the worker kept under
    var/dev-walk/J-<job>/<head[:12]>/, which GET /api/dev-jobs/<job>/walk/<head[:12]>/<file> serves."""
    text: str
    lines: int
    cut_after: int | None            # lines of `text` before the cut (the head), None when nothing was cut
    exit: int | None
    ms: int
    shot: str | None
    file: FileHead | None            # the `show` file's head
    volatile: list[tuple[int, int]]  # [start, end) spans of `text` that differ on every run
    scrubbed: list[dict]             # [{"kind", "n"}] from secrets_scan.scrub, never the value


class Seed(TypedDict):
    path: str   # where the step expects it, relative to the journey folder (habit-app/features.md)
    text: str   # the sample input as the owner copies it, cleaned like a capture


class Ran(TypedDict):
    """One bound step as the worker ran it, before any verdict. `capture` None means it did not run, and `not_run`
    says why. `signals` are what the server itself observed of the environment: "egress:<host>" for a host the
    egress proxy refused during the step that the step's output also names, "oom" for exit 137 under the memory cap."""
    id: str
    kind: Kind
    action: str     # the exact command, or the path on the preview address
    expect: Expect
    capture: Capture | None
    not_run: NotRun | None
    signals: list[str]
    seed: list[Seed]


class Walk(TypedDict):
    """What `walk` gives: the setup lines (ids setup.1, setup.2...) and every journey step by id, each journey step of
    the spec present, bound or not."""
    setup: list[Ran]
    steps: dict[str, Ran]
    setup_ms: int
    ms: int
    lint: list[str]


class StepRun(TypedDict):
    """A step as the card shows it: the spec's words copied at acceptance, what ran, and `held`'s verdict."""
    id: str
    do: str
    why: str
    see: str
    kind: Kind
    action: str
    expect: Expect
    seed: list[Seed]
    capture: Capture | None
    state: StepState
    not_run: NotRun | None
    need: str | None
    fault: Fault | None
    fault_by: Literal["server", "agent"] | None
    judged: bool        # False for a captured step the product agent gave no verdict ("not judged")
    looks_like: str
    hint: str


class WalkthroughRun(TypedDict):
    """One acceptance's walkthrough of one delivered commit. It copies the spec's about, journeys and stories, so the
    card never reads the registry and an old card keeps its own text. Each journey is its journeys.yaml fields with
    `steps: list[StepRun]`; `stories` are stories.yaml's with full scenario text and `guards`."""
    v: Literal[1]
    head: str
    spec_commit: str
    ran_at: str
    ms: int
    setup_ms: int
    about: dict | None
    setup: list[StepRun]
    journeys: list[dict]
    stories: list[dict]


# ---- the binding (phase 2)

STEP_ID = re.compile(r"JN\d+\.\d+")
RUN_CHARS = 300                       # one line on the card (merge_review._STEP)
_BINDING_BYTES = 64_000               # more than any real binding: the host reads it from a clone the engineer had
SEED_BYTES = 20_000                   # a sample input the owner copies from the card
_FIELDS = ("run", "open", "show", "seed", "expect")
_EXPECT = ("ok", "error")
_PRINTS = frozenset({"echo", "printf", "cat", "true", ":"})  # a run of only these never calls the product
_BETWEEN = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")"})
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SUBSTITUTION = re.compile(r"\$\(([^()]+)\)|`([^`]+)`")
_SEED_DIR = PurePosixPath(SEEDS).parts
_JOURNEYS = ".ship/spec/journeys.yaml"


class _NoAliases(yaml.SafeLoader):
    """A binding has no use for anchors: an alias repeated inside an alias turns 48 KB into millions of entries."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("anchors and aliases (& and *) are not allowed")
        return super().compose_node(parent, index)


def read_binding(root: Path) -> tuple[dict | None, list[str]]:
    """The branch's `scripts/walkthrough.yaml` under `root` as {"setup": [str], "steps": {id: {"run" | "open",
    "show"?, "seed"?, "expect"?}}}, and what is wrong with its shape. None when the branch has none. Never follows a
    link."""
    from justshipit.engineering import worker  # the worker imports this module
    try:
        with open(worker._host_path(root, BINDING), "rb") as f:
            data = f.read(_BINDING_BYTES + 1)
    except FileNotFoundError:
        return None, []
    except (OSError, SaidError):
        return None, [f"`{BINDING}` is a link or not a plain file: make it a plain file the branch commits."]
    if len(data) > _BINDING_BYTES:
        return None, [f"`{BINDING}` is larger than {_BINDING_BYTES} bytes: it holds actions only, never output."]
    try:
        doc = yaml.load(data.decode("utf-8", "replace"), Loader=_NoAliases)  # noqa: S506 - a SafeLoader
    except yaml.YAMLError as exc:
        return None, [f"`{BINDING}` is not valid YAML: {' '.join(str(exc).split())[:300]}"]
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        return None, [f"`{BINDING}` must be a mapping with `setup:` and `steps:`."]
    problems = [f"`{BINDING}` has an unknown key `{k}`: it holds only `setup:` and `steps:`."
                for k in doc if k not in ("setup", "steps")]
    setup = doc.get("setup") or []
    if not isinstance(setup, list):
        problems.append(f"`setup:` in `{BINDING}` must be a list of shell lines.")
        setup = []
    lines = []
    for i, line in enumerate(setup, 1):
        if isinstance(line, str):
            lines.append(line)
        else:
            problems.append(f"`setup:` line {i} in `{BINDING}` is not text: write it as one shell line.")
    raw = doc.get("steps") or {}
    if not isinstance(raw, dict):
        problems.append(f"`steps:` in `{BINDING}` must map each step id to its action.")
        raw = {}
    steps = {}
    for key, value in raw.items():
        sid = str(key)
        if not isinstance(value, dict):
            problems.append(f"{sid} in `{BINDING}` must be a mapping such as `{{run: <shell line>}}`.")
            continue
        steps[sid] = entry = {}
        for name, field in value.items():
            if name not in _FIELDS:
                problems.append(f"{sid} has an unknown key `{name}`: a step takes {', '.join(_FIELDS)}.")
            elif name == "seed":
                if isinstance(field, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in field.items()):
                    entry["seed"] = dict(field)
                else:
                    problems.append(f"{sid}'s `seed` must map where the step reads each file to its source in "
                                    f"`{SEEDS}`.")
            elif isinstance(field, str):
                entry[name] = field
            else:
                problems.append(f"{sid}'s `{name}` is not text.")
        for src in (entry.get("seed") or {}).values():
            if _seed_source(src) and not _plain_file(root, src):
                problems.append(f"{sid}'s `seed` source {src} is not a plain file of at most {SEED_BYTES} bytes in "
                                "the branch: commit it there.")
    return {"setup": lines, "steps": steps}, problems


def _plain_file(root: Path, rel: str) -> bool:
    from justshipit.engineering import worker  # the worker imports this module
    try:
        st = os.lstat(worker._host_path(root, rel))
    except (OSError, SaidError):
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size <= SEED_BYTES


def lint(binding: dict | None, doc: dict) -> list[str]:
    """What the engineer must fix in the binding, one line each: unbound steps, unknown ids, a `run` that never calls
    the product, a `show` or `seed` outside the folder, a `run` over 300 characters."""
    ids = list(journeys.steps(doc))
    if not ids:
        return []
    if binding is None:
        return [(f"There is no `{BINDING}`, so the worker cannot run any step of `{_JOURNEYS}`. Write it: under "
                 "`steps:`, each step id mapped to `{run: <one shell line>}` or `{open: /path}`.")]
    out = [f"`setup:` line {i}{problem}" for i, line in enumerate(binding["setup"], 1)
           if (problem := _line_problem(line))]
    steps = binding["steps"]
    if unbound := [sid for sid in ids if sid not in steps]:
        out.append(f"These journey steps have no entry under `steps:` in `{BINDING}`: {', '.join(unbound)}. Bind "
                   "each to the `run` line or the `open` path a person uses for it.")
    if unknown := [sid for sid in steps if sid not in ids]:
        out.append(f"These `{BINDING}` steps name no step of `{_JOURNEYS}`: {', '.join(unknown)}. Use the ids "
                   "exactly as journeys.yaml gives them.")
    for sid, entry in steps.items():
        if sid in ids:
            out += entry_problems(sid, entry)
    return out


def entry_problems(sid: str, entry: dict) -> list[str]:
    """What keeps one bound step from running: the worker runs only an entry with none, and shows the rest as not
    bound, so a capture never comes from a line that only prints."""
    run, opened = entry.get("run") or "", entry.get("open") or ""
    out = []
    if run and opened:
        out.append(f"{sid} has both `run` and `open`: a step is one or the other.")
    elif not run and not opened:
        out.append(f"{sid} has neither `run` nor `open`.")
    if run:
        if problem := _line_problem(run):
            out.append(f"{sid}'s `run`{problem}")
        elif (words := _commands(run)) is None:
            out.append(f"{sid}'s `run` is not a valid shell line: a quote is left open.")
        elif all(w in _PRINTS for w in words):
            out.append(f"{sid}'s `run` never calls the product: it only runs echo, printf, cat, true or :. Run the "
                       "product's own command, the way its README tells a person to, and let it print.")
    if opened and not (opened.startswith("/") and len(opened) <= RUN_CHARS and not re.search(r"\s", opened)
                       and not any(_hidden(c) for c in opened)):
        out.append(f"{sid}'s `open` is not a path on the preview address: it starts with / and has no spaces.")
    if "show" in entry and not _inside(entry["show"]):
        out.append(f"{sid}'s `show` must be a file inside the folder the step runs in: a relative path with no `..`.")
    for dest, src in (entry.get("seed") or {}).items():
        if not _inside(dest):
            out.append(f"{sid}'s `seed` puts a file at {dest}, outside the folder the step runs in: give a relative "
                       "path with no `..`.")
        if not _seed_source(src):
            out.append(f"{sid}'s `seed` for {dest} must come from `{SEEDS}`: {src} does not.")
    if entry.get("expect", "ok") not in _EXPECT:
        out.append(f"{sid}'s `expect` is {entry['expect']!r}: it is ok (the default) or error.")
    return out


def _hidden(c: str) -> bool:
    """A character the card would not show as the owner copies it: a control (C0, DEL, C1) or a format character
    (a bidi override, a zero-width space)."""
    return c != "\t" and unicodedata.category(c) in ("Cc", "Cf")


def _line_problem(line: str) -> str:
    """What is wrong with a shell line as the card shows it, worded to follow its name, or ''."""
    if not line.strip():
        return " is empty."
    if "\n" in line or "\r" in line:
        return " has a line break: it must be one shell line."
    if any(_hidden(c) for c in line):
        return " has a control or invisible character: write it as a person types it."
    if len(line) > RUN_CHARS:
        return f" is {len(line)} characters: keep it to one line of at most {RUN_CHARS}."
    return ""


def _commands(line: str) -> list[str] | None:
    """The command word of each simple command in a shell line and in its command substitutions (assignments and
    redirections skipped), or None for a line whose quotes do not close."""
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return None
    words, first = [], True
    for tok in tokens:
        if tok in _BETWEEN:
            first = True
        elif first and not _ASSIGN.match(tok) and not set(tok) <= set("<>&"):
            words.append(tok)
            first = False
    for inner in _SUBSTITUTION.findall(line):
        words += _commands(next(x for x in inner if x)) or []
    return words


def _inside(rel: str) -> bool:
    """A path that stays in the folder it is read from: relative, with no `..` and no control character."""
    p = PurePosixPath(rel)
    return (bool(p.parts) and not p.is_absolute() and ".." not in p.parts
            and not any(ord(c) < 32 or c == "\\" for c in rel))


def _seed_source(src: str) -> bool:
    parts = PurePosixPath(src).parts
    return _inside(src) and parts[:len(_SEED_DIR)] == _SEED_DIR and len(parts) > len(_SEED_DIR)


# ---- the run (phase 3)

RAN_ID = re.compile(r"JN\d+\.\d+|setup\.\d+")
STEP_S = 90                        # one journey step, as `timeout` holds it
_SHOT_S = 60                       # one capture of a screen
_PREVIEW_WAIT_S = 180              # a preview's first answer, as the screen capture waits for it (worker._capture)
_MARGIN_S = 30                     # under the container cap, so the last step's frame still gets out
HEAD_BYTES, TAIL_BYTES = 512 * 1024, 128 * 1024  # of a step's output the container sends; clean keeps far less
SHOW_BYTES = 64 * 1024             # of a show file's head
_WALK = "/tmp/walk"                # in the container: base/ is set up once, j<n>/ is journey n's copy of it
_SHOTS = "/shots"                  # the mounted folder the open steps' captures land in
_MAX_SHOT_BYTES = 10_000_000
_PNG = b"\x89PNG\r\n\x1a\n"
_SHOW_STATES = ("", "ok", "link", "outside", "missing", "notfile")

# The script the container runs, its plan of calls appended as main(): every command, path and id in a call is
# base64, so no text the branch wrote is ever read as shell. The whole script is read before any of it runs, and
# each step runs with stdin from /dev/null, so no step reads the rest of the plan.
#
# A frame is written while every other process in the container is stopped (kill -STOP -1 spares the caller and
# PID 1, which is this script), and they resume only once the pipe to the host is read empty: a process of the
# same user can open /proc/1/fd/1 and read what this script writes, so a product never sees a frame in flight.
# The signal only asks: a reader already woken on another CPU still copies what is in the pipe before it stops, so
# hold() waits until /proc shows every other thread stopped, asking again for any forked meanwhile.
# Its tools come from the image's own folders, not from $HOME/.local/bin, which a step could fill.
_RUNNER = r"""set -u
SP="$HOME/.local/bin:$PATH"
PATH=/usr/bin:/bin
W=@WALK@
O="$W/.out"
A=@ADDRESS@
END=0 J= F= P= UP=0
SS= SF= SZ=0

now() { t=$(date +%s%3N); case $t in ''|*[!0-9]*) t=$(( $(date +%s) * 1000 ));; esac; echo "$t"; }
due() { [ $(( $(now) + $1 * 1000 )) -le "$END" ]; }
dec() { printf %s "$1" | base64 -d; }
b64() { base64 | tr -d '\n'; }
num() { case ${1:-} in ''|*[!0-9]*) echo 0;; *) echo "$1";; esac; }
hold() {
  tries=0
  while [ "$tries" -lt 200 ]; do
    kill -STOP -1 2>/dev/null
    busy=
    for st in /proc/[0-9]*/task/[0-9]*/stat; do
      case $st in /proc/1/*) continue ;; esac
      read -r line < "$st" 2>/dev/null || continue
      state=${line##*) }
      case ${state%% *} in T|t|Z|X|x) ;; *) busy=1; break ;; esac
    done
    [ -z "$busy" ] && return 0
    tries=$((tries + 1))
  done
}
drain() {
  python3 -I -S -c 'import array, fcntl, termios, time
a = array.array("i", [0]); end = time.monotonic() + 3
while time.monotonic() < end:
    fcntl.ioctl(1, termios.FIONREAD, a, True)
    if not a[0]: break
    time.sleep(0.005)' 2>/dev/null || sleep 0.2
}
free() { drain; kill -CONT -1 2>/dev/null; }

start() {
  END=$(( $(now) + @BUDGET@ * 1000 ))
  mkdir -p "$W/base" && cp -a /work/. "$W/base/" || exit 0
  printf '\n@@%s@@{"kind":"start"}\n' "$1"
}

oomk() { num "$(sed -n 's/^oom_kill //p' /sys/fs/cgroup/memory.events 2>/dev/null)"; }

show_state() {
  SS= SF= SZ=0
  [ -n "$1" ] || return 0
  f="$J/$1"
  if [ -L "$f" ]; then SS=link
  elif [ ! -e "$f" ]; then SS=missing
  else
    r=$(realpath -e -- "$f" 2>/dev/null) || r=
    case $r in
      "$J"/*) if [ -f "$r" ]; then SS=ok; SF=$r; SZ=$(num "$(wc -c < "$r")"); else SS=notfile; fi ;;
      *) SS=outside ;;
    esac
  fi
}

frame_run() {
  size=$(num "$(wc -c < "$O")"); lines=$(num "$(wc -l < "$O")")
  printf '\n@@%s@@{"kind":"run","id":"%s","exit":%s,"oom":%s,"ms":%s,"start":%s,"end":%s,"size":%s,"lines":%s,"out":"' \
    "$1" "$2" "$3" "$9" $(( $5 - $4 )) "$4" "$5" "$size" "$lines"
  if [ "$size" -gt $(( @HEAD@ + @TAIL@ )) ]; then
    head -c @HEAD@ -- "$O" | b64; printf '","tail":"'; tail -c @TAIL@ -- "$O" | b64
  else
    b64 < "$O"; printf '","tail":"'
  fi
  printf '","show":"%s","show_size":%s,"show_head":"' "$6" "$8"
  if [ "$6" = ok ]; then head -c @SHOW@ -- "$7" | b64; fi
  printf '"}\n'
}

seeds() {
  while [ $# -ge 2 ]; do
    d=$(dec "$1"); src=$(dec "$2"); shift 2
    mkdir -p -- "$(dirname -- "$d")" && cp -- "/work/$src" "$d"
  done >> "$O" 2>&1
}

setup_line() {
  cd "$W/base" || exit 0
  : > "$O"
  t0=$(now); left=$(( (END - t0) / 1000 ))
  [ "$left" -ge 1 ] || exit 0
  k0=$(oomk)
  timeout -k 5 "$left" env PATH="$SP" /bin/sh -c "$(dec "$3")" < /dev/null >> "$O" 2>&1
  rc=$?
  t1=$(now)
  hold; frame_run "$1" "$2" "$rc" "$t0" "$t1" "" "" 0 $(( $(oomk) > k0 )); free
  [ "$rc" -eq 0 ] || exit 0
}

journey() {
  journey_end
  J="$W/j$1"; F=; UP=0
  cp -a "$W/base" "$J" || exit 0
}

journey_end() {
  if [ -n "$P" ] && [ "$P" != none ]; then
    kill -s TERM -- "-$P" 2>/dev/null; sleep 1; kill -s KILL -- "-$P" 2>/dev/null
    i=0; while [ "$i" -lt 10 ] && curl -s -o /dev/null --noproxy '*' --max-time 1 "$A/"; do i=$((i + 1)); sleep 1; done
  fi
  P=
}

run_step() {
  [ -z "$F" ] || return 0
  due $(( @STEP@ + 10 )) || exit 0
  seal=$1 id=$2 expect=$3 cmd=$4 show=$5; shift 5
  cd "$J" || exit 0
  : > "$O"
  seeds "$@"
  t0=$(now)
  k0=$(oomk)
  timeout -k 5 @STEP@ env PATH="$SP" /bin/sh -c "$(dec "$cmd")" < /dev/null >> "$O" 2>&1
  rc=$?
  t1=$(now)
  hold; show_state "$(dec "$show")"
  frame_run "$seal" "$id" "$rc" "$t0" "$t1" "$SS" "$SF" "$SZ" $(( $(oomk) > k0 )); free
  case $expect in error) [ "$rc" -ne 0 ] || F=1 ;; *) [ "$rc" -eq 0 ] || F=1 ;; esac
}

preview() {
  P=none UP=0
  [ -f scripts/preview.sh ] || return 0
  setsid env PATH="$SP" /bin/sh scripts/preview.sh < /dev/null > "$W/.preview.log" 2>&1 &
  P=$!
  i=0 gone=0
  while [ "$i" -lt @WAIT@ ]; do
    due $(( @SHOT@ + 10 )) || exit 0
    curl -s -o /dev/null --noproxy '*' --max-time 5 "$A/" && { UP=1; return 0; }
    kill -0 "$P" 2>/dev/null || gone=$((gone + 1))
    [ "$gone" -le 5 ] || return 0
    i=$((i + 1)); sleep 1
  done
}

open_step() {
  [ -z "$F" ] || return 0
  due $(( @SHOT@ + 10 )) || exit 0
  seal=$1 id=$2 path=$3; shift 3
  n=$(dec "$id")
  cd "$J" || exit 0
  : > "$O"
  seeds "$@"
  t0=$(now)
  [ -n "$P" ] || preview
  shot=0
  if [ "$UP" = 1 ]; then
    rm -f -- "@SHOTS@/$n.png"
    timeout -k 5 @SHOT@ chromium --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --no-proxy-server \
      --no-first-run --hide-scrollbars --lang=@LANG@ --force-device-scale-factor=1 --window-size=1440,900 \
      --virtual-time-budget=5000 --screenshot="@SHOTS@/$n.png" "$A$(dec "$path")" < /dev/null > /dev/null 2>&1
    [ -s "@SHOTS@/$n.png" ] && shot=1
  fi
  t1=$(now)
  hold
  printf '\n@@%s@@{"kind":"open","id":"%s","up":%s,"shot":%s,"ms":%s,"start":%s,"end":%s}\n' \
    "$seal" "$id" "$UP" "$shot" $(( t1 - t0 )) "$t0" "$t1"
  free
  [ "$shot" = 1 ] || F=1
}
"""


def seal(nonce: str, sid: str) -> str:
    """The mark a step's frame starts with. The run's nonce never enters the container, only each step's seal, and
    one step's seal marks no other step's frame."""
    return hmac.new(nonce.encode(), sid.encode(), hashlib.sha256).hexdigest()[:32]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _call(name: str, *args: str) -> str:
    return " ".join([name, *(f"'{a}'" for a in args)])


def _numbers(sid: str) -> tuple[int, int]:
    j, s = sid[2:].split(".")
    return int(j), int(s)


def runner(binding: dict, nonce: str, *, language: str | None = None, budget_s: int = 1200 - _MARGIN_S) -> str:
    """The POSIX sh script one container runs: setup once, a clean copy of the set-up folder per journey, each step
    under `timeout 90`, one `@@<seal>@@` JSON frame per step with base64 output. `binding` holds only entries that
    run (`entry_problems` finds nothing); steps go in journey order, and a journey with none is skipped. `budget_s`
    is the time the steps may start in; `language` is the browser's."""
    calls = [_call("start", seal(nonce, _START))]
    for i, line in enumerate(binding["setup"], 1):
        calls.append(_call("setup_line", seal(nonce, f"setup.{i}"), _b64(f"setup.{i}"), _b64(line)))
    by_journey: dict[int, list[str]] = {}
    for sid in sorted((x for x in binding["steps"] if STEP_ID.fullmatch(x)), key=_numbers):
        by_journey.setdefault(_numbers(sid)[0], []).append(sid)
    for n, sids in enumerate(by_journey.values(), 1):
        calls.append(f"journey {n}")
        for sid in sids:
            entry = binding["steps"][sid]
            seeds = [_b64(x) for pair in (entry.get("seed") or {}).items() for x in pair]
            if entry.get("open"):
                calls.append(_call("open_step", seal(nonce, sid), _b64(sid), _b64(entry["open"]), *seeds))
            else:
                calls.append(_call("run_step", seal(nonce, sid), _b64(sid),
                                   "error" if entry.get("expect") == "error" else "ok", _b64(entry["run"]),
                                   _b64(entry.get("show") or ""), *seeds))
    calls.append("journey_end")
    script = _RUNNER
    for token, value in {"@WALK@": _WALK, "@ADDRESS@": try_it.PREVIEW_ADDRESS, "@BUDGET@": str(int(budget_s)),
                         "@HEAD@": str(HEAD_BYTES), "@TAIL@": str(TAIL_BYTES), "@SHOW@": str(SHOW_BYTES),
                         "@STEP@": str(STEP_S), "@SHOT@": str(_SHOT_S), "@WAIT@": str(_PREVIEW_WAIT_S),
                         "@SHOTS@": _SHOTS, "@LANG@": language_tag(language) or "en"}.items():
        script = script.replace(token, value)
    return script + "main() {\n" + "".join(f"  {c}\n" for c in calls) + "}\nmain\n"


_FRAME = re.compile(rb"@@([0-9a-f]{32})@@(\{.*\})")
# The runner's first frame, sealed before any of the branch's code runs: a run that printed it ran the script, so a
# step with no frame after it is out of time, never Docker that could not be reached.
_START = "start"


def started(stdout: bytes, nonce: str) -> bool:
    mark = b"@@" + seal(nonce, _START).encode() + b'@@{"kind":"start"}'
    return any(line.rstrip(b"\r") == mark for line in stdout.split(b"\n"))


def frames(stdout: bytes, nonce: str) -> list[dict]:
    """The frames this run's nonce marks, decoded; anything else the container printed is ignored. The first sealed
    frame of a step is the step's; a later one, or one whose JSON or base64 does not decode, is not."""
    out, seen = [], set()
    for line in stdout.split(b"\n"):
        if not (m := _FRAME.fullmatch(line.rstrip(b"\r"))):
            continue
        try:
            frame = _frame(json.loads(m[2]))
        except (ValueError, KeyError, TypeError):  # binascii.Error and json's errors are ValueErrors
            continue
        if frame is None or frame["id"] in seen or not hmac.compare_digest(m[1].decode(), seal(nonce, frame["id"])):
            continue
        seen.add(frame["id"])
        out.append(frame)
    return out


def _int(value: object) -> int:
    if type(value) is not int:
        raise TypeError(value)
    return value


def _bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise TypeError(value)
    return base64.b64decode(value, validate=True)


def _frame(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    sid = _bytes(raw["id"]).decode("ascii")
    if not RAN_ID.fullmatch(sid):
        return None
    times = {"id": sid, "kind": raw["kind"], "ms": _int(raw["ms"]), "start": _int(raw["start"]),
             "end": _int(raw["end"])}
    if raw["kind"] == "open":
        up, shot = _int(raw["up"]), _int(raw["shot"])
        return {**times, "up": up == 1, "shot": shot == 1} if {up, shot} <= {0, 1} else None
    if raw["kind"] != "run" or raw["show"] not in _SHOW_STATES:
        return None
    return {**times, "exit": _int(raw["exit"]), "oom": _int(raw.get("oom", 0)) == 1, "size": _int(raw["size"]),
            "lines": _int(raw["lines"]),
            "out": _bytes(raw["out"]), "tail": _bytes(raw["tail"]), "show": raw["show"],
            "show_size": _int(raw["show_size"]), "show_head": _bytes(raw["show_head"])}


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]|\x9b[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_HERE = re.compile(rf"(?<![\w./~-])(?:{re.escape(_WALK)}/(?:base|j\d+)|/work)(?=/|(?![\w.-]))")
_HOME = re.compile(r"(?<![\w./~-])/tmp(?=/|(?![\w.-]))")
_VOLATILE = re.compile("|".join((
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?",  # ISO dates
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",  # UUIDs
    r"\b\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\b",  # clock times
    r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{8,}\b",  # hex runs with a digit: hashes, ids
    r"(?<![\w.])\d+(?:\.\d+)? ?(?:ms|s)\b",  # durations
)))
_LINES, _TAIL_LINES, _CHARS = 60, 20, 6000
_FILE_LINES, _FILE_CHARS = 40, 3000


def _tidy(raw: bytes) -> tuple[str, list[dict]]:
    """Text as a terminal would have shown it (a carriage return starts its line again), with no escape or control
    character but newline and tab, the container's folders as the owner's, and every secret replaced by its kind."""
    text = _ANSI.sub("", raw.decode("utf-8", "replace").replace("\r\n", "\n"))
    text = _CONTROL.sub("", "\n".join(line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n")))
    text = _HOME.sub("~", _HERE.sub(".", text))
    return secrets_scan.scrub(text, lambda kind: f"[secret:{kind}]")


def _cut(text: str, *, head: int, tail: int, chars: int) -> tuple[str, int, int | None]:
    """The first `head` and last `tail` lines within `chars` characters, the tail taking a quarter of them; how many
    lines there were; and how many lines of the kept text come before the cut (None when nothing was cut)."""
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if len(lines) <= head + tail and len(whole := "\n".join(lines)) <= chars:
        return whole, len(lines), None
    split = max(1, len(lines) - tail)
    room = chars // 4 if tail else 0
    bottom: list[str] = []
    for line in reversed(lines[split:]):
        if len(line) + 1 > room:
            break
        bottom.insert(0, line)
        room -= len(line) + 1
    room = chars - sum(len(x) + 1 for x in bottom)
    top: list[str] = []
    for line in lines[:min(split, head)]:
        if len(line) + 1 > room:
            if not top:
                top.append(line[:max(room - 1, 0)])
            break
        top.append(line)
        room -= len(line) + 1
    return "\n".join(top + bottom), len(lines), len(top)


def _volatile(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _VOLATILE.finditer(text):
        if spans and m.start() <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(m.end(), spans[-1][1]))
        else:
            spans.append(m.span())
    return spans


def clean(raw: bytes, *, ms: int, exit_code: int | None, shot: str | None = None,
          file: FileHead | None = None) -> Capture:
    """A step's output as stored: UTF-8 with replacement, no ANSI or control characters but newline and tab, the
    journey folder and /work as ./ and /tmp as ~, secrets scrubbed, head and tail kept, volatile spans marked."""
    text, scrubbed = _tidy(raw)
    kept, lines, cut_after = _cut(text, head=_LINES, tail=_TAIL_LINES, chars=_CHARS)
    return Capture(text=kept, lines=lines, cut_after=cut_after, exit=exit_code, ms=ms, shot=shot, file=file,
                   volatile=_volatile(kept), scrubbed=scrubbed)


def file_head(path: str, head: bytes, size: int) -> tuple[FileHead, list[dict]]:
    """The head of a step's `show` file, cleaned like its output to 40 lines and 3000 characters, and the secrets
    removed from it. A file that is not text is only its size."""
    if _binary(head):
        return FileHead(path=path, text="", lines=0, volatile=[], binary=True, size=size), []
    text, scrubbed = _tidy(head)
    kept, lines, _ = _cut(text, head=_FILE_LINES, tail=0, chars=_FILE_CHARS)
    return FileHead(path=path, text=kept, lines=lines, volatile=_volatile(kept), binary=False, size=size), scrubbed


def _binary(head: bytes) -> bool:
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start < len(head) - 3  # a character the head cut in two is still text
    return False


def matches(expect: str, exit_code: int | None) -> bool:
    """Whether a step ended as its binding expects: `ok` exits 0, `error` exits otherwise."""
    return exit_code is not None and (exit_code != 0 if expect == "error" else exit_code == 0)


_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY( BLOCK)?-----")
_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----")


def _output(frame: dict) -> bytes:
    """What a step printed as the container sent it: all of it, or its head and its tail from the tail's first whole
    line. A private key whose BEGIN line fell in the part not sent is dropped with it, so no part of one is kept."""
    if not frame["tail"]:
        return frame["out"]
    tail = frame["tail"].split(b"\n", 1)[-1]
    text = tail.decode("utf-8", "replace")
    if (end := _KEY_END.search(text)) and not _KEY_BEGIN.search(text, 0, end.start()):
        tail = text[end.end():].encode()
    return frame["out"] + b"\n" + tail


def _nonce() -> str:
    return secrets.token_hex(16)


def _plan(binding: dict | None, ids: list[str]) -> dict:
    """What of the binding runs: its setup lines that are one shell line, and its entries for the spec's steps that
    `entry_problems` finds nothing in."""
    if binding is None:
        return {"setup": [], "steps": {}}
    return {"setup": [x for x in binding["setup"] if not _line_problem(x)],
            "steps": {sid: e for sid, e in binding["steps"].items() if sid in ids and not entry_problems(sid, e)}}


def _seeds(export: Path, entry: dict) -> list[Seed]:
    """The sample inputs a step copies, read on the host from the export as committed, for the owner to copy."""
    from justshipit.engineering import worker  # the worker imports this module
    out = []
    for dest, src in (entry.get("seed") or {}).items():
        try:
            path = worker._host_path(export, src)
            if not stat.S_ISREG(os.lstat(path).st_mode):
                continue
            with open(path, "rb") as f:
                out.append(Seed(path=dest, text=_tidy(f.read(SEED_BYTES))[0]))
        except (OSError, SaidError):
            continue
    return out


def _png(path: Path) -> bytes | None:
    """A capture the container wrote, when it is a plain PNG file of at most 10 MB: the branch's code ran beside the
    browser and could have put anything there."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as f:
            data = f.read(_MAX_SHOT_BYTES + 1)
    except OSError:
        return None
    return data if len(data) <= _MAX_SHOT_BYTES and data.startswith(_PNG) else None


def _keep_shots(shots: Path, got: dict[str, dict], job_id: int, head: str) -> set[str]:
    """Copy the captures the frames name to the job's folder for this head, and say which steps have one."""
    keep = try_it.walk_dir(job_id, head)
    shutil.rmtree(keep, ignore_errors=True)  # an earlier walk of this head is not this one
    kept = set()
    for sid, frame in got.items():
        if frame["kind"] == "open" and frame["shot"] and (png := _png(shots / f"{sid}.png")) is not None:
            keep.mkdir(parents=True, exist_ok=True)
            (keep / f"{sid}.png").write_bytes(png)
            kept.add(sid)
    return kept


def _signals(frame: dict, raw: bytes) -> list[str]:
    """What the server saw of the sandbox during a step: the memory cap, by the container's own count of processes it
    killed (an exit 137 alone is also what `timeout -k` and `kill -9` give), and each host the egress proxy refused
    in the step's time that the step's own output names (other containers' refusals share the log)."""
    said = raw.decode("utf-8", "replace").lower()
    refused = docker.refused_hosts(frame["start"] - 1000, frame["end"] + 1000)
    return [*(["oom"] if frame.get("oom") else []),
            *(f"egress:{host}" for host in sorted(refused) if host.lower() in said)]


def _ran(sid: str, entry: dict | None, *, capture: Capture | None = None, not_run: NotRun | None = None,
         signals: list[str] | None = None, seed: list[Seed] | None = None) -> Ran:
    entry = entry or {}
    kind: Kind = "open" if entry.get("open") else "run"
    # The branch's own line, stored and shown to the owner and the product agent: a key written into it goes too.
    action = secrets_scan.scrub(entry.get("open") or entry.get("run") or "", lambda k: f"[secret:{k}]")[0]
    return Ran(id=sid, kind=kind, action=action,
               expect="error" if entry.get("expect") == "error" else "ok", capture=capture,
               not_run=None if capture is not None else not_run, signals=signals or [], seed=seed or [])


def _walked(plan: dict, doc: dict, got: dict[str, dict], seeds: dict[str, list[Seed]], *, lint_lines: list[str],
            down: NotRun | None = None, shots: set[str] | frozenset[str] = frozenset()) -> Walk:
    """Every setup line and journey step as the frames say it ran, or why it did not: `down` for every bound step
    when nothing could run at all; after a setup line that failed, setup_failed; after a step that ended otherwise
    than expected in its journey, earlier_failed; a step with no frame otherwise ran out of time."""
    stopped: NotRun | None = down
    setup = []
    for i, line in enumerate(plan["setup"], 1):
        ran = _captured(f"setup.{i}", {"run": line}, got, stopped or "out_of_time", [], shots)
        setup.append(ran)
        if stopped is None and (ran["capture"] is None or ran["capture"]["exit"] != 0):
            stopped = "out_of_time" if ran["capture"] is None else "setup_failed"
    steps: dict[str, Ran] = {}
    for journey in doc.get("journeys") or []:
        failed = False
        for step in journey.get("steps") or []:
            sid = step["id"]
            if (entry := plan["steps"].get(sid)) is None:
                steps[sid] = _ran(sid, None, not_run="unbound")
                continue
            ran = _captured(sid, entry, got, stopped or ("earlier_failed" if failed else "out_of_time"),
                            seeds.get(sid) or [], shots)
            steps[sid] = ran
            if ran["capture"] is not None:
                failed = failed or (ran["kind"] == "run" and not matches(ran["expect"], ran["capture"]["exit"]))
            elif ran["not_run"] == "unreachable":
                failed = True
    ms = sum(r["capture"]["ms"] for r in steps.values() if r["capture"])
    return Walk(setup=setup, steps=steps, setup_ms=sum(r["capture"]["ms"] for r in setup if r["capture"]), ms=ms,
                lint=lint_lines)


def _captured(sid: str, entry: dict, got: dict[str, dict], otherwise: NotRun, seed: list[Seed],
              shots: set[str] | frozenset[str]) -> Ran:
    """One bound step from its frame: a run step's output cleaned, an open step's capture when it was kept, and
    `otherwise` when the container sent no frame for it."""
    frame = got.get(sid)
    if frame is None:
        return _ran(sid, entry, not_run=otherwise, seed=seed)
    if frame["kind"] == "open":
        if sid not in shots:
            return _ran(sid, entry, not_run="unreachable", seed=seed)
        return _ran(sid, entry, capture=clean(b"", ms=frame["ms"], exit_code=None, shot=f"{sid}.png"), seed=seed)
    raw = _output(frame)
    head, scrubbed = (file_head(entry.get("show") or "", frame["show_head"], frame["show_size"])
                      if frame["show"] == "ok" else (None, []))
    capture = clean(raw, ms=frame["ms"], exit_code=frame["exit"], file=head)
    if frame["tail"]:  # cut in the container: `wc -l` counted every line break of the whole
        capture["lines"] = max(capture["lines"], frame["lines"] if raw.endswith(b"\n") else frame["lines"] + 1)
    if scrubbed:
        counts: dict[str, int] = {}
        for x in [*capture["scrubbed"], *scrubbed]:
            counts[x["kind"]] = counts.get(x["kind"], 0) + x["n"]
        capture["scrubbed"] = [{"kind": k, "n": n} for k, n in counts.items()]
    return _ran(sid, entry, capture=capture, signals=_signals(frame, raw), seed=seed)


def walk(repo: Path, ref: str, doc: dict, *, job_id: int, head: str, language: str | None) -> Walk:
    """Run the binding for the spec's journeys `doc` on a fresh export of `ref` (the delivered `head`), in the
    sandbox. `language` is the browser's for `open` steps."""
    ids = list(journeys.steps(doc))
    if not ids:
        return Walk(setup=[], steps={}, setup_ms=0, ms=0, lint=[])
    from justshipit.engineering import worker  # the worker imports this module
    with tempfile.TemporaryDirectory(prefix="ship-walk-", ignore_cleanup_errors=True) as tmp:
        export, shots = Path(tmp, "export"), Path(tmp, "shots")
        export.mkdir()
        shots.mkdir()
        worker._export_branch(repo, ref, export)
        worker.drop_links(export)
        worker._clear_ship(export)
        binding, problems = read_binding(export)
        found = problems + lint(binding, doc)
        plan = _plan(binding, ids)
        seeds = {sid: _seeds(export, entry) for sid, entry in plan["steps"].items()}
        if not plan["steps"]:
            return _walked(plan, doc, {}, seeds, lint_lines=found, down="unbound")
        if not docker.docker_ok():
            return _walked(plan, doc, {}, seeds, lint_lines=found, down="no_docker")
        nonce, cap = _nonce(), int(company_config()["engineering"]["test_timeout_s"])
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"
        try:
            res = docker.run(docker.ContainerSpec(image=company_config()["engineering"]["sandbox"]["image"],
                                                  command=["sh", "-s"],
                                                  mounts=[docker.Mount(export, "/work", read_only=True),
                                                          docker.Mount(shots, _SHOTS)],
                                                  network="sandbox", memory="4g", cpus="2", user=user,
                                                  env={"HOME": "/tmp"}, name_prefix="ship-test-walk"),
                             stdin=runner(plan, nonce, language=language, budget_s=cap - _MARGIN_S).encode(),
                             timeout_s=cap)
        except docker.SandboxUnavailable:
            return _walked(plan, doc, {}, seeds, lint_lines=found, down="no_docker")
        kinds = {**{f"setup.{i}": "run" for i in range(1, len(plan["setup"]) + 1)},
                 **{sid: "open" if e.get("open") else "run" for sid, e in plan["steps"].items()}}
        got = {f["id"]: f for f in frames(res.stdout, nonce) if kinds.get(f["id"]) == f["kind"]}
        if not got and not res.timed_out and not started(res.stdout, nonce):  # the container never ran the script
            return _walked(plan, doc, {}, seeds, lint_lines=found, down="no_docker")
        return _walked(plan, doc, got, seeds, lint_lines=found, shots=_keep_shots(shots, got, job_id, head))


# ---- the verdict (phase 4)

# Exits that are never the product answering as a step expects: a step stopped by `timeout`, a shell that found no
# such command, and a process killed under the memory cap.
TIMED_OUT, NOT_FOUND, KILLED = 124, 127, 137
_NOT_AN_ANSWER = (TIMED_OUT, NOT_FOUND, KILLED)
# What a fix round can repair: a journey with such a step sends the build back (gate.failures). A login the sandbox
# never gets, or a step the spec never promised, is no fix round's to repair, so it leaves the journey's criterion met.
_FIXABLE = ("unbound", "setup_failed", "out_of_time", "unreachable")


def empty_walk() -> Walk:
    """The walk of a spec with no journeys: nothing was bound and nothing ran."""
    return {"setup": [], "steps": {}, "setup_ms": 0, "ms": 0, "lint": []}


def held(walk: Walk, doc: dict, stories: list[dict], verdicts: list[StepVerdictOut], needs: list[dict], *,
         head: str, spec_commit: str, ran_at: str) -> WalkthroughRun:
    """The record, with the server's word on each step over the product agent's: no capture is not_run, passed
    needs a capture, an exit that matches `expect` and the agent's passed, environment holds only on a server signal
    or a required need, and a captured step with no verdict differs, not judged. Setup lines are judged by their exit
    alone: the agent is asked about journey steps."""
    said: dict[str, StepVerdictOut] = {}
    for v in verdicts:
        said.setdefault(v.step_id, v)
    required = {str(n.get("name") or "").strip().casefold(): str(n.get("name") or "").strip()
                for n in needs if n.get("required") and str(n.get("name") or "").strip()}
    journeys = []
    for j in doc.get("journeys") or []:
        steps = [_step(walk["steps"].get(st["id"]) or _unbound(st["id"]), st, said.get(st["id"]), required)
                 for st in j.get("steps") or []]
        journeys.append({**{k: v for k, v in j.items() if k != "steps"}, "steps": steps})
    return {"v": 1, "head": head, "spec_commit": spec_commit, "ran_at": ran_at, "ms": walk["ms"],
            "setup_ms": walk["setup_ms"], "about": copy.deepcopy(doc.get("about")),
            "setup": [_setup_step(r) for r in walk["setup"]], "journeys": journeys,
            "stories": copy.deepcopy(stories)}


def _unbound(step_id: str) -> Ran:
    return {"id": step_id, "kind": "run", "action": "", "expect": "ok", "capture": None, "not_run": "unbound",
            "signals": [], "seed": []}


def _answered(r: Ran) -> bool:
    """Whether the capture is the product answering as the step expects: an `open` step's shot, or an exit that
    matches `expect`."""
    capture = r["capture"]
    if r["kind"] == "open":
        return capture["shot"] is not None
    if r["expect"] == "error":
        return capture["exit"] is not None and capture["exit"] != 0 and capture["exit"] not in _NOT_AN_ANSWER
    return capture["exit"] == 0


def captured(r: Ran) -> bool:
    """Whether the step ran and left what it printed, or, for an `open` step, its shot."""
    return r["capture"] is not None and (r["kind"] != "open" or r["capture"]["shot"] is not None)


def _base(r: Ran, st: dict) -> StepRun:
    return {"id": r["id"], "do": str(st.get("do") or ""), "why": str(st.get("why") or ""),
            "see": str(st.get("see") or ""), "kind": r["kind"], "action": r["action"], "expect": r["expect"],
            "seed": r["seed"], "capture": r["capture"], "state": "not_run", "not_run": None, "need": None,
            "fault": None, "fault_by": None, "judged": True, "looks_like": "", "hint": ""}


def _setup_step(r: Ran) -> StepRun:
    base = _base(r, {})
    if not captured(r):
        return {**base, "not_run": r["not_run"] or "unbound"}
    if _answered(r):
        return {**base, "state": "passed"}
    return {**base, "state": "differs", "fault": "product", "fault_by": "server"}


def _step(r: Ran, st: dict, v: StepVerdictOut | None, required: dict[str, str]) -> StepRun:
    base = _base(r, st)
    if not captured(r):  # whatever the agent said of it: there is nothing it could have read
        return {**base, "not_run": r["not_run"] or ("unreachable" if r["kind"] == "open" else "unbound")}
    if v is None:  # the agent said nothing; an exit against the step is still the server's own finding
        server = {} if _answered(r) else {"fault": "product", "fault_by": "server"}
        return {**base, "state": "differs", "judged": False, **server}
    said = {**base, "looks_like": v.looks_like, "hint": v.hint}
    if v.verdict == "passed":
        if _answered(r):
            return {**said, "state": "passed"}
        return {**said, "state": "differs", "fault": "product", "fault_by": "server"}
    if v.fault == "scenario":
        return {**said, "state": "differs", "fault": "scenario", "fault_by": "agent"}
    if v.fault == "environment":
        need = _need(r, v, required)
        if need:
            return {**said, "need": need, "not_run": "needs"}
        return {**said, "state": "differs", "fault": "product", "fault_by": "server"}
    if v.fault == "product":
        return {**said, "state": "differs", "fault": "product", "fault_by": "agent"}
    return {**said, "state": "differs", "fault": "product", "fault_by": "server"}


def _need(r: Ran, v: StepVerdictOut, required: dict[str, str]) -> str | None:
    """What the sandbox lacked, as the server can stand behind it: a required need of the product's that the agent
    named, or what the server itself saw (the host the egress proxy refused, or the memory cap). A timeout and a
    command not found are the build's unless the server saw the sandbox refuse something."""
    named = required.get(v.need.strip().casefold())
    seen = next(("memory" if x == "oom" else x.removeprefix("egress:") for x in r["signals"]
                 if x == "oom" or (x.startswith("egress:") and x.removeprefix("egress:"))), None)
    if seen is None and r["capture"]["exit"] in (TIMED_OUT, NOT_FOUND):
        return None
    return named or seen


def holds_up(journey: dict) -> bool:
    """Whether a journey's run meets its criterion: no step a fix round can repair went wrong, that is no step that
    differs as the product's fault and none unbound, stopped by a failed setup, or out of time."""
    return not any((st["state"] == "differs" and st["fault"] == "product") or st["not_run"] in _FIXABLE
                   for st in journey["steps"])


def p1(run: WalkthroughRun | None) -> list[dict]:
    """The must-have journeys of a run, whose criteria can fail the build's check."""
    return [j for j in (run or {}).get("journeys") or [] if j.get("priority") == "P1"]


def store(result: dict, run: WalkthroughRun) -> list[WalkthroughRun]:
    """The new `result["walkthroughs"]`: a list, newest last, one run per head. `run` replaces its head's earlier run
    and only the last KEPT_HEADS stay. A list, because a JSONB object keeps no key order."""
    kept = [x for x in result.get("walkthroughs") or [] if isinstance(x, dict) and x.get("head") != run["head"]]
    return [*kept, run][-KEPT_HEADS:]
