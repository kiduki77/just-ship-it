"""Owner-only permissions for what the server keeps on disk: var/ (api_token, secrets, login codes, calls) and .env.

POSIX: mode 0700 for a directory, 0600 for a file. Windows ignores those modes, and a checkout under a folder such
as C:\\workspace inherits ACEs that give BUILTIN\\Users read and Authenticated Users modify access. There the path
gets a protected DACL with one ACE for the current user, which a directory's children inherit.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def restrict(path: Path) -> None:
    """Make path readable and writable by the current user only. Idempotent, and a no-op when it already is."""
    if sys.platform == "win32":
        if not _win_owner_only(path):
            _win_set_owner_only(path)
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def owner_only(path: Path) -> bool:
    if sys.platform == "win32":
        return _win_owner_only(path)
    return os.stat(path).st_mode & 0o077 == 0


if sys.platform == "win32":
    import ctypes
    from ctypes import POINTER, byref, c_int, c_void_p
    from ctypes.wintypes import BOOL, DWORD, HANDLE, LPCWSTR

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for _fn, _args in {
        "OpenProcessToken": [HANDLE, DWORD, POINTER(HANDLE)],
        "GetTokenInformation": [HANDLE, c_int, c_void_p, DWORD, POINTER(DWORD)],
        "ConvertSidToStringSidW": [c_void_p, POINTER(c_void_p)],
        "GetNamedSecurityInfoW": [LPCWSTR, c_int, DWORD, c_void_p, c_void_p, c_void_p, c_void_p, POINTER(c_void_p)],
        "SetNamedSecurityInfoW": [LPCWSTR, c_int, DWORD, c_void_p, c_void_p, c_void_p, c_void_p],
        "ConvertSecurityDescriptorToStringSecurityDescriptorW": [c_void_p, DWORD, DWORD, POINTER(c_void_p), c_void_p],
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": [LPCWSTR, DWORD, POINTER(c_void_p), c_void_p],
        "GetSecurityDescriptorDacl": [c_void_p, POINTER(BOOL), POINTER(c_void_p), POINTER(BOOL)],
    }.items():
        getattr(_advapi, _fn).argtypes = _args
    _advapi.GetNamedSecurityInfoW.restype = DWORD
    _advapi.SetNamedSecurityInfoW.restype = DWORD
    _kernel.GetCurrentProcess.restype = HANDLE
    _kernel.CloseHandle.argtypes = [HANDLE]
    _kernel.LocalFree.argtypes = [c_void_p]
    _SE_FILE_OBJECT = 1
    _DACL = 0x4
    _PROTECTED_DACL = 0x80000000
    _TOKEN_QUERY = 0x8
    _TOKEN_USER = 1

    def _check(ok: int, what: str) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error(), what)

    def _take_string(ptr: c_void_p) -> str:
        try:
            return ctypes.wstring_at(ptr.value)
        finally:
            _kernel.LocalFree(ptr)

    def _user_sid() -> str:
        token = HANDLE()
        _check(_advapi.OpenProcessToken(_kernel.GetCurrentProcess(), _TOKEN_QUERY, byref(token)), "OpenProcessToken")
        try:
            size = DWORD()
            _advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, byref(size))
            buf = ctypes.create_string_buffer(size.value)
            _check(_advapi.GetTokenInformation(token, _TOKEN_USER, buf, size, byref(size)), "GetTokenInformation")
            sid = ctypes.cast(buf, POINTER(c_void_p))[0]  # TOKEN_USER.User.Sid
            text = c_void_p()
            _check(_advapi.ConvertSidToStringSidW(sid, byref(text)), "ConvertSidToStringSidW")
            return _take_string(text)
        finally:
            _kernel.CloseHandle(token)

    def _win_dacl_sddl(path: Path) -> str:
        sd, text = c_void_p(), c_void_p()
        err = _advapi.GetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL, None, None, None, None, byref(sd))
        if err:
            raise ctypes.WinError(err, "GetNamedSecurityInfoW")
        try:
            _check(_advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, _DACL, byref(text), None),
                   "ConvertSecurityDescriptorToStringSecurityDescriptorW")
            return _take_string(text)
        finally:
            _kernel.LocalFree(sd)

    def _win_owner_only(path: Path) -> bool:
        """Every ACE is an allow ACE for the current user. A file inside a restricted directory gets that from the
        directory's inherited ACE, so its DACL need not be protected itself."""
        sddl = _win_dacl_sddl(path)
        aces = sddl[sddl.find("(") + 1:-1].split(")(") if "(" in sddl else []
        sid = _user_sid()
        return bool(aces) and all(a.startswith("A;") and a.endswith(f";{sid}") for a in aces)

    def _win_set_owner_only(path: Path) -> None:
        inherit = "OICI" if path.is_dir() else ""
        sd, present, dacl, defaulted = c_void_p(), BOOL(), c_void_p(), BOOL()
        _check(_advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;{inherit};FA;;;{_user_sid()})", 1, byref(sd), None),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            _check(_advapi.GetSecurityDescriptorDacl(sd, byref(present), byref(dacl), byref(defaulted)),
                   "GetSecurityDescriptorDacl")
            err = _advapi.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL | _PROTECTED_DACL, None, None,
                                                dacl, None)
            if err:
                raise ctypes.WinError(err, "SetNamedSecurityInfoW")
        finally:
            _kernel.LocalFree(sd)
