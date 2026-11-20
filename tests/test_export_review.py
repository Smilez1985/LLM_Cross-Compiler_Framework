"""What a pack of the person's own work must not carry out of this computer (orchestrator/security/export_review.py).

The measurement of 2026-11-20 planted 19 local data in a module the way the wizard makes one and sent them through the
pack scrub: 15 survived. These tests keep the shapes that are private by their nature out, and keep the text that only
looks like them. Every name is made at run time; this file holds no real address, host or path.
"""

import random
import string

import pytest

from orchestrator.security import export_review as er


def word(seed, n=7):
    return "".join(random.Random(seed).choices(string.ascii_lowercase, k=n))


corp = word("corp")
UUID = "-".join("".join(random.Random(i).choices("0123456789abcdef", k=n)) for i, n in enumerate((8, 4, 4, 4, 12)))

# (label, text, the part that must be gone after the proposals are applied)
REVIEW = [
    ("path on a network drive", f"SDK_DIR=/mnt/nas/{corp}/sdk", f"/mnt/nas/{corp}"),
    ("path on an external volume", f"cp x /media/{corp}/disk/models/", f"/media/{corp}"),
    ("macOS volume", f"MODEL=/Volumes/{corp}/models/m.gguf", f"/Volumes/{corp}"),
    ("Windows drive path", f"set SDK=D:\\Projekte\\{corp}\\sdk", f"D:\\Projekte\\{corp}"),
    ("Windows share", f"copy \\\\{corp}-nas\\share\\model.bin .", f"{corp}-nas"),
    ("internal domain", f"git clone https://git.{corp}.internal/team/repo.git", f"{corp}.internal"),
    ("a name on lan", f"scp model.bin dev@{corp}.lan:/backup", f"{corp}.lan"),
    ("private registry with a port", f"FROM registry.{corp}.corp:5000/base/img@sha256:{'a' * 64}", f"registry.{corp}.corp"),
    ("ssh HostName", f"Host board\n  HostName {corp}-board.home.arpa\n", f"{corp}-board"),
    ("ssh IdentityFile", f"Host board\n  IdentityFile ~/.ssh/id_{corp}\n", f"id_{corp}"),
    ("ssh User", f"Host board\n  User {corp}\n", f"User {corp}"),
    ("ssh -i key", f"ssh -i ~/.ssh/id_{corp}_ed25519 dev@board", f"id_{corp}_ed25519"),
    ("scp -i key", f"scp -i /keys/{corp}.pem a b", f"{corp}.pem"),
]
CERTAIN = [
    ("probe serial", f"SERIAL={word('s', 10).upper()}1234", word("s", 10).upper()),
    ("probe machine id", "MACHINE_ID=" + "".join(random.Random("m").choices("0123456789abcdef", k=32)),
     "".join(random.Random("m").choices("0123456789abcdef", k=32))),
    ("probe board uuid", "BOARD_UUID=" + UUID, UUID),
    ("probe host name", f"HOSTNAME={corp}-pi", f"{corp}-pi"),
    ("known_hosts entry", f"{corp}.example.org ssh-ed25519 AAAA" + word("k", 40), "AAAA"),
]


@pytest.mark.parametrize("label,text,private", REVIEW, ids=[r[0] for r in REVIEW])
def test_what_is_private_by_its_shape_is_found_and_gone_after_the_proposal(label, text, private):
    found = er.to_review(text)
    assert found and not any(f.certain for f in found), label
    out = er.apply(text, found)
    assert private not in out, label
    assert er.to_review(out) == [], "a proposal is not found again (the export that accepted passes the check)"


@pytest.mark.parametrize("label,text,private", CERTAIN, ids=[r[0] for r in CERTAIN])
def test_what_is_certainly_personal_is_replaced_without_asking(label, text, private):
    assert er.to_review(text) == []
    out = er.certain(text)
    assert private not in out, label
    assert er.certain(out) == out


