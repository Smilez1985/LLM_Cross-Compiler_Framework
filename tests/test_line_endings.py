"""Files that run or are read on a board or in a Linux container keep LF line endings on every host. .gitattributes
keeps the repository's files LF also in a Windows checkout (Git for Windows checks text out with CRLF by default), and
the files the framework writes are written with LF also on Windows: windows_newlines() shows on any host what a Windows
host would write (text mode there turns every "\\n" into CRLF unless newline= says otherwise)."""

import builtins
import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT, windows_newlines

TEMPLATE = REPO_ROOT / "targets" / "_template"


def crlf_files(folder: Path):
    """Text files below `folder` with a CR in them (files with a NUL byte count as binary)."""
    found = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        data = path.read_bytes()
        if b"\0" not in data and b"\r" in data:
            found.append(path.relative_to(folder).as_posix())
    return found


def test_the_simulation_writes_crlf_where_a_windows_host_would(tmp_path):
    real = builtins.open
    with windows_newlines():
        (tmp_path / "default.txt").write_text("a\nb\n")
        (tmp_path / "lf.txt").write_text("a\nb\n", newline="\n")
        with open(tmp_path / "open.txt", "w", encoding="utf-8") as f:
            f.write("a\n")
        # O_BINARY: without it a descriptor is in text mode on Windows and the C runtime adds a second CR (\r\r\n)
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(tmp_path / "fd.txt", flags, 0o600), "w") as f:
            f.write("a\n")
        (tmp_path / "bytes.bin").write_bytes(b"a\n")
    assert (tmp_path / "default.txt").read_bytes() == b"a\r\nb\r\n"
    assert (tmp_path / "open.txt").read_bytes() == (tmp_path / "fd.txt").read_bytes() == b"a\r\n"
    assert (tmp_path / "lf.txt").read_bytes() == b"a\nb\n" and (tmp_path / "bytes.bin").read_bytes() == b"a\n"
    assert builtins.open is real and io.open is real, "the simulation ends with the block"


def test_a_generated_target_module_is_lf_on_every_host(tmp_path):
    """build.sh, the Dockerfiles, modules/*.sh and target.yml run or are read in the Linux build container: a Windows
    host wrote them with CRLF, and bash stopped at the first line ("$'\\r': command not found")."""
    from orchestrator.Core.module_generator import ModuleGenerator
    from orchestrator.tools.tools_targets import mark_created_under
    targets = tmp_path / "targets"
    targets.mkdir()
    generator = ModuleGenerator(targets)
    generator.template_dir = TEMPLATE
    data = dict(module_name="My Board", architecture="aarch64", sdk="none", base_os="debian:bookworm-slim",
                packages=["build-essential", "cmake"], cpu_flags="", cmake_flags="", setup_commands="",
                detection_commands="lscpu", quantization_logic='    Q4_K_M) build_gguf "$QUANT_TYPE" ;;')
    with windows_newlines():
        folder = Path(generator.generate_module(data))
        mark_created_under(folder, "grant:g_" + "1" * 16, "a" * 64)
    written = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    assert {"modules/build.sh", "Dockerfile", "target.yml"} <= written
    assert crlf_files(folder) == []
    assert yaml.safe_load((folder / "target.yml").read_text(encoding="utf-8"))["metadata"]["created_under"]


def test_the_calibration_text_is_lf_on_every_host(tmp_path):
    """llama-imatrix reads it in the build container: with CRLF from a Windows host it computed the importance matrix
    from other text than on Linux."""
    from orchestrator.utils.calibration import build_calibration_text, write_calibration_dataset
    with windows_newlines():
        path = write_calibration_dataset(tmp_path / "calibration.txt", "mixed")
    assert path.read_bytes() == build_calibration_text("mixed").encode("utf-8")


