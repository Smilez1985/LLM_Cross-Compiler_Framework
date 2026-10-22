"""Path labelling and home-folder scrubbing for AIs (consolidation chain c1-paths: orchestrator/security/paths.py).

Characterization first: each scrubber keeps its own policy, and a move must neither widen nor narrow one silently.
- label_paths: every path by its label (a known folder) or its file name; a home folder as "the home folder" or
  "a home folder" (the account name is never shown).
- device_text: a board's account and its home folder never reach an AI (D0); then label_paths and the status scrub.
- tools_gui._no_home: text the person typed, with every home folder as "~" (no labels, no cap).
- tools_status._scrub_paths: framework text with this computer's home folder as "~", secrets redacted, cleaned, capped.
- tools_deploy.hide_paths: known folders by their label, then the status scrub.
- community_pack.scrub: the irreversible anonymisation of an export; llm/privacy: the reversible masks (home users).
Every case runs with this computer's home folder as a POSIX path and as a Windows path, on any host.
"""

import getpass
import socket
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from orchestrator.Core import community_pack
from orchestrator.llm import privacy
from orchestrator.tools import tools_deploy, tools_gui, tools_runtime, tools_status

ROOTS = {"/srv/my out": "output"}
DEVICE_USERS = ["dietpi"]

CASES = (
    "cannot open '/home/Jane Doe/My Models/x.gguf'",
    "load /home/Jane Doe/models/qwen 2/model.gguf failed",
    "home is /home/Jane Doe here",
    "in c:/users/jane doe/src/llama.cpp now",
    r"open C:\Users\Jane Doe\Desktop\m.gguf",
    "[WinError 5] Access is denied: " + repr(r"C:\Users\Jane Doe\x.bin"),
    "[WinError 5] Access is denied: " + repr(r"C:\Users\Bob Smith"),
    "cannot read /usr/home/bob/x.gguf and /usr/home/bob",
    "ls /var/home/pi",
    "data in /export/home/carol/data.bin and /export/home/carol",
    "WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave",
    "MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin",
    r"share \\fs\Users\frank\m.gguf and \\fs\Users\frank",
    r"share '\\srv\home$\gina\My Models\x.gguf'",
    "ok (/srv/my out/Rk/m.gguf)",
    "nothing in /srv/my out.",
    "scp to dietpi@board:/home/dietpi/x.tar failed",
    "installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models",
    "bad uri file:///home/Jane Doe/private/a.bin",
    "keep 127.0.0.1:8080/x and https://x.org/a/b and and/or",
    "colour \x1b[31mred\u200b text",
    "x" * 520 + " /home/Jane Doe/end",
)


def scrubbers():
    """Each scrubber under the name its callers use today."""
    return {
        "label_paths": lambda s: tools_runtime.label_paths(s, ROOTS),
        "shorten_home": tools_runtime.shorten_home,
        "device_text": lambda s: tools_runtime.device_text(s, DEVICE_USERS, ROOTS),
        "no_home": tools_gui._no_home,
        "scrub_paths": tools_status._scrub_paths,
        "hide_paths": lambda s: tools_deploy.hide_paths(s, {"/srv/my out": "the output folder"}),
        "community_scrub": community_pack.scrub,
        "privacy_home_users": lambda s: [span.value for span in privacy._find_home_users(s)],
    }


@pytest.fixture(params=["posix", "windows"])
def home(request, monkeypatch):
    """This computer's home folder 'Jane Doe' (account jdoe on host build-host-7), as a POSIX or a Windows path."""
    text = "/home/Jane Doe" if request.param == "posix" else r"C:\Users\Jane Doe"
    pure = PurePosixPath(text) if request.param == "posix" else PureWindowsPath(text)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, text)
    for name in ("USER", "USERNAME"):
        monkeypatch.setenv(name, "jdoe")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: pure))
    monkeypatch.setattr(getpass, "getuser", lambda: "jdoe")
    monkeypatch.setattr(socket, "gethostname", lambda: "build-host-7")
    return request.param


