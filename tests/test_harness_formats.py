"""Every common format of a harness taken in (orchestrator/runtime/bundle/harness.py, orchestrator/utils/archives.py):
the format by content, never by name; zip, tar and its compressed kinds and a single compressed file through the
standard library; 7z, iso, cpio, rar and disk images through 7-Zip (those tests run only where 7-Zip and the tool that
makes the fixture are on this computer); a script or a program as a harness of one file; the archive pin is always the
file's sha256. The listing parser and its refusals are tested on a recorded `7z l -slt -ba` text without 7-Zip. The
content rule for a host without executable bits (forced here) gives the same listing as a Linux file system's modes,
and a plain folder on Windows is taken in."""

import bz2
import gzip
import io
import json
import lzma
import os
import shutil
import struct
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import harness as h
from orchestrator.utils import archives as ar
from orchestrator.utils.helpers import UnsafeArchive

DATA = Path(__file__).parent / "data"
POSIX = pytest.mark.skipif(os.name == "nt", reason="sets a file's mode with chmod, which Windows does not keep")
SEVEN = ar.find_7zip()
NEEDS_7Z = pytest.mark.skipif(SEVEN is None, reason="7-Zip (7zz, 7z or 7za) is not on this computer")
SCRIPT = b"#!/bin/sh\necho ok\n"
ELF = b"\x7fELF\x02\x01\x01" + bytes(57)


def spec(**kw):
    base = dict(id="demo", title="Demo", kind="archive", location="", installer="installer/install.sh")
    base.update(kw)
    return h.HarnessSpec(**base)


def members():
    return [("installer/install.sh", SCRIPT, 0o755), ("src/app.py", b"print('hi')\n", 0o644)]


def tar_bytes(mode="w") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data, perm in members():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), perm
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def tree(root: Path) -> Path:
    for name, data, perm in members():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
        if os.name != "nt":
            (root / name).chmod(perm)
    return root


def kinds(taken):
    return {r[0]: r[1] for r in h.stored_listing(taken.path)}


# --- the format by content -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
def test_tar_and_its_compressed_kinds_under_any_name(tmp_path, mode):
    archive = tmp_path / "harness.zip"    # a misleading name: the content decides
    archive.write_bytes(tar_bytes(mode))
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "src/app.py": "f"}
    assert taken.pin == h._sha256_file(archive) and taken.format.startswith("tar")
    assert not taken.content_rule and "modes_derived_from_content" not in taken.record()


def test_a_zip_without_unix_modes_takes_the_content_rule(tmp_path):
    archive = tmp_path / "made-on-windows.bin"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data, _perm in members() + [("tools/helper", ELF, 0)]:
            info = zipfile.ZipInfo(name)
            info.create_system, info.external_attr = 0, 0x20   # made on Windows: DOS attributes, no unix mode
            zf.writestr(info, data)
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "src/app.py": "f", "tools/helper": "x"}
    assert taken.content_rule and taken.derived == ("installer/install.sh", "tools/helper")
    assert taken.record()["modes_derived_from_content"] == 2 and "tools/helper" not in json.dumps(taken.record())


@pytest.mark.parametrize("compress", [gzip.compress, bz2.compress, lzma.compress])
def test_a_single_compressed_script_is_a_harness_of_one_file(tmp_path, compress):
    archive = tmp_path / "setup.sh.gz"
    archive.write_bytes(compress(SCRIPT))
    taken = h.take_in(spec(location=str(archive), installer="setup.sh"), tmp_path / "state")
    assert kinds(taken) == {"setup.sh": "x"} and taken.files == 1
    with pytest.raises(h.HarnessError, match="give the installer setup.sh"):
        h.take_in(spec(location=str(archive)), tmp_path / "state2")


def test_a_compressed_file_that_holds_no_tar_and_no_program_is_refused(tmp_path):
    archive = tmp_path / "notes.txt.gz"
    archive.write_bytes(gzip.compress(b"just text\n"))
    with pytest.raises(h.HarnessError, match="neither a tar archive nor a script"):
        h.take_in(spec(location=str(archive), installer="notes.txt"), tmp_path / "state")


