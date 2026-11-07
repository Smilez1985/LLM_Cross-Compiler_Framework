"""Every common format of a harness taken in (orchestrator/runtime/bundle/harness.py, orchestrator/utils/archives.py):
the format by content, never by name; zip, tar and its compressed kinds and a single compressed file through the
standard library; 7z, iso, cpio, rar and disk images through 7-Zip (those tests run only where 7-Zip and the tool that
makes the fixture are on this computer); a script or a program as a harness of one file; the archive pin is always the
file's sha256. The listing parser and its refusals are tested on a recorded `7z l -slt -ba` text without 7-Zip. Hard
links inside the tree become files of their own; one out of it is refused. An encrypted archive asks the person's
password (never on 7-Zip's command line). A disk image's ext partition is read in place (utils/ext4_read), never
extracted whole. The content rule for a host without executable bits (forced here) gives the same listing as a Linux
file system's modes, and a plain folder on Windows is taken in."""

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
    ("Path = hard\nFolder = -\nSize = 0\nMode = -rw-r--r--\nHard Link = ../../etc/passwd", "hard link out"),
    ("Path = hard\nFolder = -\nSize = 0\nMode = -rw-r--r--\nHard Link = /etc/passwd", "hard link out"),
    ("Path = hard\nFolder = -\nSize = 0\nMode = -rw-r--r--\nHard Link = nothere.txt", "does not have"),
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


# --- hard links -------------------------------------------------------------------------------------------------------

def hardlink_tar(path: Path, linkname: str) -> Path:
    with tarfile.open(path, "w") as tf:
        for name, data, perm in members():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), perm
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("installer/again.sh")
        link.type, link.linkname, link.mode = tarfile.LNKTYPE, linkname, 0o755
        tf.addfile(link)
    return path


def test_a_hard_link_inside_the_tree_is_a_file_of_its_own(tmp_path):
    archive = hardlink_tar(tmp_path / "h.tar", "installer/install.sh")
    taken = h.take_in(spec(location=str(archive)), tmp_path / "state")
    assert kinds(taken) == {"installer/install.sh": "x", "installer/again.sh": "x", "src/app.py": "f"}
    rows = {r[0]: r[2] for r in h.stored_listing(taken.path)}
    assert rows["installer/again.sh"] == rows["installer/install.sh"]
    if os.name != "nt":
        assert (taken.path / "installer" / "again.sh").stat().st_nlink == 1


@pytest.mark.parametrize("target", ["../../etc/passwd", "nothere.sh"])
def test_a_hard_link_out_of_the_tree_or_to_nothing_is_refused(tmp_path, target):
    archive = hardlink_tar(tmp_path / "h.tar", target)
    with pytest.raises(h.HarnessError, match="hard ?link|Tar Slip"):
        h.take_in(spec(location=str(archive)), tmp_path / "state")


def test_a_listed_hard_link_inside_is_kept_as_an_entry():
    found = {e.path: e for e in ar.entries(ar.parse_blocks(with_entry(
        "Path = b.txt\nFolder = -\nSize = 0\nMode = -rw-r--r--\nHard Link = a.txt")))}
    assert found["b.txt"].kind == "hardlink" and found["b.txt"].link == "a.txt"


@NEEDS_7Z
def test_7zip_materialises_a_hard_link_inside(tmp_path):
    archive = hardlink_tar(tmp_path / "h.tar", "installer/install.sh")
    dest = tmp_path / "work" / "q"
    dest.parent.mkdir()
    found = h._extract_7zip(archive, dest, spec(), "7z", tmp_path / "state")
    assert (dest / "installer" / "again.sh").read_bytes() == SCRIPT and "installer/again.sh" in found.modes.executable


# --- encrypted archives: the person's password, on 7-Zip's stdin only -------------------------------------------------

def take_with_passwords(archive, state, passwords):
    """take_in as the CLI and the window drive it: PasswordNeeded, then the next password."""
    given = None
    asked = []
    for _ in range(h.MAX_PASSWORD_TRIES + 1):
        try:
            return h.take_in(spec(location=str(archive)), state, password=given), asked
        except h.PasswordNeeded as e:
            asked.append(e.wrong)
            if not passwords:
                raise
            given = passwords.pop(0)
    raise AssertionError("asked too often")