# Today's output of each scrubber for each case (characterization: the move into security/paths.py keeps it). The
# default of 2026-10-17 (D0, no host path to an AI) then changed two cases of label_paths and device_text on purpose:
# another account's home inside a file:/// URL and another account's name with a space (tests further below).
GOLDEN = {
    'posix': {
        'community_scrub': [
            "cannot open '~/My Models/x.gguf'",
            'load ~/models/qwen 2/model.gguf failed',
            'home is ~ here',
            'in c:/users/jane doe/src/llama.cpp now',
            'open ~ Doe\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: 'C:\\\\Users\\\\Jane Doe\\\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\\\Users\\\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to <user>@<host>:~/x.tar failed',
            'installed ~/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' ~/end',
        ],
        'device_text': [
            "cannot open 'x.gguf'",
            'load model.gguf failed',
            'home is the home folder here',
            'in llama.cpp now',
            'open m.gguf',
            "[WinError 5] Access is denied: 'x.bin'",
            "[WinError 5] Access is denied: 'a home folder'",
            'cannot read x.gguf and a home folder',
            'ls a home folder',
            'data in data.bin and a home folder',
            'WSL: m.gguf and a home folder',
            'MSYS: q.gguf and a home folder',
            'share m.gguf and a home folder',
            "share 'x.gguf'",
            'ok (output:Rk/m.gguf)',
            'nothing in the output folder.',
            'scp to device account@board:x.tar failed',
            'installed q.gguf for device account; chown device account:device account models',
            'bad uri file://a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred text',
            "x" * 500 + '\n[... truncated ...]',
        ],
        'hide_paths': [
            "cannot open '~/My Models/x.gguf'",
            'load ~/models/qwen 2/model.gguf failed',
            'home is ~ here',
            'in c:/users/jane doe/src/llama.cpp now',
            'open C:\\Users\\Jane Doe\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: 'C:\\Users\\Jane Doe\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\Users\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (the output folder/Rk/m.gguf)',
            'nothing in the output folder.',
            'scp to dietpi@board:/home/dietpi/x.tar failed',
            'installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred text',
            "x" * 500 + '\n[... truncated ...]',
        ],
        'label_paths': [
            "cannot open 'x.gguf'",
            'load model.gguf failed',
            'home is the home folder here',
            'in llama.cpp now',
            'open m.gguf',
            "[WinError 5] Access is denied: 'x.bin'",
            "[WinError 5] Access is denied: 'a home folder'",
            'cannot read x.gguf and a home folder',
            'ls a home folder',
            'data in data.bin and a home folder',
            'WSL: m.gguf and a home folder',
            'MSYS: q.gguf and a home folder',
            'share m.gguf and a home folder',
            "share 'x.gguf'",
            'ok (output:Rk/m.gguf)',
            'nothing in the output folder.',
            'scp to dietpi@board:x.tar failed',
            'installed q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' end',
        ],
        'no_home': [
            "cannot open '~/My Models/x.gguf'",
            'load ~/models/qwen 2/model.gguf failed',
            'home is ~ here',
            'in c:/users/jane doe/src/llama.cpp now',
            'open ~\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: '~\\x.bin'",
            "[WinError 5] Access is denied: '~ Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to dietpi@board:~/x.tar failed',
            'installed ~/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' ~/end',
        ],
        'privacy_home_users': [
            ['Jane'],
            ['Jane'],
            ['Jane'],
            [],
            ['Jane'],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            ['dietpi'],
            ['dietpi'],
            ['Jane'],
            [],
            [],
            ['Jane'],
        ],
        'scrub_paths': [
            "cannot open '~/My Models/x.gguf'",
            'load ~/models/qwen 2/model.gguf failed',
            'home is ~ here',
            'in c:/users/jane doe/src/llama.cpp now',
            'open C:\\Users\\Jane Doe\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: 'C:\\Users\\Jane Doe\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\Users\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to dietpi@board:/home/dietpi/x.tar failed',
            'installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred text',
            "x" * 500 + '\n[... truncated ...]',
        ],
        'shorten_home': [
            "cannot open '~/My Models/x.gguf'",
            'load ~/models/qwen 2/model.gguf failed',
            'home is ~ here',
            'in c:/users/jane doe/src/llama.cpp now',
            'open C:\\Users\\Jane Doe\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: 'C:\\Users\\Jane Doe\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\Users\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to dietpi@board:/home/dietpi/x.tar failed',
            'installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' ~/end',
        ],
    },
    'windows': {
        'community_scrub': [
            "cannot open '~ Doe/My Models/x.gguf'",
            'load ~ Doe/models/qwen 2/model.gguf failed',
            'home is ~ Doe here',
            'in c:/users/jane doe/src/llama.cpp now',
            'open ~\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: 'C:\\\\Users\\\\Jane Doe\\\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\\\Users\\\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to <user>@<host>:~/x.tar failed',
            'installed ~/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~ Doe/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' ~ Doe/end',
        ],
        'device_text': [
            "cannot open 'x.gguf'",
            'load model.gguf failed',
            'home is a home folder here',
            'in llama.cpp now',
            'open m.gguf',
            "[WinError 5] Access is denied: 'x.bin'",
            "[WinError 5] Access is denied: 'a home folder'",
            'cannot read x.gguf and a home folder',
            'ls a home folder',
            'data in data.bin and a home folder',
            'WSL: m.gguf and a home folder',
            'MSYS: q.gguf and a home folder',
            'share m.gguf and a home folder',
            "share 'x.gguf'",
            'ok (output:Rk/m.gguf)',
            'nothing in the output folder.',
            'scp to device account@board:x.tar failed',
            'installed q.gguf for device account; chown device account:device account models',
            'bad uri file://a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred text',
            "x" * 500 + '\n[... truncated ...]',
        ],
        'hide_paths': [
            "cannot open '/home/Jane Doe/My Models/x.gguf'",
            'load /home/Jane Doe/models/qwen 2/model.gguf failed',
            'home is /home/Jane Doe here',
            'in ~/src/llama.cpp now',
            'open ~\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: '~\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\Users\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (the output folder/Rk/m.gguf)',
            'nothing in the output folder.',
            'scp to dietpi@board:/home/dietpi/x.tar failed',
            'installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file:///home/Jane Doe/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred text',
            "x" * 500 + '\n[... truncated ...]',
        ],
        'label_paths': [
            "cannot open 'x.gguf'",
            'load model.gguf failed',
            'home is a home folder here',
            'in llama.cpp now',
            'open m.gguf',
            "[WinError 5] Access is denied: 'x.bin'",
            "[WinError 5] Access is denied: 'a home folder'",
            'cannot read x.gguf and a home folder',
            'ls a home folder',
            'data in data.bin and a home folder',
            'WSL: m.gguf and a home folder',
            'MSYS: q.gguf and a home folder',
            'share m.gguf and a home folder',
            "share 'x.gguf'",
            'ok (output:Rk/m.gguf)',
            'nothing in the output folder.',
            'scp to dietpi@board:x.tar failed',
            'installed q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' end',
        ],
        'no_home': [
            "cannot open '~/My Models/x.gguf'",
            'load ~/models/qwen 2/model.gguf failed',
            'home is ~ Doe here',
            'in ~/src/llama.cpp now',
            'open ~\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: '~\\x.bin'",
            "[WinError 5] Access is denied: '~ Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to dietpi@board:~/x.tar failed',
            'installed ~/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file://~/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' ~/end',
        ],
        'privacy_home_users': [
            ['Jane'],
            ['Jane'],
            ['Jane'],
            [],
            ['Jane'],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            ['dietpi'],
            ['dietpi'],
            ['Jane'],
            [],
            [],
            ['Jane'],
        ],
        'scrub_paths': [
            "cannot open '/home/Jane Doe/My Models/x.gguf'",
            'load /home/Jane Doe/models/qwen 2/model.gguf failed',
            'home is /home/Jane Doe here',
            'in ~/src/llama.cpp now',
            'open ~\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: '~\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\Users\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to dietpi@board:/home/dietpi/x.tar failed',
            'installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file:///home/Jane Doe/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred text',
            "x" * 500 + '\n[... truncated ...]',
        ],
        'shorten_home': [
            "cannot open '/home/Jane Doe/My Models/x.gguf'",
            'load /home/Jane Doe/models/qwen 2/model.gguf failed',
            'home is /home/Jane Doe here',
            'in ~/src/llama.cpp now',
            'open ~\\Desktop\\m.gguf',
            "[WinError 5] Access is denied: '~\\x.bin'",
            "[WinError 5] Access is denied: 'C:\\Users\\Bob Smith'",
            'cannot read /usr/home/bob/x.gguf and /usr/home/bob',
            'ls /var/home/pi',
            'data in /export/home/carol/data.bin and /export/home/carol',
            'WSL: /mnt/c/Users/dave/m.gguf and /mnt/c/Users/dave',
            'MSYS: /c/Users/erin/models/q.gguf and /c/Users/erin',
            'share \\\\fs\\Users\\frank\\m.gguf and \\\\fs\\Users\\frank',
            "share '\\\\srv\\home$\\gina\\My Models\\x.gguf'",
            'ok (/srv/my out/Rk/m.gguf)',
            'nothing in /srv/my out.',
            'scp to dietpi@board:/home/dietpi/x.tar failed',
            'installed /home/dietpi/models/q.gguf for dietpi; chown dietpi:dietpi ~dietpi/models',
            'bad uri file:///home/Jane Doe/private/a.bin',
            'keep 127.0.0.1:8080/x and https://x.org/a/b and and/or',
            'colour \x1b[31mred\u200b text',
            "x" * 520 + ' /home/Jane Doe/end',
        ],
    },
}


