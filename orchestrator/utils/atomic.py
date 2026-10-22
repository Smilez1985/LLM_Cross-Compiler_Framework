#!/usr/bin/env python3
"""
Private files written atomically (consolidation chain c1-imports: moved unchanged from Core/vault_key.py and
runtime/llama_server.py, which keep the old names, so the utils and the kernel no longer import the Core for them).

- replace_file(src, dst): os.replace, tried again for a moment on Windows while another process has the target open
  (REPLACE_RETRIES times there, never elsewhere).
- write_private(path, text): the shared writer (the secrets store, profiles, trusted boards, the notices, the input
  history, the GUI bridge): the link is resolved first (a link onto an encrypted volume keeps pointing there), the
  folder is made 0700, a temporary ".<name>.<random>.tmp" file next to the target is written, flushed and synced,
  made 0600 (not on Windows) and moved over the target with replace_file.
- write_private_exclusive(path, text): the local runtime's state files (llama_server, the build store, the runtime
  mode): a ".<name>.<8 hex>.tmp" file created with O_EXCL and 0600, LF line ends on every system, no fsync, no link
  resolution (a link is replaced by the file), plain os.replace.
Neither leaves its temporary file behind. The other writers (JSON and signed stores) are unified later (chain
c5-io-stores).
- owner_only(path): an existing file private to this account (the framework's SSH key before OpenSSH uses it, chain
  c2-remote-system; the one helper for every file that must be "only you", such as Mojo's narrowing of a token file).
  On POSIX the group and other bits go and the owner's stay (as Mojo's state.tighten narrows the state folder). On
  Windows, where os.chmod sets only the read-only flag, the file gets a protected ACL with one entry: full access for
  this account, nothing inherited. That is the form OpenSSH for Windows accepts for a private key ("UNPROTECTED
  PRIVATE KEY FILE" otherwise), and other accounts of this computer (Users, Authenticated Users) lose access.
"""

import os
import platform
import secrets
import stat
import tempfile
import time
from pathlib import Path

REPLACE_RETRIES = 10 if os.name == "nt" else 0
WINDOWS = os.name == "nt"   # which way owner_only narrows a file (tests set it to try the Windows way with fakes)


def replace_file(src, dst) -> None:
    """os.replace, tried again for a moment on Windows while another process has the target open."""
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt >= REPLACE_RETRIES:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_private(path: Path, text: str) -> None:
    """Atomic write with mode 0600 (temp file in the same folder, then rename)."""
    path = Path(os.path.realpath(path))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if platform.system() != "Windows":
            os.chmod(tmp, 0o600)
        replace_file(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_private_exclusive(path: Path, text: str) -> None:
    """Writes a file only the current user can read (created 0600, replaced atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# --- owner only ------------------------------------------------------------------------------------------------------

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000   # inherited entries of the folder do not apply
_FILE_ALL_ACCESS = 0x001F01FF
_ACL_REVISION = 2
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1                                     # TOKEN_INFORMATION_CLASS TokenUser
_ACL_HEADER = 8                                     # sizeof(ACL)
_ACE_HEADER = 8                                     # sizeof(ACCESS_ALLOWED_ACE) without its SidStart


def owner_only(path) -> None:
    """Makes the existing file at `path` private to this account (see the module docstring); OSError when that
    cannot be done. A file that is private already stays as it is."""
    if WINDOWS:
        _windows_owner_only(str(path))
        return
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        os.chmod(path, mode & 0o700)


def _windows_api():
    """advapi32 and kernel32 with the argument types owner_only uses (own instances, so the types stay local; tests
    replace this with fakes)."""
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.InitializeAcl.restype = wintypes.BOOL
    advapi32.AddAccessAllowedAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    advapi32.AddAccessAllowedAce.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    return advapi32, kernel32


def _windows_owner_only(path: str) -> None:
    """The protected one-entry ACL of owner_only: this process's account (the user SID of its token) with full
    access, set with SetNamedSecurityInfoW (no other program is started)."""
    import ctypes
    from ctypes import wintypes
    advapi32, kernel32 = _windows_api()

    def failed(what: str, code: int = 0) -> OSError:
        code = code or getattr(ctypes, "get_last_error", lambda: 0)()
        return OSError(code, f"{what} failed (Windows error {code})", path)

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise failed("OpenProcessToken")
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))  # asks for the size only
        if not needed.value:
            raise failed("GetTokenInformation")
        user = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, user, needed, ctypes.byref(needed)):
            raise failed("GetTokenInformation")
    finally:
        kernel32.CloseHandle(token)
    # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is the pointer to the account's SID (in `user`)
    sid = ctypes.c_void_p(ctypes.cast(user, ctypes.POINTER(ctypes.c_void_p))[0])
    size = (_ACL_HEADER + _ACE_HEADER + advapi32.GetLengthSid(sid) + 3) & ~3
    acl = ctypes.create_string_buffer(size)
    if not advapi32.InitializeAcl(acl, size, _ACL_REVISION):
        raise failed("InitializeAcl")
    if not advapi32.AddAccessAllowedAce(acl, _ACL_REVISION, _FILE_ALL_ACCESS, sid):
        raise failed("AddAccessAllowedAce")
    error = advapi32.SetNamedSecurityInfoW(path, _SE_FILE_OBJECT,
                                           _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                                           None, None, acl, None)
    if error:
        raise failed("SetNamedSecurityInfoW", error)


__all__ = ["REPLACE_RETRIES", "owner_only", "replace_file", "write_private", "write_private_exclusive"]