def test_the_device_tarball_checksum_file_is_lf_on_every_host(tmp_path):
    from orchestrator.runtime.device.package import package_runtime
    from test_device_runtime import LICENCE, stored_build
    source = tmp_path / "src"
    source.mkdir()
    (source / "CMakeLists.txt").write_bytes(b"project(llama.cpp)\n")
    (source / "LICENSE").write_bytes(LICENCE.encode("utf-8"))
    store, build = stored_build(tmp_path / "state", source)
    with windows_newlines():
        packaged = package_runtime(store, build, licence=source / "LICENSE", out_dir=tmp_path / "out")
    assert packaged.path.with_name(packaged.path.name + ".sha256").read_bytes() == \
        f"{packaged.sha256}  {packaged.path.name}\n".encode("ascii")


def test_the_server_key_file_is_written_through_a_binary_descriptor(tmp_path, monkeypatch):
    """llama-server reads its API key line by line from this file, in a Linux container also when the host is Windows
    (the file is mounted into it). os.open() gives a text-mode descriptor on Windows unless O_BINARY is set: the key
    line ended in \\r\\n despite newline="\\n", the server took the CR as part of the key and refused every request."""
    from orchestrator.runtime import server_policy
    binary = getattr(os, "O_BINARY", None)
    if binary is None:  # POSIX: Windows' flag stands in as a bit no POSIX flag uses (removed before the real open)
        binary = 1 << 28
        monkeypatch.setattr(os, "O_BINARY", binary, raising=False)
    real_open, seen = os.open, []

    def recording(path, flags, mode=0o777, *args, **kwargs):
        seen.append(flags)
        return real_open(path, flags & ~(1 << 28), mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording)
    path, key = server_policy.write_key_file(tmp_path)
    assert seen and all(flags & binary for flags in seen), "a text-mode descriptor on Windows"
    assert path.read_bytes() == key.encode("ascii") + b"\n"


# --- the repository's own files ---------------------------------------------------------------------------------------

def _git(*args):
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def tracked():
    if shutil.which("git") is None:
        pytest.skip("needs git")
    listing = _git("ls-files", "-z")
    if listing.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in listing.stdout.split("\0") if p]


def _attributes(paths):
    """{path: {attribute: value}} for text and eol, from git check-attr (what a checkout does with each file)."""
    result = subprocess.run(["git", "-C", str(REPO_ROOT), "check-attr", "-z", "--stdin", "text", "eol"],
                            input="\0".join(paths) + "\0", capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    fields = result.stdout.split("\0")
    found = {}
    for i in range(0, len(fields) - 2, 3):
        found.setdefault(fields[i], {})[fields[i + 1]] = fields[i + 2]
    return found


WINDOWS_ONLY = (".bat", ".cmd", ".ps1")


def test_every_text_file_is_checked_out_with_lf_except_windows_scripts(tracked):
    """Shell scripts, Dockerfiles, entrypoints, git hooks, LICENSE files shipped into packages, the targets' scripts and
    the test fixtures compared byte for byte: LF in every checkout, Windows' default core.autocrlf=true included."""
    attributes = _attributes(tracked)
    wrong = []
    for path in tracked:
        attrs = attributes.get(path, {})
        if attrs.get("text") == "unset":
            continue  # binary
        expected = "crlf" if path.lower().endswith(WINDOWS_ONLY) else "lf"
        if attrs.get("eol") != expected:
            wrong.append(f"{path}: eol={attrs.get('eol')} (expected {expected})")
    assert wrong == []


def test_files_that_run_on_a_board_or_in_a_container_are_named_by_the_rules(tracked):
    """The explicit rules, not only the catch-all, cover what a board or a container runs."""
    attributes = _attributes(tracked)
    must = [p for p in tracked if p.endswith((".sh", ".Dockerfile", ".dockerfile")) or
            Path(p).name.startswith(("Dockerfile", "LICENSE", "entrypoint")) or p.startswith(".githooks/") or
            Path(p).name == "dockerfile"]
    assert len(must) > 20
    assert [p for p in must if attributes[p].get("text") != "set" or attributes[p].get("eol") != "lf"] == []