@pytest.mark.parametrize("name", sorted(scrubbers()))
def test_each_scrubber_keeps_its_policy(name, home):
    fn = scrubbers()[name]
    expected = GOLDEN[home][name]
    assert len(expected) == len(CASES)
    for text, want in zip(CASES, expected):
        assert fn(text) == want, (name, home, text)


# --- one implementation: orchestrator/security/paths.py -------------------------------------------------------------

def test_the_old_names_are_the_one_implementation():
    """tools_runtime keeps the old names (callers and tests import them there); each is the object in
    security.paths, the one implementation the Core, the utils and the tools use."""
    from orchestrator.security import paths
    for name in ("label_paths", "shorten_home", "home_folders", "replace_folder", "device_text", "DEVICE_ACCOUNT",
                 "_undouble", "scrub_for_ai"):
        assert getattr(tools_runtime, name) is getattr(paths, name), name
    assert paths.undouble("'C:\\\\Users\\\\x'") == paths._undouble("'C:\\\\Users\\\\x'") == "'C:\\Users\\x'"


def test_scrub_for_ai_is_the_status_scrub_and_with_roots_the_runtime_tools_scrub(home):
    from orchestrator.security.paths import label_paths, scrub_for_ai, shorten_home
    from orchestrator.utils.helpers import redact_secrets
    from orchestrator.utils.untrusted import sanitize_untrusted
    for text in CASES:
        assert scrub_for_ai(text) == tools_status._scrub_paths(text) == \
            sanitize_untrusted(redact_secrets(shorten_home(text)), 500)
        assert scrub_for_ai(text, ROOTS) == \
            sanitize_untrusted(redact_secrets(shorten_home(label_paths(text, ROOTS))), 500)
        assert scrub_for_ai(text, limit=40) == sanitize_untrusted(redact_secrets(shorten_home(text)), 40)


