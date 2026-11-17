"""
A stand-in for the module `pwd`, for SearXNG on Windows (llmcf's own Python folder for the search engine).

SearXNG's `searx/valkeydb.py` does `import pwd` at the top, and the web application imports that file when it starts.
`pwd` exists only on Linux and macOS, so on Windows SearXNG would stop with "No module named 'pwd'" before it serves
anything. The only use is a log line when a Valkey database cannot be reached (`pwd.getpwuid(os.getuid())`), a path llmcf
never takes (its SearXNG has no Valkey: the limiter is off, see configs/searxng/settings.yml). This file answers the
import and gives that log line a name, nothing more. It is put on PYTHONPATH of the SearXNG process on Windows only
(Core/searxng_native.py); on Linux and macOS the real module is used and this file is never read.
"""

import getpass
from collections import namedtuple

struct_passwd = namedtuple("struct_passwd", "pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell")


def _entry(uid: int = 0) -> struct_passwd:
    name = getpass.getuser()
    return struct_passwd(name, "x", int(uid), 0, name, "", "")


def getpwuid(uid: int) -> struct_passwd:
    return _entry(uid)


def getpwnam(name: str) -> struct_passwd:
    entry = _entry()
    return entry._replace(pw_name=str(name))


def getpwall() -> list:
    return [_entry()]
