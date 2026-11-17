"""Who may talk to the console API.

Every /api request needs the token in var/api_token, as `Authorization: Bearer` or as the console's cookie made
from it, in every bind mode: on loopback any local process (curl, a script, a container reaching the port as
host.docker.internal) can write whatever Host and headers it likes. The owner's browser gets the cookie by
opening a one-time login link whose code `ship api` mints into var/login_codes, so the long-lived token never
appears in a URL or a log line.

The older checks stay as defense in depth. Bound to loopback, a Host other than a loopback name means DNS
rebinding. A change without the console's X-Ship header, or with another site's Origin, means a page on
another site, since a browser cannot add that header cross-site without a CORS preflight this server never
answers.

A cookie goes to every port of its host, and the preview of a product (engineering/try_it.py) is code an agent
wrote, served on this PC. The owner opens it as `localhost` and the console answers on the loopback address only,
so the browser never sends the console's cookie to it. Every response also carries the headers that tell a browser
what the console may load and who may frame it (`guarded_send`).
"""
from __future__ import annotations

import copy
import errno
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from justshipit.settings import settings

CONSOLE_HEADER = "x-ship"
LEGACY_CONSOLE_HEADER = "x-aiteam"  # what scripts sent before the rename; accepted for one release
COOKIE = "ship_token"
LOGIN_PARAM = "login"
LOGIN_TTL_S = 600
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# What the built console loads, all of it from this server: its script and style files, the fonts its style names,
# images (its own, the captures of a build under /api, one data: icon in its style), and /api calls with the event
# stream. The pages this server writes itself (no build yet, a login address that cannot be used) carry style
# attributes, so the style rule allows inline styles; no script is inline, nothing is framed, no image is a blob.
CONTENT_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                  "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                  "form-action 'self'")
_GUARD_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                  "Content-Security-Policy": CONTENT_POLICY}

_LOGIN_UNUSABLE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Just Ship It</title>
<body style="font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;line-height:1.6">
<h1>This login address can't be used</h1>
<p>A login address works once and expires after 10 minutes. In the terminal of the PC running <code>ship api</code>,
get a new login address with the command below and open it.</p>
<pre style="background:#f3f4f6;padding:.75rem 1rem;border-radius:6px">uv run ship api --login</pre>
</body></html>"""


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


# Windows files have no owner/group/other bits to tighten, and its os.fchmod refuses a handle opened read-only.
_POSIX_MODES = os.name == "posix"


class TokenFileError(Exception):
    """var/api_token cannot be used as the API token, said in Korean for the owner."""


def _link_refused(path: Path) -> TokenFileError:
    return TokenFileError(f"var/{path.name} is a symbolic link, so it isn't written. Remove the link and run again.")


def _open_unlinked(path: Path, flags: int) -> int:
    """os.open that refuses a symbolic link at path with ELOOP. Windows has no O_NOFOLLOW, so there the open
    follows a link and is checked against the path's own lstat afterwards."""
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not os.path.samestat(st, os.fstat(fd)):
        os.close(fd)
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(path))
    return fd


def _read_regular(path: Path) -> str:
    try:
        fd = _open_unlinked(path, os.O_RDONLY)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _link_refused(path) from exc
        raise
    with os.fdopen(fd, encoding="utf-8") as f:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise TokenFileError(f"var/{path.name} isn't a regular file. Remove it and run again.")
        if _POSIX_MODES:
            os.fchmod(fd, 0o600)
        return f.read().strip()