def test_the_home_patterns_are_shared():
    """The GUI tool's form values, the pack export and the privacy masks build their home-folder patterns from the
    shared parts (each keeps its policy: the outputs above are unchanged)."""
    from orchestrator.security.paths import ACCOUNT_NAME, HOME_ROOT
    assert HOME_ROOT in tools_gui.HOME_FOLDERS.pattern and ACCOUNT_NAME in tools_gui.HOME_FOLDERS.pattern
    assert HOME_ROOT in community_pack._HOME_DIRS.pattern and ACCOUNT_NAME in community_pack._HOME_DIRS.pattern
    assert privacy._HOME.pattern.endswith(f"({ACCOUNT_NAME})")


def test_the_gui_tool_hides_this_computers_home_folder_in_every_spelling(monkeypatch):
    """Security (chain c1-paths): the GUI tool's _no_home compared only Path.home() letter by letter, so a home folder
    named by HOME (Git Bash, MSYS) or USERPROFILE, with the other separator, in another case or doubled as Python
    quotes a path reached the AI with the person's account name. It now uses security.paths.shorten_home."""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane Doe")
    monkeypatch.setenv("HOME", "/c/Users/Jane Doe")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: PureWindowsPath(r"C:\Users\Jane Doe")))
    for text in ("c:/users/jane doe/models/x.gguf", "C:/Users/Jane Doe/x", "/c/Users/Jane Doe/src",
                 "error: " + repr(r"C:\Users\Jane Doe\x.bin"), r"C:\Users\Jane Doe"):
        out = tools_gui._no_home(text)
        assert "jane" not in out.lower() and out.count("~") == 1, (text, out)


