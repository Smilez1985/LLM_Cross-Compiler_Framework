"""Stub tools of a target image for the tests of the config and target stages (the compiler test, the package for the
board): the compilers of the aarch64 cross toolchain and the image's own gcc and g++, cmake and make.

- toolchain/: aarch64-linux-gnu-gcc, -g++, -ar, -strip, gcc and g++. A compiler writes the file named with -o: for the
  cross compilers an ELF header of an aarch64 program (machine 183), for gcc and g++ a small shell program that runs
  on the test host (the native x86_64 plan runs its test program). STUB_CC_MACHINE (a number) makes every compiler
  write an ELF header of that machine instead; STUB_FAIL=cc makes every compiler fail with a compiler's message.
- buildtools/: cmake and make. `cmake -P FILE` (the config stage's check of the toolchain file) succeeds unless
  STUB_FAIL=cmake-P; a configure call does nothing. make writes an ELF program of STUB_MAKE_MACHINE (default 183,
  aarch64) for each target it is given, in bin/ of the folder it runs in (the target stage's build folder).
Every tool logs its call to STUB_TOOLS_LOG when that is set (never to the calls log the characterization tests
compare).
"""

import sys
from pathlib import Path

_LOG = """\
import os, sys
a = sys.argv
log = os.environ.get('STUB_TOOLS_LOG')
if log:
    with open(log, 'a') as f:
        f.write(os.path.basename(a[0]) + ' ' + ' '.join(a[1:]) + '\\n')
"""

_ELF = """\
def elf(path, machine):
    head = b'\\x7fELF' + bytes([2, 1, 1]) + bytes(9) + (2).to_bytes(2, 'little') + int(machine).to_bytes(2, 'little')
    with open(path, 'wb') as f:
        f.write(head + bytes(44))
    os.chmod(path, 0o755)
"""

COMPILER = _LOG + _ELF + """\
if os.environ.get('STUB_FAIL') == 'cc':
    sys.stderr.write("cc1: error: unknown value 'cortex-a99' for '-mcpu'\\n")
    sys.exit(1)
out = a[a.index('-o') + 1]
machine = os.environ.get('STUB_CC_MACHINE') or MACHINE
if machine == 'native':
    with open(out, 'w') as f:
        f.write('#!/bin/sh\\necho compiler test 3\\n')
    os.chmod(out, 0o755)
else:
    elf(out, machine)
"""

NOOP = _LOG

CMAKE = _LOG + """\
if '-P' in a and os.environ.get('STUB_FAIL') == 'cmake-P':
    sys.stderr.write('CMake Error at toolchain.cmake:3:\\n  Parse error.  Expected a command name, got unquoted argument.\\n')
    sys.exit(1)
"""

MAKE = _LOG + _ELF + """\
os.makedirs('bin', exist_ok=True)
for name in [x for x in a[1:] if not x.startswith('-')]:
    elf(os.path.join('bin', name), os.environ.get('STUB_MAKE_MACHINE', '183'))
"""


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def install_toolchain(root: Path):
    """The stub tools below `root`: (toolchain folder, build tools folder), both to be put first on PATH."""
    toolchain, buildtools = Path(root) / "toolchain", Path(root) / "buildtools"
    for name in ("aarch64-linux-gnu-gcc", "aarch64-linux-gnu-g++"):
        _script(toolchain / name, "MACHINE = '183'\n" + COMPILER)
    for name in ("gcc", "g++", "cc", "c++"):
        _script(toolchain / name, "MACHINE = 'native'\n" + COMPILER)
    for name in ("aarch64-linux-gnu-ar", "aarch64-linux-gnu-strip"):
        _script(toolchain / name, NOOP)
    _script(buildtools / "cmake", CMAKE)
    _script(buildtools / "make", MAKE)
    return toolchain, buildtools
