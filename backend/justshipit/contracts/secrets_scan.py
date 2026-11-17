"""What a secret looks like, shared by the scan of a product the owner brings (services/existing_product.py) and the
scrubber of team chat (services/chat.py), which removes secrets from pasted text before anything stores it."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

# By kind. POSIX extended regexes, since `git grep -E` reads them.
SECRET_PATTERNS: dict[str, str] = {
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----",
    "github_token": r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}",
    "slack_token": r"xox[abprs]-[0-9]+-[A-Za-z0-9-]{10,}",
    "api_key": r"sk-ant-[A-Za-z0-9_-]{20,}|sk-(proj-)?[A-Za-z0-9_-]{32,}|(sk|rk)_live_[A-Za-z0-9]{24,}",
    "aws_key": r"AKIA[0-9A-Z]{16}",
    "google_key": r"AIza[0-9A-Za-z_-]{35}",
}

# What pasted text holds that a repository scan does not look for. A pattern's `v` group is the secret; the words
# around it stay, so "Authorization: Bearer" and "GITHUB_TOKEN=" still say what was there.
_PASTED: dict[str, str] = {
    "private_key": r"(?P<v>-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY( BLOCK)?-----|\Z))",
    "bearer": r"(?i:\bBearer)\s+(?P<v>[A-Za-z0-9._~+/=-]{8,})",
    "env_secret": r"(?m)^[ \t]*(?:export[ \t]+)?[A-Za-z0-9_]*(?i:token|secret|key|password|pass)[ \t]*[=:][ \t]*"
                  r"(?P<v>[^\s\[][^\r\n]*?)[ \t]*$",
}
_SECRET_WORD = r"(?i:token|secret|key|password|passwd|pass|pwd)"
# Shapes a pasted log or config holds beyond a line of its own. A value that starts with `[` is a marker already.
_MORE: tuple[tuple[str, str], ...] = (
    ("api_key", r"(?P<v>npm_[A-Za-z0-9]{36})"),
    # A token the terminal wrapped onto a second line: the whole shape went first, so this one is shorter.
    ("github_token", r"(?P<v>(?:(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{4,35}|github_pat_[A-Za-z0-9_]{4,})"
                     r"[ \t]*\r?\n[ \t]*[A-Za-z0-9_]{1,80}(?![A-Za-z0-9_]))"),
    ("bearer", r"(?i:\b(?:Proxy-)?Authorization)[ \t]*:[ \t]*(?:[A-Za-z]+[ \t]+)?(?P<v>[A-Za-z0-9._~+/=-]{8,})"),
    ("env_secret", r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]*:(?P<v>[^\s@/\[]+)@"),  # scheme://user:password@host
    ("env_secret", rf'"[A-Za-z0-9_-]*{_SECRET_WORD}"[ \t]*:[ \t]*"(?P<v>[^"\s\[][^"]*)"'),  # "password": "…"
    ("env_secret", r"(?i:_authToken|_auth|_password)[ \t]*=[ \t]*(?P<v>[^\s\[]\S*)"),  # .npmrc
    # --token value, --password=value: a value with a digit, a capital or a symbol, so "--token flag" stays.
    ("env_secret", rf"(?<![\w-])--?[A-Za-z0-9-]*{_SECRET_WORD}(?:[ \t]+|[ \t]*=[ \t]*)"
                   r"(?P<v>(?=\S*[0-9A-Z_./+=:])[^\s\[-]\S{5,})"),
    ("env_secret", r"(?i:\b(?:Set-)?Cookie)[ \t]*:[ \t]*(?P<v>[^\r\n]*[^\s])"),
)

_SCRUB: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(_PASTED["private_key"])),
    *((kind, re.compile(f"(?P<v>{p})")) for kind, p in SECRET_PATTERNS.items() if kind != "private_key"),
    ("bearer", re.compile(_PASTED["bearer"])),
    *((kind, re.compile(p)) for kind, p in _MORE),
    ("env_secret", re.compile(_PASTED["env_secret"])),
)
KINDS: tuple[str, ...] = tuple(dict.fromkeys(kind for kind, _ in _SCRUB))


def scrub(text: str, marker: Callable[[str], str]) -> tuple[str, list[dict]]:
    """`text` with every secret replaced by `marker(kind)`, and what was removed as `[{"kind", "n"}]`, never the value.
    The token shapes go first, so a `GITHUB_TOKEN=ghp_…` line counts one GitHub token, not a second secret."""
    found: Counter[str] = Counter()
    for kind, pattern in _SCRUB:
        mark = marker(kind)

        def swap(m: re.Match[str], kind: str = kind, mark: str = mark) -> str:
            found[kind] += 1
            start, end = m.span("v")
            return m.group(0)[:start - m.start()] + mark + m.group(0)[end - m.start():]
        text = pattern.sub(swap, text)
    return text, [{"kind": k, "n": n} for k, n in found.items()]