# --- D0 default of 2026-10-17: file URLs and account names with spaces ----------------------------------------------

FILE_URLS = (
    ("bad uri file:///home/bob/private/a.bin", "bad uri file://a.bin"),
    ("file:///home/Bob%20Smith/a.bin and file:///home/Bob%20Smith", "file://a.bin and file://a home folder"),
    ("see file:///C:/Users/Bob%20Smith/m.gguf or file:///C:/Users/Bob", "see file://m.gguf or file://a home folder"),
    ("file://localhost/home/carol/x.gguf.", "file://x.gguf."),
    ("share file://fs/Users/frank/m.gguf and file://fs/Users/frank", "share file://m.gguf and file://a home folder"),
    ("url 'file:///home/dave/x.bin'", "url 'file://x.bin'"),
    ("in file:///srv/my out/Rk/m.gguf", "in file://output:Rk/m.gguf"),
    # a version-control file URL (pip's and uv's 'git+file:///...' in an install log) is a file URL too
    ("pip install git+file:///home/bob/pkg failed", "pip install git+file://pkg failed"),
    ("checkout svn+file:///home/bob/repo/trunk.", "checkout svn+file://trunk."),
    ("hg+file:///C:/Users/Bob%20Smith/m and bzr+file://localhost/home/eve/x", "hg+file://m and bzr+file://x"),
    ("no path: file:// alone", "no path: file:// alone"),
    ("keep https://x.org/file:///a", "keep https://x.org/file:///a"),
)


@pytest.mark.parametrize("text, want", FILE_URLS)
def test_a_path_inside_a_file_url_is_labelled_like_any_other(home, text, want):
    """Another account's home inside a file:/// URL reached an AI whole; its path is now labelled (a known folder by
    its label, any other path by its file name, a bare home folder as 'a home folder'), the URL keeps 'file://'."""
    assert tools_runtime.label_paths(text, ROOTS) == want
    assert tools_runtime.device_text(text, DEVICE_USERS, ROOTS) == want