def _write_new(path: Path, text: str) -> None:
    fd = _open_unlinked(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def ensure_token(var_dir: Path) -> str:
    """The API token in var/api_token, made once. Never follows a link planted at that path."""
    path = var_dir / "api_token"
    try:
        if stored := _read_regular(path):
            return stored
        path.unlink()
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    try:
        _write_new(path, token)
    except FileExistsError:
        return _read_regular(path)
    return token


def rotate_token(var_dir: Path) -> str:
    """Replace the token, which ends every console cookie and Bearer use of the old one, and drop unused
    login codes, which would otherwise hand out the new one."""
    path = var_dir / "api_token"
    if path.is_symlink():
        raise _link_refused(path)
    token = secrets.token_urlsafe(32)
    fresh = var_dir / f".api_token.{secrets.token_hex(8)}"
    _write_new(fresh, token)
    os.replace(fresh, path)
    codes = var_dir / "login_codes"
    if codes.is_dir() and not codes.is_symlink():
        for f in codes.iterdir():
            f.unlink(missing_ok=True)
    return token


def retire_cookie_token(var_dir: Path) -> bool:
    """Rotate the token once, on the first start of a version whose cookie is derived from it: before, the cookie
    held the token itself, and every 127.0.0.1 port received it, so a copy may still work as a Bearer. The
    marker var/cookie_is_hmac records that it was done. True when this call rotated the token."""
    mark = var_dir / "cookie_is_hmac"
    if os.path.lexists(mark):
        return False
    rotated = os.path.lexists(var_dir / "api_token")
    if rotated:
        rotate_token(var_dir)
    try:
        _write_new(mark, "The console cookie holds HMAC(token); the token from before it was rotated.\n")
    except FileExistsError:
        pass
    return rotated


_seen: dict[Path, tuple[tuple[int, int, int], str | None]] = {}


def current_token(var_dir: Path) -> str | None:
    """The token var/api_token holds now, so a rotation reaches a running server without a restart. None when
    there is no usable token, which refuses every protected request."""
    path = var_dir / "api_token"
    try:
        st = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    if (hit := _seen.get(path)) and hit[0] == key:
        return hit[1]
    try:
        token = _read_regular(path) or None
    except (OSError, TokenFileError):
        token = None
    _seen[path] = (key, token)
    return token


def _code_file(var_dir: Path, code: str) -> Path:
    # Stored hashed, so a copy of var/ never holds a live login code.
    return var_dir / "login_codes" / hashlib.sha256(code.encode()).hexdigest()


def _expiry(path: Path) -> float:
    fd = _open_unlinked(path, os.O_RDONLY)
    with os.fdopen(fd, encoding="utf-8") as f:
        return float(f.read())


def mint_login_code(var_dir: Path, ttl_s: float = LOGIN_TTL_S) -> str:
    """A single-use login code good for ttl_s seconds. Kept on disk, so `ship api --login` can mint one for a
    server running in another process."""
    codes = var_dir / "login_codes"
    codes.mkdir(mode=0o700, exist_ok=True)
    if codes.is_symlink():
        raise _link_refused(codes)
    now = time.time()
    for old in codes.iterdir():
        try:
            if _expiry(old) <= now:
                old.unlink()
        except (OSError, ValueError):
            old.unlink(missing_ok=True)
    code = secrets.token_urlsafe(18)
    _write_new(_code_file(var_dir, code), str(now + ttl_s))
    return code


def redeem_login_code(var_dir: Path, code: str) -> bool:
    """Whether code was live. It is used up either way, and only the request whose unlink succeeds wins."""
    path = _code_file(var_dir, code)
    try:
        expires = _expiry(path)
        path.unlink()
    except (OSError, ValueError):
        return False
    return time.time() < expires


class DropQuery(logging.Filter):
    """Uvicorn's access line carries the request target with its query string, where a login code travels."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) > 2:
            record.args = (*record.args[:2], str(record.args[2]).split("?", 1)[0], *record.args[3:])
        return True


def log_config() -> dict:
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"drop_query": {"()": DropQuery}}
    config["loggers"]["uvicorn.access"]["filters"] = ["drop_query"]
    return config


def _hostname(netloc: str) -> str | None:
    try:
        return urlsplit("//" + netloc).hostname
    except ValueError:
        return None


def _origin_netloc(origin: str) -> str | None:
    try:
        return urlsplit(origin).netloc.lower()
    except ValueError:
        return None


def _same(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented.encode(), expected.encode())


def _cookie_for(token: str) -> str:
    # Every port on 127.0.0.1 receives the cookie (a dev server, another local app), so it holds a value derived
    # from the token, not the token: whatever reads it there does not learn the Bearer. It is still a full
    # credential when replayed as the cookie, until the token is rotated.
    return hmac.new(token.encode(), b"cookie", hashlib.sha256).hexdigest()


def presents_token(request: Request, token: str | None) -> bool:
    """Whether the request carries token as `Authorization: Bearer`, or the console's cookie made from it."""
    if token is None:
        return False
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return _same(auth.removeprefix("Bearer "), token)
    return _same(request.cookies.get(COOKIE, ""), _cookie_for(token))


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def guarded_send(send: Send, path: str) -> Send:
    """`send`, adding the guard headers to the response it starts. A header the response set itself stays: a build's
    capture is served under a stricter policy, and the event stream and the captures name their own cache rule.
    Data under /api that names none is never kept by the browser."""
    async def guarded(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            for name, value in _GUARD_HEADERS.items():
                headers.setdefault(name, value)
            if _is_api(path):
                headers.setdefault("Cache-Control", "no-store")
        await send(message)
    return guarded


def _refuse(status: int, detail: str) -> Response:
    return JSONResponse({"detail": detail}, status_code=status)


def _login(request: Request, var_dir: Path, token: str | None) -> Response:
    redeemed = redeem_login_code(var_dir, request.query_params[LOGIN_PARAM])
    if not (redeemed and token) and not presents_token(request, token):
        return HTMLResponse(_LOGIN_UNUSABLE, status_code=401)
    rest = request.url.remove_query_params(LOGIN_PARAM)
    # One leading slash, so a path like //other.host cannot turn the redirect into one off this site.
    back = RedirectResponse("/" + rest.path.lstrip("/") + (f"?{rest.query}" if rest.query else ""), status_code=303)
    if redeemed:
        back.set_cookie(COOKIE, _cookie_for(token), max_age=30 * 24 * 3600, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https")
    return back


def check(request: Request, var_dir: Path, loopback_only: bool) -> Response | None:
    """The response that refuses `request`, or None to let it through."""
    host = request.headers.get("host", "")
    path = request.scope["path"]
    name = _hostname(host)
    off_address = "This request came in on an address that isn't allowed. Open the console at http://127.0.0.1."
    if loopback_only and name == "localhost":
        # Before the login code is read: a cookie set for localhost would go to the product preview on localhost.
        if request.method in ("GET", "HEAD") and not _is_api(path):
            return RedirectResponse(str(request.url.replace(hostname="127.0.0.1")), status_code=307)
        return _refuse(403, off_address)
    if loopback_only and not is_loopback(name):
        return _refuse(403, off_address)
    token = current_token(var_dir)
    if request.method == "GET" and LOGIN_PARAM in request.query_params:
        return _login(request, var_dir, token)
    # The console's own files carry no data, and serving them lets the console explain a missing login.
    guarded = _is_api(path) or request.method not in ("GET", "HEAD")
    if guarded and not presents_token(request, token):
        return _refuse(401, "You need to log in. Open the login address shown in the terminal running ship api. "
                            "If it has expired, get a new one in that terminal with uv run ship api --login.")
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        if not (CONSOLE_HEADER in request.headers or LEGACY_CONSOLE_HEADER in request.headers) or (origin is not None and _origin_netloc(origin) != host.lower()):
            return _refuse(403, "Refused: this change request came from outside the console.")
    return None


class AccessGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # No route takes one and `check` reads only HTTP requests, so a future WebSocket route must not
            # inherit an open door.
            await WebSocketClose(code=1008)(scope, receive, send)
            return
        if scope["type"] == "http":
            send = guarded_send(send, scope["path"])
            refusal = check(Request(scope), settings().var_dir, scope["app"].state.loopback_only)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
        await self.app(scope, receive, send)