@pytest.mark.parametrize("content", [SCRIPT, ELF], ids=["makeself-script", "elf"])
def test_a_bin_that_is_a_script_or_a_program_is_a_harness_of_one_file(tmp_path, content):
    archive = tmp_path / "harness-installer.bin"
    archive.write_bytes(content)
    taken = h.take_in(spec(location=str(archive), installer="harness-installer.bin"), tmp_path / "state")
    assert kinds(taken) == {"harness-installer.bin": "x"} and taken.pin == h._sha256_file(archive)
    assert (taken.path / "harness-installer.bin").read_bytes() == content
    with pytest.raises(h.HarnessError, match="that file itself"):
        h.take_in(spec(location=str(archive), installer="installer/install.sh"), tmp_path / "state2")


def test_a_file_that_is_nothing_llmcf_reads_is_refused_by_its_content(tmp_path):
    archive = tmp_path / "harness.tar.gz"
    archive.write_bytes(b"plain words, no archive\n" * 40)
    with pytest.raises(h.HarnessError, match="read from the file's content"):
        h.take_in(spec(location=str(archive)), tmp_path / "state")


def test_sniff_reads_magic_bytes(tmp_path):
    cases = {"7z": b"7z\xbc\xaf\x27\x1c" + bytes(30), "rar": b"Rar!\x1a\x07\x01\x00" + bytes(30),
             "zstd": b"\x28\xb5\x2f\xfd" + bytes(30), "cpio": b"070701" + bytes(30),
             "iso": bytes(0x8001) + b"CD001" + bytes(10), "disk": bytes(510) + b"\x55\xaa" + bytes(600),
             "squashfs": b"hsqs" + bytes(30), "macho": b"\xcf\xfa\xed\xfe" + bytes(30), "elf": ELF,
             "script": SCRIPT, "tar": tar_bytes()}
    for kind, data in cases.items():
        (tmp_path / kind).write_bytes(data)
        assert ar.sniff(tmp_path / kind) == kind, kind
    (tmp_path / "none").write_bytes(b"hello")
    assert ar.sniff(tmp_path / "none") is None


def test_without_7zip_the_refusal_names_the_format_and_the_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "find_7zip", lambda: None)
    archive = tmp_path / "harness.zip"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c" + bytes(64))
    with pytest.raises(h.HarnessError) as e:
        h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert str(e.value).startswith("A 7z archive is read with 7-Zip") and "installer again" in str(e.value)


# --- 7-Zip's listing, parsed and checked without 7-Zip ----------------------------------------------------------------

def recorded() -> str:
    return (DATA / "sevenzip_listing.txt").read_text(encoding="utf-8")


def test_the_recorded_listing_is_parsed_with_modes_and_links():
    found = {e.path: e for e in ar.entries(ar.parse_blocks(recorded()))}
    assert set(found) == {"installer", "installer/install.sh", "lnk", "a.txt"}
    assert found["installer/install.sh"].mode == 0o755 and found["installer/install.sh"].kind == "file"
    assert found["a.txt"].mode == 0o644 and found["installer"].kind == "dir"
    assert found["lnk"].kind == "link" and found["lnk"].link == "a.txt"
    assert ar.check_caps(found.values(), max_files=10, max_bytes=100) == 26
    with pytest.raises(UnsafeArchive, match="more than 2 files"):
        ar.check_caps(found.values(), max_files=2, max_bytes=100)


def with_entry(block: str) -> str:
    return recorded().rstrip("\n") + "\n\n" + block.strip("\n") + "\n"