ACCOUNTS = (
    (r"denied: C:\Users\Bob Smith", "denied: a home folder"),
    (r"denied: C:\Users\Bob Smith could not be read", "denied: a home folder could not be read"),
    (r"C:\Users\Bob Smith Jr\x.gguf missing", "x.gguf missing"),
    (r"open \\fs\Users\Ann Lee 2 now", "open a home folder now"),
    ("WSL: /mnt/c/Users/Ann Lee and more", "WSL: a home folder and more"),
    (r"C:\Users\bob could not", "a home folder could not"),
    ("/home/pi Failed to start", "a home folder Failed to start"),
)


@pytest.mark.parametrize("text, want", ACCOUNTS)
def test_another_accounts_name_with_a_space_is_hidden_whole(home, text, want):
    """A Windows profile folder keeps the spaces of the person's name: the words after another account's Windows home
    that start with a capital letter or a digit belong to it (before, 'C:\\Users\\Bob Smith' left 'Smith' for the AI).
    A POSIX account name has no space, so '/home/pi Failed' keeps the next word; a lower-case word ends the name."""
    assert tools_runtime.label_paths(text, ROOTS) == want
    assert tools_runtime.device_text(text, DEVICE_USERS, ROOTS) == want


def test_this_computers_account_name_is_hidden_whole_in_any_root(monkeypatch):
    """This computer's account name ('Jane Doe' of C:\\Users\\Jane Doe) is taken whole after any root, also a POSIX one
    another tool writes ('/home/Jane Doe here'), so neither of its words is left."""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane Doe")
    monkeypatch.setenv("HOME", r"C:\Users\Jane Doe")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: PureWindowsPath(r"C:\Users\Jane Doe")))
    for text, want in (("home is /home/Jane Doe here", "home is a home folder here"),
                       ("wsl /mnt/c/Users/Jane Doe/x.gguf", "wsl x.gguf"),
                       ("bad uri file:///home/Jane Doe/private/a.bin", "bad uri file://a.bin")):
        assert tools_runtime.label_paths(text, ROOTS) == want, text
        assert "Doe" not in tools_runtime.device_text(text, DEVICE_USERS, ROOTS), text


# --- the problem report (utils/telemetry.py) uses the one home-folder scrub ----------------------------------------

def telemetry_sanitize(text):
    from orchestrator.utils.telemetry import TelemetryManager
    return TelemetryManager(SimpleNamespace(config={}))._sanitize(text)


def test_the_problem_report_keeps_its_markers(home, monkeypatch):
    """Characterization: secrets redacted, this computer's home folder as '~USER', the account name after a separator
    as 'USER' (names of three characters or more), an empty text stays empty."""
    folder = "/home/Jane Doe" if home == "posix" else r"C:\Users\Jane Doe"
    sep = "/" if home == "posix" else "\\"
    text = f"key sk-proj-AbCdEf0123456789_xyz-ABCDEFGHIJ in {folder}{sep}project and /srv/jdoe/x"
    out = telemetry_sanitize(text)
    assert "sk-proj-AbCdEf" not in out and f"~USER{sep}project" in out and "/srv/USER/x" in out
    assert "Jane" not in out and telemetry_sanitize("") == ""


def test_the_problem_report_hides_the_home_folder_in_every_spelling(monkeypatch):
    """Security (R0, the Stage-B leftover): _sanitize replaced only os.path.expanduser('~') letter by letter, so a home
    folder named by USERPROFILE, with the other separator, in another case or doubled as Python quotes a path stayed in
    the report. It now uses security.paths (home_folders, replace_folder, undouble), keeping its '~USER' marker."""
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane Doe")
    monkeypatch.setenv("HOME", "/c/Users/Jane Doe")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: PureWindowsPath(r"C:\Users\Jane Doe")))
    for text in ("c:/users/jane doe/models/x.gguf", "C:/Users/Jane Doe/x", "/c/Users/Jane Doe/src",
                 "error: " + repr(r"C:\Users\Jane Doe\x.bin"), r"C:\Users\Jane Doe"):
        out = telemetry_sanitize(text)
        assert "jane" not in out.lower() and out.count("~USER") == 1, (text, out)
