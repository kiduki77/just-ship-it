"""Host git on content an AI wrote: an engineer's branch, or a registry document that carries their test log."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from justshipit.runtime import children

log = logging.getLogger(__name__)

# Attributes are read from this tree, the empty one, so nothing a branch's .gitattributes names (a filter, a diff or
# merge driver, an eol rule) selects anything. SHA-1 only: a repo in SHA-256 format makes every call die.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


_attr_source: dict[tuple[str, int], bool] = {}


def knows_attr_source() -> bool:
    """Whether the host git has --attr-source (2.41 and later). Kept per git binary."""
    binary = shutil.which("git")
    if binary is None:
        return True  # the command itself names the missing git
    key = (binary, os.stat(binary).st_mtime_ns)
    if key not in _attr_source:
        version = subprocess.run(["git", "--version"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", check=False, timeout=60).stdout
        found = re.search(r"(\d+)\.(\d+)", version)
        _attr_source[key] = not found or (int(found[1]), int(found[2])) >= (2, 41)
        if not _attr_source[key]:
            log.warning("git %s has no --attr-source (before 2.41). Registry documents are saved without it (the "
                        "registry takes no git config file path); container engineering jobs are refused. Update git "
                        "to 2.45.1 or later.", version.strip())
    return _attr_source[key]


# The owner's global and system config is never read (run), so these guard against the repository's own
# .git/config, which the owner may edit by hand: every key there that runs a program or changes files in a
# work tree during a host command. Git for Windows opens /dev/null as nul (compat/mingw.c).
_REPO_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.attributesFile=/dev/null",
    "filter.lfs.process=", "filter.lfs.smudge=", "filter.lfs.clean=", "filter.lfs.required=false",
    # A fast-forward in the owner's checkout stashed their uncommitted edit and wrote it back as a conflict.
    "merge.autoStash=false", "rebase.autoStash=false",
    # The driver for every file no attribute names, and attributes are off, so it was the driver for every file.
    "merge.default=text",
    # A fast-forward would otherwise clone whatever the branch's .gitmodules names.
    "submodule.recurse=false",
)


# Config set through the environment: what `git -c` hands its children, and the COUNT/KEY_n/VALUE_n pairs.
_ENV_CONFIG = re.compile(r"GIT_CONFIG_(PARAMETERS|COUNT|KEY_\d+|VALUE_\d+)")


# Nothing may wait on a person: no terminal or credential-manager prompt and no console stdin. A command still
# stuck after TIMEOUT_S (a push whose remote stopped answering) is ended with every helper it started, and fails.
_UNATTENDED = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
TIMEOUT_S = 600


def run(cwd: Path, *args: str, env: dict[str, str] | None = None, attr_source: bool = True,
        owner_config: bool = False, timeout_s: float = TIMEOUT_S) -> subprocess.CompletedProcess[str]:
    """Host git reads none of the owner's global or system config (GIT_CONFIG_GLOBAL, git 2.32 and later, and
    GIT_CONFIG_NOSYSTEM), nor config the worker's environment carries (_ENV_CONFIG, from a worker started under
    a git alias or the owner's shell): no key the owner set for their own work (a merge driver, autoStash, an
    external diff, a template directory) applies to content an AI wrote. Every way git runs a command that the content or the
    repository's own config can choose is off too (_REPO_CONFIG): hooks, fsmonitor, attributes (GIT_ATTR_SOURCE
    for child processes, --attr-source so a git older than 2.41 refuses instead of silently reading the tree,
    GIT_ATTR_NOSYSTEM for the system attributes file), the git-lfs filter and its smudge (a branch .lfsconfig
    would pick the URL), and lazy fetch from a promisor remote planted in a clone's config (CVE-2024-32465,
    CVE-2024-32004). A repo's info/attributes cannot be turned off.
    attr_source=False leaves out only --attr-source, for a tree no one but the host names files in.
    owner_config=True keeps the owner's global and system config, for the one command that must reach a remote
    with their credentials: a push moves refs and never checks content out, and credential helpers, proxies
    and url rewrites live in that config."""
    argv = ["git", *([f"--attr-source={EMPTY_TREE}"] if attr_source else []),
            *(a for key in _REPO_CONFIG for a in ("-c", key)), *args]
    isolation = {} if owner_config else {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    inherited = {k: v for k, v in os.environ.items() if owner_config or not _ENV_CONFIG.fullmatch(k)}
    # Content and file names are any bytes (a CP949 file, a Latin-1 name under core.quotePath=false).
    p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace",
                         env={**inherited, **isolation, "GIT_NO_LAZY_FETCH": "1", "GIT_ATTR_SOURCE": EMPTY_TREE,
                              "GIT_ATTR_NOSYSTEM": "1", "GIT_LFS_SKIP_SMUDGE": "1", **_UNATTENDED, **(env or {})},
                         **children.GROUP)
    try:
        out, err = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        children.stop_tree(p)
        p.wait()
        for f in (p.stdout, p.stderr):
            f.close()
        return subprocess.CompletedProcess(argv, -1, "", f"git {args[0] if args else ''}: no answer in "
                                                          f"{timeout_s:g}s, stopped (a remote or a prompt waiting?)")
    return subprocess.CompletedProcess(argv, p.returncode, out, err)