def test_a_proposal_is_an_example_the_person_can_read():
    out = er.apply(f"SDK_DIR=/mnt/nas/{corp}/sdk\nssh -i ~/.ssh/id_{corp} dev@x\n"
                   f"git clone https://git.{corp}.internal:8443/r\n", er.to_review(
                       f"SDK_DIR=/mnt/nas/{corp}/sdk\nssh -i ~/.ssh/id_{corp} dev@x\n"
                       f"git clone https://git.{corp}.internal:8443/r\n"))
    assert out == ("SDK_DIR=/path/to/example\nssh -i ~/.ssh/id_example dev@x\n"
                   "git clone https://host.example.com:8443/r\n")


# Text that looks like these shapes and is none: a container's mount point, a system folder, documentation.
HARMLESS = [
    "mount -t tmpfs tmpfs /mnt",
    "MODELS=/mnt/models",
    "cd /opt/rocm/bin && ./tool",
    "export PATH=/usr/local/bin:$PATH",
    "COPY . /workspace/",
    "C:\\Windows\\System32\\cmd.exe",
    "C:\\Program Files\\Git\\bin\\git.exe",
    "see https://example.com/docs and https://docs.python.org/3/",
    "host.example.com:8443",
    "self.cfg.internal = True",
    "os.local",
    "ssh -i $KEY dev@board",
    "ssh -i ${KEY_FILE} dev@board",
    "ssh -i ~/.ssh/id_example dev@board",
    "Host board\n  User root\n",
    "Host board\n  HostName %h\n",
    "HOSTNAME=localhost",
    "the serial number is printed on the board",
    "SERIAL=",
    "git@github.com:Smilez1985/LLM_Cross-Compiler_Framework.git",
    "FROM debian:bookworm-slim@sha256:" + "b" * 64,
    "USER llmbuilder",
    "RUN useradd llmbuilder\nUSER llmbuilder\nHostName is a word\n",
]


@pytest.mark.parametrize("text", HARMLESS)
def test_text_that_only_looks_private_is_left_alone(text):
    assert er.findings(text) == [], text


def test_the_description_names_the_place_and_shows_the_original_only_to_the_person():
    f = er.to_review(f"line one\nSDK_DIR=/mnt/nas/{corp}/sdk\n")[0]
    assert f.line == 2
    for_ai = er.describe(f, "modules/build.sh", with_original=False)
    for_person = er.describe(f, "modules/build.sh", with_original=True)
    assert corp not in for_ai and "/path/to/example" in for_ai and "modules/build.sh:2" in for_ai
    assert corp in for_person


def test_findings_never_overlap_and_the_replacement_keeps_the_rest_of_the_line():
    text = f"ssh -i ~/.ssh/id_{corp} dev@{corp}.lan 'echo ok'"
    out = er.apply(text, er.to_review(text))
    assert out.startswith("ssh -i ~/.ssh/id_example dev@") and out.endswith("'echo ok'")
    assert corp not in out


def test_the_module_needs_only_the_standard_library():
    import ast
    import sys
    from pathlib import Path
    tree = ast.parse(Path(er.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert {name.split(".")[0] for name in imported} <= sys.stdlib_module_names


def test_the_detectors_stay_linear_on_a_hostile_text():
    import time
    hostile = ("ssh -i " * 6000 + "a." * 30000 + "/mnt/x/" * 6000 + "HostName " * 6000)
    start = time.perf_counter()
    er.findings(hostile)
    assert time.perf_counter() - start < 5.0


def test_no_file_of_a_shipped_target_stops_an_export():
    """The first version flagged `USER llmbuilder` in every Dockerfile, so no shipped target could be exported; the
    export test of the CI found it. The files the framework ships must hold nothing this module would stop at."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "targets"
    flagged = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in (".png", ".zip", ".gz", ".pyc"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        flagged += [f"{path.relative_to(root)}:{f.line}: {f.kind}: {f.original}" for f in er.to_review(text)]
    assert not flagged, "\n".join(flagged[:20])