@pytest.mark.parametrize("block, words", [
    ("Path = ../../etc/cron.d/x\nFolder = -\nSize = 1\nMode = -rw-r--r--", "out of the folder"),
    ("Path = /etc/passwd\nFolder = -\nSize = 1\nMode = -rw-r--r--", "absolute path"),
    ("Path = C:\\Windows\\x\nFolder = -\nSize = 1", "absolute path"),
    ("Path = out\nFolder = -\nSize = 0\nMode = lrwxrwxrwx\nSymbolic Link = ../../../etc", "link out"),
    ("Path = abs\nFolder = -\nSize = 0\nMode = lrwxrwxrwx\nSymbolic Link = /etc/shadow", "link out"),
    ("Path = dev/sda\nFolder = -\nSize = 0\nMode = brw-rw----", "device"),
    ("Path = fifo\nFolder = -\nSize = 0\nMode = prw-r--r--", "device, FIFO"),
    ("Path = hard\nFolder = -\nSize = 0\nMode = -rw-r--r--\nHard Link = a.txt", "hard link"),
    ("Path = x\nFolder = -\nPath = ../evil", "twice"),
    ("Folder = -\nSize = 1", "no path"),
])
def test_a_crafted_listing_is_refused(block, words):
    with pytest.raises(UnsafeArchive, match=words):
        ar.entries(ar.parse_blocks(with_entry(block)))


def test_unix_modes_in_7zips_hex_attributes_and_windows_letters():
    hexed = ar.entries(ar.parse_blocks("Path = run\nAttributes = 0x81ED8020\n\nPath = plain\nAttributes = A\n"))
    assert hexed[0].mode == 0o755 and hexed[1].mode is None and hexed[1].kind == "file"
    assert ar.is_partition_listing(ar.entries(ar.parse_blocks(
        "Path = 0.fat\nSize = 7\nFile System = FAT32-LBA\nOffset = 1048576\n\nPath = 1.img\nSize = 7\nOffset = 9\n")))


# --- formats through 7-Zip (fixtures made with 7-Zip and the system's own tools at test time) -------------------------

def seven(*args, cwd=None):
    subprocess.run([SEVEN, *args], cwd=cwd, check=True, capture_output=True)


@NEEDS_7Z
@POSIX
def test_a_7z_archive_keeps_modes_and_links(tmp_path):
    src = tree(tmp_path / "src")
    os.symlink("app.py", src / "src" / "current.py")
    archive = tmp_path / "harness.dat"
    seven("a", "-snl", str(archive), "installer", "src", cwd=src)
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "src/app.py": "f", "src/current.py": "l"}
    assert taken.format == "7z" and taken.pin == h._sha256_file(archive)
    plain = h.take_in(spec(kind="folder", location=str(src)), tmp_path / "folder")
    assert plain.tree_sha256 == taken.tree_sha256


@NEEDS_7Z
@POSIX
def test_a_7z_archive_with_a_link_out_is_refused(tmp_path):
    src = tree(tmp_path / "src")
    os.symlink("../../outside", src / "src" / "out")
    archive = tmp_path / "harness.7z"
    seven("a", "-snl", str(archive), "installer", "src", cwd=src)
    with pytest.raises(h.HarnessError):
        h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert not any((tmp_path / "state" / "harnesses" / "demo").glob("*/tree"))


def newc_cpio(entries) -> bytes:
    out = b""
    for i, (name, data, mode) in enumerate(entries + [("TRAILER!!!", b"", 0)]):
        raw = name.encode() + b"\0"
        fields = [i + 1, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw), 0]
        head = b"070701" + b"".join(b"%08X" % f for f in fields) + raw
        out += head + b"\0" * (-len(head) % 4) + data + b"\0" * (-len(data) % 4)
    return out


@NEEDS_7Z
def test_a_cpio_archive_through_7zip(tmp_path):
    archive = tmp_path / "harness.img"
    archive.write_bytes(newc_cpio([("installer", b"", 0o040755)]
                                  + [(n, d, 0o100000 | p) for n, d, p in members()]))
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "src/app.py": "f"} and taken.format == "cpio"


@NEEDS_7Z
@pytest.mark.skipif(not (shutil.which("xorriso") or shutil.which("genisoimage") or shutil.which("mkisofs")),
                    reason="no xorriso, genisoimage or mkisofs to make an ISO image")