@NEEDS_7Z
@pytest.mark.parametrize("args", [["-mhe=on"], []], ids=["encrypted-names", "encrypted-data"])
def test_an_encrypted_7z_asks_for_the_password_and_never_puts_it_on_the_command_line(tmp_path, args, monkeypatch):
    src = tree(tmp_path / "src")
    archive = tmp_path / "harness.7z"
    seven("a", "-pS3cret-pw", *args, str(archive), "installer", "src", cwd=src)
    seen = []
    real = subprocess.run

    def watched(argv, *a, **kw):
        seen.append((list(argv), kw.get("input")))
        return real(argv, *a, **kw)
    monkeypatch.setattr(ar.subprocess, "run", watched)
    with pytest.raises(h.PasswordNeeded) as e:
        h.take_in(spec(location=str(archive)), tmp_path / "a")
    assert not e.value.wrong and "only the person" in str(e.value)
    taken, asked = take_with_passwords(archive, tmp_path / "b", ["wrong", "S3cret-pw"])
    assert asked == [False, True] and kinds(taken)["installer/install.sh"] == "x"
    assert all("S3cret-pw" not in " ".join(argv) for argv, _ in seen)
    assert any(data == b"S3cret-pw\n" for _, data in seen)
    assert "S3cret-pw" not in json.dumps(taken.record())


@NEEDS_7Z
def test_a_zipcrypto_zip_without_7zip_goes_through_python_and_gives_up_after_three_wrong(tmp_path, monkeypatch):
    src = tree(tmp_path / "src")
    archive = tmp_path / "harness.zip"
    seven("a", "-tzip", "-pS3cret-pw", "-mem=ZipCrypto", str(archive), "installer", "src", cwd=src)
    monkeypatch.setattr(ar, "find_7zip", lambda: None)
    taken, asked = take_with_passwords(archive, tmp_path / "a", ["S3cret-pw"])
    assert asked == [False] and "installer/install.sh" in kinds(taken)
    with pytest.raises(h.PasswordNeeded) as e:
        take_with_passwords(archive, tmp_path / "b", ["no", "nope", "never"])
    assert e.value.wrong


def test_the_cli_refuses_an_encrypted_archive_without_a_terminal(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import invoke_plain
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: tmp_path / "state")

    def needs(*a, **kw):
        raise h.PasswordNeeded()
    monkeypatch.setattr(h, "take_in", needs)
    res = invoke_plain("bundle", "harness", "take-in", "mine", str(tmp_path), "--kind", "archive", "--installer",
                       "install.sh")
    assert res.code == 1 and "only the person" in res.out


def test_the_cli_asks_three_times_without_echo(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from test_cli_builds import invoke_plain
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: tmp_path / "state")
    calls = []

    def wrong(spec_, state, **kw):
        calls.append(kw.get("password"))
        raise h.PasswordNeeded(wrong=kw.get("password") is not None)
    monkeypatch.setattr(h, "take_in", wrong)
    monkeypatch.setattr(cli_module, "_ask_hidden", lambda prompt: "guess")
    res = invoke_plain("bundle", "harness", "take-in", "mine", str(tmp_path), "--kind", "archive", "--installer",
                       "install.sh")
    assert res.code == 1 and "not right 3 times" in res.out and calls == [None, "guess", "guess", "guess"]
    assert "guess" not in res.out


# --- ext file systems in a disk image: read in place -----------------------------------------------------------------

NEEDS_EXT4 = pytest.mark.skipif(shutil.which("mkfs.ext4") is None, reason="mkfs.ext4 makes the ext4 images")


def rootfs(root: Path) -> Path:
    """A board's rootfs in small: the harness in opt/demo with a symlink inside, an extent-tree file (sparse, many
    extents), a big file, and other folders around it."""
    tree(root / "opt" / "demo")
    os.symlink("../installer/install.sh", root / "opt" / "demo" / "src" / "run")
    with open(root / "opt" / "demo" / "src" / "sparse.bin", "wb") as fh:
        for i in range(40):
            fh.seek(i * 8192)
            fh.write(bytes([i + 1]) * 4096)
    (root / "opt" / "demo" / "src" / "big.bin").write_bytes(bytes(range(256)) * 9000)
    (root / "etc").mkdir()
    (root / "etc" / "hostname").write_text("board\n")
    many = root / "usr" / "share" / "many"
    many.mkdir(parents=True)
    for i in range(600):   # an htree directory
        (many / f"file-{i:04d}.txt").write_text(str(i))
    return root


