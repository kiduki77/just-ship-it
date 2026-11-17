#!/usr/bin/env python3
"""Rename the model role id `ceo` to `project_manager` across backend, config, prompts, web/src and tests.

Standard library only.  Usage:

    python rename_ceo.py ROOT                 # dry run (default): prints every changed line, writes a report
    python rename_ceo.py ROOT --apply         # rewrites files and renames files
    python rename_ceo.py ROOT --apply --git-mv   # renames files with `git mv` (ROOT must be a git work tree)
    python rename_ceo.py ROOT --rename-stored --rename-catalog-keys   # also the stored compounds and catalog keys (see doc)
    python rename_ceo.py ROOT --skip test-name --skip ident-var   # turn categories off (see CATEGORIES)
    python rename_ceo.py ROOT --out DIR       # where changes.txt, edits.json, skipped.json, codemod_report.md go

What it renames (the id, and names that embed it):
  * the quoted literals "ceo" 'ceo' `ceo`, the YAML key `ceo:`, the path `ceo.md` and `agents/ceo`, `by=ceo`,
    `ceo/purpose`, `owner | ceo`, `(owner or ceo)` and the stored string "ceo (결정 권한)";
  * identifiers: `ceo`, `_ceo`, `by_ceo`, `cycle_ceo`, `ceoWeek`, `ScriptedCeo`, `CeoPlanOut`, `CEO_DECIDABLE`, test names;
  * the catalog key `label.agent.ceo` (the console builds it from the agent id) and the module path `...cycle_ceo...`;
  * file names that contain the word (prompts/agents/ceo.md, services/cycle_ceo.py, tests/test_ceo_*.py).

What it never touches:
  * the display word "CEO" in sentences (catalog values, prompt prose, gate.py rule descriptions), and lowercase prose;
  * alembic revision files (backend/justshipit/db/migrations/versions) and their history-pinning test lines;
  * stored or wire compounds, kept on purpose (PROTECTED_SUBSTRINGS): ceo_plan, ceo_review(s), ceo_decided, ceo_text;
  * catalog keys other than label.agent.ceo (a dotted token such as `decide.who.ceo`), CSS class names, the `{ceo}`
    placeholder of decide.inbox.week.
Every skipped lowercase or protected hit is listed in skipped.json so a human can look at it.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import io
import json
import os
import re
import subprocess
import sys
import tokenize

NEW = "project_manager"
# Directories, and files that name a path inside them (the notice cites prompts/agents/<id>.md).
SCOPE = ["backend", "config", "prompts", "web/src", "tests", "THIRD_PARTY_NOTICES.md"]
SKIP_DIRS = {"node_modules", "__pycache__", "dist", ".venv", ".git", "worktrees", ".pytest_cache"}
HISTORY_DIRS = ["backend/justshipit/db/migrations/versions"]
# Lines that must keep the old string: what an old migration writes or reads, and the one name for the old id that
# readers of rows written before the rename accept. (file, line regex)
HISTORY_PINS = [
    ("tests/test_roadmap_model.py", r'\("DONE", "owner", "STOPPED", "ceo"\)'),
    ("tests/test_work_path_migration.py", r'"agent": "ceo", "type": "QUESTION"'),
    ("backend/justshipit/agents/roles.py", r'^FORMER_PROJECT_MANAGER = "ceo"$'),
    ("web/src/labels.ts", r'^const FORMER_PROJECT_MANAGER = "ceo";$'),
]
# Kept on purpose: stored values, wire keys, graph node names and the DB column. A hit is kept when the compound
# starts at it, so `test_the_ceo_reviews_...` keeps naming the `ceo_reviews` key.
PROTECTED_SUBSTRINGS = ["ceo_plan", "ceo_review", "ceo_decided", "ceo_text"]   # ceo_text is a DB column: never renamed
RENAME_CATALOG_KEYS = False   # --rename-catalog-keys: also rename dotted catalog keys such as decide.who.ceo
# Spans whose `ceo` is a placeholder name shared with a catalog sentence ({ceo|을}); renaming one side breaks it.
PROTECTED_SPANS = [r"(?m)^[ \t]*(?P<x>ceo)(?=: <Link)", r"(?<=decide\.inbox\.week:)(?P<x>ceo)", r"\{(?P<x>ceo)[|}]"]
DYNAMIC_KEYS = {"label.agent.ceo"}      # the console builds label.agent.<agent id>
CATEGORIES = [
    "literal", "literal-backtick", "yaml-key", "path", "responded-by", "id-in-text", "ident-var", "ident-snake",
    "ident-camel", "ident-const", "ident-in-text", "dynamic-key", "module-path", "test-name", "file-rename", "catalog-key",
]
TEXT_EXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".yaml", ".yml", ".md", ".json", ".txt", ".toml", ".html",
            ".sh", ".mako", ".cfg", ".ini", ".csv", ".sql"}

RX_ANY = re.compile(r"(?<![A-Za-z0-9])ceos?(?![a-z])|(?<![A-Z])Ceo(?![a-z])|(?<![A-Za-z0-9])CEO(?![A-Za-z])")
RX_WORD = re.compile(r"[A-Za-z0-9_]+")
# Patterns that mark a lowercase `ceo` in prose, a comment or a string as the id. Each has one group named `x`.
ID_PATTERNS = [
    ("responded-by", re.compile(r"(?P<x>ceo)(?= \(결정 권한\))")),
    ("path", re.compile(r"(?P<x>ceo)(?=\.md\b)")),
    ("path", re.compile(r"(?<=agents/)(?P<x>ceo)(?![A-Za-z0-9_])")),
    ("id-in-text", re.compile(r"(?<=by=)(?P<x>ceo)(?![A-Za-z0-9_])")),
    ("id-in-text", re.compile(r"(?<![A-Za-z0-9_./-])(?P<x>ceo)(?=/[a-z_]+)")),
    ("id-in-text", re.compile(r"(?<=\| )(?P<x>ceo)(?= \|)")),
    ("id-in-text", re.compile(r"(?<=owner \| )(?P<x>ceo)(?![A-Za-z0-9_])")),
    ("id-in-text", re.compile(r"(?<=owner or )(?P<x>ceo)(?![A-Za-z0-9_])")),
    ("id-in-text", re.compile(r"(?<=owner 또는 )(?P<x>ceo)(?![A-Za-z0-9_])")),
    ("id-in-text", re.compile(r"(?<=\()(?P<x>ceo)(?=, marketing)")),
    ("id-in-text", re.compile(r"(?P<x>ceo)(?=: the CEO)")),
]
RX_YAML_KEY = re.compile(r"(?m)^[ \t]*(?P<x>ceo)(?=:)")

# Special cases a human must review.  (id, path regex, line regex, why).  The first match per edit is used.
SPECIAL = [
    ("door-contract", r"contracts/door\.py", r"ceo",
     "stored enum AND the JSON schema the door-rules model fills in: decider: Literal[\"owner\",\"ceo\"]; "
     "prompts/door_rules.md tells the model the same value"),
    ("policy-deciders", r"services/policy\.py", r"DECIDERS|decider|CEO_DECIDABLE|ceo",
     "DECIDERS tuple and decider comparisons: the stored value of every door rule"),
    ("roadmap-literal", r"services/roadmap\.py", r"Literal|decider|origin|actor|rules|ceo",
     "Literal[\"owner\",\"ceo\"] on a stored brief door-policy rule, derived-rule withdrawal, bet origin"),
    ("profile-writer", r"services/profile/", r"ceo",
     "profile Writer / written_by / provenance rules: stored on every customer-profile row"),
    ("bet-origin", r"(services/(cycle|bet_retro|ideas|roadmap)\.py|business/(Bets\.tsx|types\.ts)|db/models\.py)",
     r"origin|ceo",
     "bets.origin (and brief author) stored value; the web compares it to decide whether to show the proposal tag"),
    ("requests-by", r"(services/requests\.py|api/app\.py|api/rows\.py|pages/Approvals\.tsx|components/decision\.tsx)",
     r"by_ceo|by=|by\"|by=\"|\bby\b|from_agent|CEO_DECIDED|decided_by|ceo",
     "requests: by_ceo wire field, response.by, from_agent, responded_by, withdrawn_ceo path"),
    ("cycle-by", r"(services/cycle\.py|contracts/cycle\.py|cycle/types\.ts)", r"by|summary_by|ceo",
     "cycle retro/summary `by` is stored JSON read back by the web (Literal[\"ceo\",\"server\"])"),
    ("chat-tags", r"services/chat_tags\.py|services/chat_reply\.py|contracts/chat\.py", r"ceo",
     "chat_tags._ceo, allowed() escalate/handoff, escalate target; the model also writes `to`"),
    ("gate-rules", r"services/gate\.py", r"ceo", "gate rules carry the owning role id as the last argument"),
    ("assignable-owners", r"(services/tasks\.py|orchestration/company_graph\.py)", r"!= \"ceo\"|k != |ceo\"",
     "a task owner may be any role except the coordinator; these inequalities exclude the id"),
    ("model-visible", r"(^prompts/|contracts/(chat|door)\.py|i18n/messages/door\.py)", r"ceo",
     "text the model reads or the schema description it is given: the model must write the new id"),
    ("web-who", r"pages/Approvals\.tsx", r"ceo",
     "Who type, WHO record key, ?by= query parameter (old bookmarks / URLs carry by=ceo)"),
    ("responder-constant", r"(i18n/match/responders\.py|services/requests\.py|label\.ts)", r"CEO_DECIDED|결정 권한|ceo \(",
     "the stored responded_by string \"ceo (결정 권한)\" and the catalog key label.agent.ceo (결정 권한) built from it"),
    ("activity-actor", r"services/(activity|bet_retro|cycle_ceo|ideas|roadmap)\.py", r"actor|as_actor|\"ceo\"",
     "activity_events.actor stored value; console label.agent.<actor> resolves it"),
    ("agent-id-table", r"(i18n/glossary\.py|i18n/areas/label\.ts|config/agents\.yaml)", r"ceo",
     "tables keyed by agent id: config agents, glossary ROLES, label.agent.<id>; tests/test_agent_names.py compares them"),
    ("history-pin", r"^tests/test_roadmap_model\.py", r"ceo",
     "a test that pins what an old migration writes is on the HISTORY_PINS skip list; the other lines follow live code"),
]
RX_TEST_PIN = re.compile(r"^\s*(assert\b|assert_|expect\()|==\s*\"ceo\"|\"ceo\"\s*\)\s*==|\bin \(.*\"ceo\"")


# ------------------------------------------------------------------ segmentation
def ts_segments(t: str) -> list[tuple[str, int, int]]:
    """Split TS/TSX/JS into code, string and comment segments.  JSX text counts as code; a stray apostrophe in it
    cannot start a string because a quote string must close on its own line."""
    segs: list[tuple[str, int, int]] = []
    n = len(t)

    def emit(kind: str, a: int, b: int) -> None:
        if b > a:
            segs.append((kind, a, b))

    def lex(i: int, in_expr: bool) -> int:
        cs = i
        depth = 0
        prev = ""
        while i < n:
            c = t[i]
            if c == "/" and t[i + 1:i + 2] == "/":
                emit("code", cs, i)
                j = t.find("\n", i)
                j = n if j < 0 else j
                emit("comment", i, j)
                i = cs = j
                continue
            if c == "/" and t[i + 1:i + 2] == "*":
                emit("code", cs, i)
                j = t.find("*/", i + 2)
                j = n if j < 0 else j + 2
                emit("comment", i, j)
                i = cs = j
                continue
            if c in "\"'":
                j = i + 1
                while j < n and t[j] != c and t[j] != "\n":
                    j += 2 if t[j] == "\\" else 1
                if j < n and t[j] == c:
                    emit("code", cs, i)
                    emit("string", i, j + 1)
                    i = cs = j + 1
                    prev = '"'
                    continue
                i += 1
                continue
            if c == "`":
                emit("code", cs, i)
                ts = i
                j = i + 1
                while j < n:
                    if t[j] == "\\":
                        j += 2
                        continue
                    if t[j] == "`":
                        break
                    if t[j] == "$" and t[j + 1:j + 2] == "{":
                        emit("string", ts, j + 2)
                        j = lex(j + 2, True)
                        ts = j
                        continue
                    j += 1
                emit("string", ts, min(j + 1, n))
                i = cs = min(j + 1, n)
                prev = '"'
                continue
            if c == "/" and (prev in "(,=:[!&|?{};" or prev == "") and t[i + 1:i + 2] not in ("", " ", "="):
                j = i + 1
                incls = False
                while j < n and t[j] != "\n":
                    if t[j] == "\\":
                        j += 2
                        continue
                    if t[j] == "[":
                        incls = True
                    elif t[j] == "]":
                        incls = False
                    elif t[j] == "/" and not incls:
                        break
                    j += 1
                if j < n and t[j] == "/":
                    emit("code", cs, i)
                    emit("regex", i, j + 1)
                    i = cs = j + 1
                    prev = ")"
                    continue
            if in_expr:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        emit("code", cs, i)
                        return i
                    depth -= 1
            if not c.isspace():
                prev = c
            i += 1
        emit("code", cs, n)
        return n

    lex(0, False)
    return segs


def py_segments(text: str) -> tuple[list[tuple[str, int, int]], list[tuple[int, int, str]]]:
    """(string/comment segments, NAME tokens (start, end, previous token string)) for Python source."""
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))

    def off(pos: tuple[int, int]) -> int:
        return starts[pos[0] - 1] + pos[1]

    segs: list[tuple[str, int, int]] = []
    names: list[tuple[int, int, str]] = []
    fdepth = 0
    fstart = 0
    prev = ""
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        tt, s = tok.type, tok.string
        a, b = off(tok.start), off(tok.end)
        if tt == tokenize.FSTRING_START:
            if fdepth == 0:
                fstart = a
            fdepth += 1
        elif tt == tokenize.FSTRING_END:
            fdepth -= 1
            if fdepth == 0:
                segs.append(("string", fstart, b))
        elif tt == tokenize.STRING and fdepth == 0:
            segs.append(("string", a, b))
        elif tt == tokenize.COMMENT:
            segs.append(("comment", a, b))
        elif tt == tokenize.NAME:
            names.append((a, b, prev))
        if tt not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT):
            prev = s
    return segs, names


# ------------------------------------------------------------------ edits
def rename_match(s: str, word: str, nxt: str) -> str:
    """The replacement for one regex hit: `ceo` -> project_manager (camel before an uppercase letter), Ceo, CEO."""
    if s == "CEO":
        return "PROJECT_MANAGER"
    if s == "Ceo":
        return "ProjectManager"
    plural = "s" if s.endswith("s") else ""
    if not plural and nxt.isupper() and nxt.isalpha() and word.count("_") == 0:
        return "projectManager"
    return NEW + plural


class FileScan:
    def __init__(self, rel: str, text: str, skip_cats: set[str]):
        self.rel, self.text, self.skip = rel, text, skip_cats
        self.starts = [0]
        for line in text.splitlines(keepends=True):
            self.starts.append(self.starts[-1] + len(line))
        self.edits: dict[tuple[int, int], dict] = {}
        self.skips: list[dict] = []
        self.ext = os.path.splitext(rel)[1]
        self.protected = [(m.start("x"), m.end("x")) for p in PROTECTED_SPANS for m in re.finditer(p, text)]
        self.pins = [re.compile(p) for f, p in HISTORY_PINS if f == rel]

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)  # 1-based

    def line_text(self, ln: int) -> str:
        return self.text[self.starts[ln - 1]:self.starts[ln]].rstrip("\r\n") if ln < len(self.starts) else ""

    def add(self, a: int, b: int, new: str, cat: str) -> None:
        if cat in self.skip:
            self.skips.append(dict(line=self.line_of(a), cat="disabled:" + cat, ctx=self.text[a:b]))
            return
        ln = self.line_of(a)
        if any(x <= a < y for x, y in self.protected):
            self.skips.append(dict(line=ln, cat="protected-placeholder", ctx=self.line_text(ln).strip()[:140]))
            return
        lt = self.line_text(ln)
        if any(p.search(lt) for p in self.pins):
            self.skips.append(dict(line=ln, cat="history-pin", ctx=lt.strip()[:140]))
            return
        self.edits[(a, b)] = dict(a=a, b=b, new=new, cat=cat, line=ln)

    def skip_note(self, pos: int, cat: str, ctx: str) -> None:
        self.skips.append(dict(line=self.line_of(pos), cat=cat, ctx=ctx))

    # -- one regex hit
    def hit(self, kind: str, m: re.Match, test_name: bool = False, prev_tok: str = "") -> None:
        t = self.text
        a, b = m.start(), m.end()
        s = m.group(0)
        wa, wb = a, b
        while wa > 0 and re.match(r"[A-Za-z0-9_]", t[wa - 1]):
            wa -= 1
        while wb < len(t) and re.match(r"[A-Za-z0-9_]", t[wb]):
            wb += 1
        word = t[wa:wb]
        nxt = t[b:b + 1]
        new = rename_match(s, word, nxt)
        lw = word.lower()
        if any(t.startswith(p, a) for p in PROTECTED_SUBSTRINGS):
            return self.skip_note(a, "protected-stored", word)
        if s == "CEO" and word == "CEO":
            return self.skip_note(a, "display", self._ctx(a))
        if s == "CEO" and "_" not in word:
            return self.skip_note(a, "display", self._ctx(a))
        if kind == "code":
            if s == "CEO":
                return self.add(a, b, new, "ident-const")
            if s == "Ceo" or (s.startswith("ceo") and new == "projectManager"):
                return self.add(a, b, new, "ident-camel")
            if test_name and word.startswith("test_"):
                return self.add(a, b, new, "test-name")
            if word in ("ceo", "ceos"):
                if self.ext in (".ts", ".tsx", ".js", ".mjs") and s == "ceo" and nxt != ":" and t[a - 1:a] != ".":
                    new = "projectManager"   # a local variable; a key or a property keyed by the id stays snake
                return self.add(a, b, new, "ident-var")
            return self.add(a, b, new, "ident-snake")
        # string, comment or text
        dotted = self._dotted(a, b)
        if dotted and "." in dotted and s in ("ceo", "ceos") and not re.search(r"\.(md|py|tsx?|ya?ml|json)$", dotted):
            if dotted in DYNAMIC_KEYS:
                return self.add(a, b, new, "dynamic-key")
            segs = dotted.split(".")
            if segs[0] == "justshipit" or any("ceo" in x and "_" in x for x in segs[:-1]) and segs[0] != "ceo":
                return self.add(a, b, new, "module-path")
            if RENAME_CATALOG_KEYS:
                return self.add(a, b, new, "catalog-key")
            return self.skip_note(a, "catalog-key", dotted)
        if s == "ceo":
            for cat, rx in ID_PATTERNS:
                for mm in rx.finditer(t, max(0, a - 40), min(len(t), b + 40)):
                    if mm.start("x") == a:
                        return self.add(a, b, new, cat)
            if a > 0 and b < len(t) and t[a - 1] == t[b] and t[a - 1] in "\"'`" and word == "ceo":
                return self.add(a, b, new, "literal-backtick" if t[a - 1] == "`" else "literal")
            if self.ext in (".yaml", ".yml") and RX_YAML_KEY.search(t, max(0, a - 80), b + 1) and \
                    any(mm.start("x") == a for mm in RX_YAML_KEY.finditer(t, max(0, a - 80), b + 1)):
                return self.add(a, b, new, "yaml-key")
        if s == "CEO":
            return self.add(a, b, new, "ident-in-text")
        if s == "Ceo" or word != s.rstrip("s") and ("_" in word or new == "projectManager"):
            return self.add(a, b, new, "ident-in-text")
        return self.skip_note(a, "prose", self._ctx(a))

    def _dotted(self, a: int, b: int) -> str:
        t = self.text
        wa, wb = a, b
        while wa > 0 and re.match(r"[A-Za-z0-9_.]", t[wa - 1]):
            wa -= 1
        while wb < len(t) and re.match(r"[A-Za-z0-9_.]", t[wb]):
            wb += 1
        return t[wa:wb].strip(".")

    def _ctx(self, pos: int) -> str:
        ln = self.line_of(pos)
        return self.line_text(ln).strip()[:140]

    def scan_range(self, kind: str, a: int, b: int, exclude: list[tuple[int, int]] | None = None) -> None:
        for m in RX_ANY.finditer(self.text, a, b):
            if exclude and any(x <= m.start() < y for x, y in exclude):
                continue
            self.hit(kind, m)


def scan_file(rel: str, text: str, skip_cats: set[str]) -> FileScan:
    fs = FileScan(rel, text, skip_cats)
    ext = fs.ext
    if ext == ".py":
        segs, names = py_segments(text)
        for a, b, prev in names:
            for m in RX_ANY.finditer(text, a, b):
                fs.hit("code", m, test_name=(prev == "def" and rel.startswith("tests/")), prev_tok=prev)
        name_ranges = [(a, b) for a, b, _ in names]
        for kind, a, b in segs:
            fs.scan_range(kind, a, b, exclude=name_ranges)
    elif ext in (".ts", ".tsx", ".js", ".mjs"):
        for kind, a, b in ts_segments(text):
            if kind == "regex":
                continue
            fs.scan_range(kind, a, b)
    elif ext == ".css":
        for m in RX_ANY.finditer(text):
            fs.skip_note(m.start(), "css-class" if m.group(0) == "ceo" else "display", fs._ctx(m.start()))
    else:
        fs.scan_range("text", 0, len(text))
    return fs


def apply_edits(text: str, edits: list[dict]) -> str:
    out = text
    for e in sorted(edits, key=lambda e: e["a"], reverse=True):
        out = out[:e["a"]] + e["new"] + out[e["b"]:]
    return out


# ------------------------------------------------------------------ driver
def walk(root: str):
    for top in SCOPE:
        base = os.path.join(root, *top.split("/"))
        if os.path.isfile(base):
            yield base, top
        for dp, dns, fns in os.walk(base):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
            for fn in sorted(fns):
                p = os.path.join(dp, fn)
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                yield p, rel


def rename_basename(name: str) -> str:
    return RX_ANY.sub(lambda m: rename_match(m.group(0), name, name[m.end():m.end() + 1]), name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="default: print and report, change nothing")
    g.add_argument("--apply", action="store_true", help="rewrite files and rename files")
    ap.add_argument("--git-mv", action="store_true", help="rename files with git mv")
    ap.add_argument("--skip", action="append", default=[], choices=CATEGORIES, help="turn a category off")
    ap.add_argument("--out", default=None, help="report directory (default ROOT/../rename_ceo_report)")
    ap.add_argument("--rename-stored", action="store_true",
                    help="also rename the stored/wire compounds ceo_plan, ceo_review(s), ceo_decided (not ceo_text)")
    ap.add_argument("--rename-catalog-keys", action="store_true",
                    help="also rename catalog keys that contain ceo (decide.who.ceo ...), in code and in the catalogs")
    ap.add_argument("--quiet", action="store_true", help="do not print changed lines")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    root = os.path.abspath(args.root)
    out_dir = os.path.abspath(args.out or os.path.join(root, "..", "rename_ceo_report"))
    os.makedirs(out_dir, exist_ok=True)
    skip_cats = set(args.skip)
    global RENAME_CATALOG_KEYS
    RENAME_CATALOG_KEYS = args.rename_catalog_keys
    if args.rename_stored:
        PROTECTED_SUBSTRINGS[:] = ["ceo_text"]

    all_edits: list[dict] = []
    all_skips: list[dict] = []
    errors: list[str] = []
    renames: list[tuple[str, str]] = []
    rewrites: dict[str, str] = {}
    originals: dict[str, str] = {}
    history_files = 0
    lines_out: list[str] = []
    for p, rel in walk(root):
        if any(rel.startswith(h + "/") for h in HISTORY_DIRS):
            history_files += 1
            continue
        base = os.path.basename(rel)
        ext = os.path.splitext(base)[1]
        if ext not in TEXT_EXT:
            continue
        try:
            with open(p, encoding="utf-8", newline="") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError) as e:
            errors.append(f"{rel}: unreadable ({e})")
            continue
        if RX_ANY.search(text):
            try:
                fs = scan_file(rel, text, skip_cats)
            except (tokenize.TokenError, SyntaxError, IndentationError) as e:
                errors.append(f"{rel}: could not tokenize ({e}); no edits")
                continue
            edits = list(fs.edits.values())
            for s in fs.skips:
                s["file"] = rel
                all_skips.append(s)
            if edits:
                new = apply_edits(text, edits)
                if ext == ".py":
                    try:
                        compile(new, rel, "exec")
                    except SyntaxError as e:
                        errors.append(f"{rel}: result does not compile ({e}); no edits")
                        continue
                rewrites[rel] = new
                originals[rel] = text
                lines_out.append(f"== {rel}  ({len(edits)} edits)")
                by_line: dict[int, list[dict]] = collections.defaultdict(list)
                for e in edits:
                    e["file"] = rel
                    all_edits.append(e)
                    by_line[e["line"]].append(e)
                new_lines = new.split("\n")
                old_lines = text.split("\n")
                shift = 0
                for ln in sorted(by_line):
                    # the line count never changes, so the same index holds the rewritten line
                    lines_out.append(f"  L{ln} - {old_lines[ln - 1].rstrip(chr(13))[:300]}")
                    lines_out.append(f"  L{ln} + {new_lines[ln - 1].rstrip(chr(13))[:300]}")
        if RX_ANY.search(base):
            nb = rename_basename(base)
            if nb != base and "file-rename" not in skip_cats:
                renames.append((rel, rel[: len(rel) - len(base)] + nb))

    # line-length growth
    long_lines = []
    for rel, new in rewrites.items():
        if rel.endswith(".py"):
            before = originals[rel].split("\n")
            for i, ln in enumerate(new.split("\n"), 1):
                if len(ln.rstrip("\r")) > 110 and len(before[i - 1].rstrip("\r")) <= 110:
                    long_lines.append(f"{rel}:{i} ({len(before[i - 1].rstrip(chr(13)))} -> {len(ln.rstrip(chr(13)))})")

    # special cases
    special: dict[str, list[str]] = collections.defaultdict(list)
    for e in all_edits:
        rel = e["file"]
        try:
            lt = originals[rel].split("\n")[e["line"] - 1]
        except IndexError:
            continue
        for sid, prx, lrx, why in SPECIAL:
            if re.search(prx, rel) and re.search(lrx, lt, re.I) and not (sid == "history-pin" and e["cat"] == "test-name"):
                special[sid].append(f"{rel}:{e['line']}")
                break
        else:
            if rel.startswith("tests/") and RX_TEST_PIN.search(lt) and e["cat"] in ("literal", "id-in-text", "responded-by"):
                special["test-pin"].append(f"{rel}:{e['line']}")
    special_notes = {sid: why for sid, _, _, why in SPECIAL}
    special_notes["test-pin"] = "a test that asserts the id value: follows the rename, and fails loudly if code and test disagree"

    # outputs
    with open(os.path.join(out_dir, "changes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out) + "\n")
        for a, b in renames:
            f.write(f"RENAME {a} -> {b}\n")
    with open(os.path.join(out_dir, "edits.json"), "w", encoding="utf-8") as f:
        json.dump([{k: e[k] for k in ("file", "line", "cat")} for e in all_edits], f)
    with open(os.path.join(out_dir, "skipped.json"), "w", encoding="utf-8") as f:
        json.dump(all_skips, f, ensure_ascii=False)
    with open(os.path.join(out_dir, "special.json"), "w", encoding="utf-8") as f:
        json.dump({"notes": special_notes, "hits": special, "long_lines": long_lines, "errors": errors}, f,
                  ensure_ascii=False, indent=1)

    by_cat = collections.Counter(e["cat"] for e in all_edits)
    by_dir = collections.Counter("/".join(e["file"].split("/")[:3 if e["file"].startswith(("backend", "web")) else 1])
                                 for e in all_edits)
    skip_cat = collections.Counter(s["cat"] for s in all_skips)
    rep = [f"# rename_ceo codemod report ({'APPLY' if args.apply else 'DRY RUN'})", "",
           f"root: {root}", f"files rewritten: {len(rewrites)}; edits: {len(all_edits)}; file renames: {len(renames)}; "
           f"migration files left alone: {history_files}; errors: {len(errors)}", "", "## Edits by category"]
    rep += [f"- {c}: {n}" for c, n in by_cat.most_common()]
    rep += ["", "## Edits by directory"] + [f"- {d}: {n}" for d, n in by_dir.most_common()]
    rep += ["", "## Skipped hits by reason"] + [f"- {c}: {n}" for c, n in skip_cat.most_common()]
    rep += ["", "## Special cases (file:line)"]
    for sid, hits in special.items():
        rep += [f"### {sid} ({len(hits)})", special_notes.get(sid, ""), *[f"- {h}" for h in hits[:60]],
                *([f"- ... {len(hits) - 60} more"] if len(hits) > 60 else [])]
    rep += ["", "## File renames"] + [f"- {a} -> {b}" for a, b in renames]
    rep += ["", f"## Python lines pushed over 110 columns by the rename ({len(long_lines)})"] + [f"- {x}" for x in long_lines]
    rep += ["", "## Errors"] + [f"- {x}" for x in errors]
    with open(os.path.join(out_dir, "codemod_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rep) + "\n")

    if not args.quiet:
        print("\n".join(lines_out))
        for a, b in renames:
            print(f"RENAME {a} -> {b}")
    print(f"\n{'APPLY' if args.apply else 'DRY RUN'}: {len(all_edits)} edits in {len(rewrites)} files, "
          f"{len(renames)} file renames, {len(errors)} errors; report in {out_dir}", file=sys.stderr)
    for e in errors:
        print("ERROR", e, file=sys.stderr)

    if args.apply:
        if errors:
            print("not applying: fix the errors first", file=sys.stderr)
            return 2
        for rel, new in rewrites.items():
            with open(os.path.join(root, *rel.split("/")), "w", encoding="utf-8", newline="") as f:
                f.write(new)
        for a, b in renames:
            pa, pb = os.path.join(root, *a.split("/")), os.path.join(root, *b.split("/"))
            if os.path.exists(pb):
                print("ERROR rename target exists:", b, file=sys.stderr)
                return 2
            if args.git_mv:
                subprocess.run(["git", "mv", a, b], cwd=root, check=True)
            else:
                os.rename(pa, pb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