def test_an_iso_image_through_7zip(tmp_path):
    src = tree(tmp_path / "src")
    archive = tmp_path / "harness.iso"
    if shutil.which("xorriso"):
        cmd = ["xorriso", "-as", "mkisofs", "-R", "-o", str(archive), str(src)]
    else:
        cmd = [shutil.which("genisoimage") or shutil.which("mkisofs"), "-R", "-o", str(archive), str(src)]
    subprocess.run(cmd, check=True, capture_output=True)
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert kinds(taken)["installer/install.sh"] == "x" and taken.format == "iso"


@NEEDS_7Z
@pytest.mark.skipif(shutil.which("rar") is None, reason="RAR archives are made only by the rar program, not here")
def test_a_rar_archive_through_7zip(tmp_path):
    src = tree(tmp_path / "src")
    archive = tmp_path / "harness.rar"
    subprocess.run(["rar", "a", "-ol", str(archive), "installer", "src"], cwd=src, check=True, capture_output=True)
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert "installer/install.sh" in kinds(taken) and taken.format == "rar"


def can_read_zstd() -> bool:
    if shutil.which("zstd") is None:
        return False
    return ar.ZSTD_STDLIB or SEVEN is not None


@pytest.mark.skipif(not can_read_zstd(), reason="no zstd program to make the fixture, or neither Python 3.14 nor "
                                                "7-Zip to read it")
def test_a_zstd_tar(tmp_path):
    plain = tmp_path / "harness.tar"
    plain.write_bytes(tar_bytes())
    archive = tmp_path / "harness.tar.zst"
    subprocess.run(["zstd", "-q", str(plain), "-o", str(archive)], check=True)
    try:
        taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    except h.HarnessError as e:
        if "zstd" in str(e) and not ar.ZSTD_STDLIB:
            pytest.skip(f"this 7-Zip reads no zstd: {e}")
        raise
    assert kinds(taken) == {"installer/install.sh": "x", "src/app.py": "f"}


MTOOLS = shutil.which("mkfs.fat") and shutil.which("mcopy") and shutil.which("mmd")
NEEDS_FAT = pytest.mark.skipif(not MTOOLS, reason="mkfs.fat and mtools make the FAT images")