def ext_image(path: Path, src: Path, *, size="64M", opts=()) -> Path:
    subprocess.run(["truncate", "-s", size, str(path)], check=True)
    subprocess.run(["mkfs.ext4", "-q", "-F", *opts, "-d", str(src), str(path)], check=True, capture_output=True)
    return path


def mbr_with(path: Path, fs_image: Path, start: int = 2048) -> Path:
    """A disk image: an MBR, an empty first partition slot taken by a small FAT-less gap, and the ext image."""
    data = fs_image.read_bytes()
    sectors = len(data) // 512
    with open(path, "wb") as fh:
        fh.write(bytes(start * 512))
        fh.write(data)
    with open(path, "r+b") as fh:
        fh.seek(446)
        fh.write(bytes([0, 0, 2, 0, 0x83, 0, 0, 0]) + struct.pack("<II", start, sectors))
        fh.seek(510)
        fh.write(b"\x55\xaa")
    return path


@NEEDS_EXT4
@POSIX
@pytest.mark.parametrize("opts", [(), ("-O", "64bit"), ("-b", "1024")], ids=["default", "64bit", "1k-blocks"])
def test_the_ext4_reader_reads_files_links_and_an_extent_tree(tmp_path, opts):
    from orchestrator.utils.ext4_read import Ext4
    src = rootfs(tmp_path / "src")
    image = ext_image(tmp_path / "fs.img", src, opts=opts)
    with Ext4(image) as fs:
        for rel in ("opt/demo/installer/install.sh", "opt/demo/src/sparse.bin", "opt/demo/src/big.bin"):
            assert fs.read_data(fs.lookup(rel), limit=1 << 30) == (src / rel).read_bytes(), rel
        assert fs.link_target(fs.lookup("opt/demo/src/run")) == "../installer/install.sh"
        names = {n for n, _i, _t in fs.entries(fs.lookup("usr/share/many"))}
        assert len(names) == 600
        assert fs.lookup("opt/demo/installer/install.sh").mode & 0o777 == 0o755


@NEEDS_EXT4
@POSIX
@pytest.mark.parametrize("image_path", [None, "opt/demo"])
def test_a_disk_image_reads_only_the_harness_folder_of_its_ext_partition(tmp_path, image_path):
    src = rootfs(tmp_path / "src")
    (src / "var").mkdir()
    (src / "var" / "big.img").write_bytes(os.urandom(8 * 1024 * 1024))   # never read
    disk = mbr_with(tmp_path / "board.img", ext_image(tmp_path / "fs.img", src))
    taken = h.take_in(spec(location=str(disk), image_path=image_path), tmp_path / "state")
    plain = h.take_in(spec(kind="folder", location=str(src / "opt" / "demo")), tmp_path / "folder")
    assert taken.tree_sha256 == plain.tree_sha256 and kinds(taken)["src/run"] == "l"
    assert taken.image_size == disk.stat().st_size and taken.image_read < 4 * 1024 * 1024   # never var/big.img
    assert taken.format == "disk:ext" and taken.pin == h._sha256_file(disk)
    assert "Read" in h.image_text(taken) and taken.record()["image_read"] == taken.image_read


@NEEDS_EXT4
@POSIX
def test_an_ext_file_system_alone_and_the_installer_twice(tmp_path):
    src = rootfs(tmp_path / "src")
    tree(src / "srv" / "demo")
    image = ext_image(tmp_path / "fs.img", src)
    with pytest.raises(h.HarnessError, match="more than once.*image_path"):
        h.take_in(spec(location=str(image)), tmp_path / "state")
    taken = h.take_in(spec(location=str(image), image_path="srv/demo"), tmp_path / "state")
    assert set(kinds(taken)) == {"installer/install.sh", "src/app.py"}
    with pytest.raises(h.HarnessError, match="image_path"):
        h.take_in(spec(location=str(image), image_path="opt/none", image_partition=0), tmp_path / "state")


@NEEDS_EXT4
@POSIX
def test_a_link_out_of_the_harness_in_an_ext_image_is_refused(tmp_path):
    src = rootfs(tmp_path / "src")
    os.symlink("/etc/shadow", src / "opt" / "demo" / "src" / "out")
    image = ext_image(tmp_path / "fs.img", src)
    with pytest.raises(h.HarnessError, match="leaves the harness"):
        h.take_in(spec(location=str(image), image_path="opt/demo"), tmp_path / "state")


@NEEDS_EXT4
@POSIX
@pytest.mark.skipif(os.geteuid() != 0 if hasattr(os, "geteuid") else True,
                    reason="a device node in the source folder needs root (mknod)")
def test_a_device_node_in_an_ext_image_is_refused(tmp_path):
    src = rootfs(tmp_path / "src")
    os.mknod(src / "opt" / "demo" / "src" / "tty", 0o600 | 0o020000, os.makedev(4, 1))
    image = ext_image(tmp_path / "fs.img", src)
    with pytest.raises(h.HarnessError, match="device"):
        h.take_in(spec(location=str(image), image_path="opt/demo"), tmp_path / "state")


def test_a_crafted_ext_superblock_is_refused(tmp_path):
    from orchestrator.utils.ext4_read import Ext4, Ext4Error
    image = tmp_path / "bad.img"
    sb = bytearray(1024)
    struct.pack_into("<H", sb, 0x38, 0xEF53)
    struct.pack_into("<I", sb, 0x18, 2)
    struct.pack_into("<I", sb, 0x28, 8)
    struct.pack_into("<I", sb, 0x00, 1 << 30)
    struct.pack_into("<I", sb, 0x60, 0x10000)   # encryption
    image.write_bytes(bytes(1024) + sb + bytes(4096))
    with pytest.raises(Ext4Error, match="encryption"):
        Ext4(image)
    struct.pack_into("<I", sb, 0x60, 0x2)
    struct.pack_into("<I", sb, 0x4C, 1)
    struct.pack_into("<H", sb, 0x58, 256)
    image.write_bytes(bytes(1024) + sb)   # the group descriptors would lie beyond the image
    with Ext4(image) as fs, pytest.raises(Ext4Error, match="beyond"):
        fs.inode(2)


def gpt_with(path: Path, fs_image: Path, start: int = 2048) -> Path:
    """A disk image with a protective MBR and a GPT (one entry: Linux filesystem) around the ext image."""
    data = fs_image.read_bytes()
    sectors = len(data) // 512
    with open(path, "wb") as fh:
        fh.write(bytes(start * 512))
        fh.write(data)
        fh.write(bytes(33 * 512))
    with open(path, "r+b") as fh:
        fh.seek(446)
        fh.write(bytes([0, 0, 2, 0, 0xEE, 0, 0, 0]) + struct.pack("<II", 1, 0xFFFFFFFF))
        fh.seek(510)
        fh.write(b"\x55\xaa")
        fh.seek(512)
        fh.write(b"EFI PART" + bytes(64) + struct.pack("<QII", 2, 128, 128))
        fh.seek(1024)
        linux = bytes.fromhex("af3dc60f838472478e793d69d8477de4")
        fh.write(linux + bytes(16) + struct.pack("<QQ", start, start + sectors - 1))
    return path


@NEEDS_EXT4
@POSIX
def test_a_gpt_disk_image_is_read_the_same_way(tmp_path):
    from orchestrator.utils.ext4_read import partitions
    src = rootfs(tmp_path / "src")
    disk = gpt_with(tmp_path / "board.img", ext_image(tmp_path / "fs.img", src))
    parts = partitions(disk)
    assert [(p.number, p.offset) for p in parts] == [(0, 2048 * 512)]
    assert parts[0].type == "0fc63daf-8483-4772-8e79-3d69d8477de4"
    taken = h.take_in(spec(location=str(disk)), tmp_path / "state")
    assert "installer/install.sh" in kinds(taken) and taken.format == "disk:ext"