def fat_partition(image: Path, offset_sectors: int, sectors: int, with_installer: bool) -> None:
    subprocess.run(["mkfs.fat", "--offset", str(offset_sectors), str(image), str(sectors // 2)], check=True,
                   capture_output=True)
    env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
    target = f"{image}@@{offset_sectors * 512}"
    if with_installer:
        subprocess.run(["mmd", "-i", target, "::installer"], check=True, capture_output=True, env=env)
    folder = image.parent / f"files-{offset_sectors}"
    tree(folder)
    if with_installer:
        subprocess.run(["mcopy", "-i", target, str(folder / "installer" / "install.sh"), "::installer/"], check=True,
                       capture_output=True, env=env)
    subprocess.run(["mcopy", "-i", target, str(folder / "src" / "app.py"), "::app.py"], check=True,
                   capture_output=True, env=env)


def mbr_image(path: Path, parts) -> Path:
    """A raw disk image with an MBR and FAT partitions: parts are (start sector, sectors, holds the installer)."""
    end = max(s + n for s, n, _ in parts)
    path.write_bytes(bytes(end * 512))

    def chs(lba):
        c, r = divmod(lba, 16 * 63)
        hd, s = divmod(r, 63)
        return bytes([hd, ((c >> 2) & 0xC0) | (s + 1), c & 0xFF])
    with open(path, "r+b") as fh:
        for i, (start, n, _) in enumerate(parts):
            fh.seek(446 + 16 * i)
            fh.write(bytes([0]) + chs(start) + bytes([0x0C]) + chs(start + n - 1) + struct.pack("<II", start, n))
        fh.seek(510)
        fh.write(b"\x55\xaa")
    for start, n, with_installer in parts:
        fat_partition(path, start, n, with_installer)
    return path


@NEEDS_7Z
@NEEDS_FAT
def test_a_disk_image_takes_the_partition_with_the_installer_and_the_content_rule(tmp_path):
    image = mbr_image(tmp_path / "board.img", [(2048, 14336, False), (16384, 16384, True)])
    taken = h.take_in(spec(location=str(image)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "app.py": "f"}   # FAT keeps no modes: the content rule
    assert taken.content_rule and taken.derived == ("installer/install.sh",) and taken.format == "disk"
    assert taken.pin == h._sha256_file(image)


@NEEDS_7Z
@NEEDS_FAT
def test_a_disk_image_with_the_installer_twice_needs_image_partition(tmp_path):
    image = mbr_image(tmp_path / "board.img", [(2048, 14336, True), (16384, 16384, True)])
    with pytest.raises(h.HarnessError, match="partitions 0 and 1 of the disk image both hold"):
        h.take_in(spec(location=str(image)), tmp_path / "state")
    taken = h.take_in(spec(location=str(image), image_partition=1), tmp_path / "state")
    assert "installer/install.sh" in kinds(taken)
    with pytest.raises(h.HarnessError, match="0 to 1"):
        h.take_in(spec(location=str(image), image_partition=5), tmp_path / "state")


@NEEDS_7Z
@pytest.mark.skipif(shutil.which("mkfs.ext4") is None, reason="mkfs.ext4 makes the ext4 image")
@POSIX
def test_an_ext4_image_keeps_its_modes(tmp_path):
    src = tree(tmp_path / "src")
    image = tmp_path / "rootfs.bin"
    subprocess.run(["truncate", "-s", "4M", str(image)], check=True)
    subprocess.run(["mkfs.ext4", "-q", "-O", "^has_journal", "-d", str(src), str(image)], check=True,
                   capture_output=True)
    taken = h.take_in(spec(location=str(image)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "src/app.py": "f"} and not taken.content_rule


def test_image_partition_belongs_to_an_archive_only():
    with pytest.raises(h.HarnessError, match="image_partition"):
        h.check_spec(spec(kind="folder", location="x", image_partition=0))
    with pytest.raises(h.HarnessError, match="image_partition"):
        h.check_spec(spec(image_partition=-1))
    assert h.check_spec(spec(image_partition=2)).image_partition == 2


# --- the content rule where the host keeps no executable bit (Windows; forced here) ---------------------------------

@POSIX
def test_the_content_rule_gives_the_listing_of_the_real_modes_for_a_shebang_and_elf_tree(tmp_path, monkeypatch):
    src = tree(tmp_path / "src")
    (src / "bin").mkdir()
    (src / "bin" / "tool").write_bytes(ELF)
    (src / "bin" / "tool").chmod(0o755)
    real = h.tree_listing(src)
    modes, derived = h.content_modes(src, "installer/install.sh")
    assert h.tree_listing(src, modes) == real and derived == ("bin/tool", "installer/install.sh")
    plain = h.take_in(spec(kind="folder", location=str(src)), tmp_path / "a")
    monkeypatch.setattr(h, "NO_EXEC_BIT", True)
    for p in ("bin/tool", "installer/install.sh"):
        (src / p).chmod(0o644)   # what Windows shows: no bit in the file system
    taken = h.take_in(spec(kind="folder", location=str(src)), tmp_path / "b")
    assert taken.tree_sha256 == plain.tree_sha256 and taken.content_rule
    assert json.loads((taken.path.parent / h.MODES_FILE).read_text())["executable"] == ["bin/tool",
                                                                                         "installer/install.sh"]


def test_a_plain_folder_on_windows_is_taken_in_with_the_content_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "NO_EXEC_BIT", True)
    src = tmp_path / "src"
    (src / "installer").mkdir(parents=True)
    (src / "installer" / "install.ps1-like").write_text("no shebang here\n")   # the installer counts all the same
    (src / "run.sh").write_bytes(SCRIPT)
    (src / "notes.txt").write_text("hi\n")
    taken = h.take_in(spec(kind="folder", location=str(src), installer="installer/install.ps1-like"),
                      tmp_path / "state")
    assert kinds(taken) == {"installer/install.ps1-like": "x", "run.sh": "x", "notes.txt": "f"}
    assert taken.derived == ("installer/install.ps1-like", "run.sh")
    assert h.stored(tmp_path / "state", "demo")[0]["modes_derived_from_content"] == 2
