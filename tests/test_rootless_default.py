"""Rootless by default: no standing rights that can do more than needed (DESIGN, "Components and decisions").

- The framework prefers a rootless engine when both exist: the person's rootless Docker, then the person's rootless
  Podman socket, before a Docker daemon that runs as root; a DOCKER_HOST the person set still wins
  (orchestrator/utils/docker_engine.py ensure_docker_host).
- Mojo (and so `llmcf doctor`, the Mojo window and the AI's doctor tool, which all read the one check) names plainly
  when the person's account is in a group with root effect (Linux 'docker', Windows 'docker-users'), what it means
  and the way to a rootless engine; the texts are fixed (no account name, no path), so an AI reads the same verdict.
- Builds keep working on a rootless engine: the stage runner is root inside (the person outside), the stage account
  maps to the person's sub-ids, and the host's check of the created container passes, for rootless Podman and
  rootless Docker alike (fake engines with the inspect answers those engines give).

The sockets are real Unix sockets in a short temporary folder (the 108-byte limit of socket paths).
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

from orchestrator.utils import docker_engine
from orchestrator.utils.docker_engine import ensure_docker_host

POSIX_SOCKETS = pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(socket, "AF_UNIX"),
                                   reason="the engine sockets are Unix sockets on Linux")

PODMAN_VERSION = {"Version": "5.8.2", "ApiVersion": "1.41",
                  "Components": [{"Name": "Podman Engine", "Version": "5.8.2"}]}
DOCKER_VERSION = {"Version": "29.8.2", "ApiVersion": "1.56", "Components": [{"Name": "Engine", "Version": "29.8.2"}]}
ROOTLESS_INFO = {"SecurityOptions": ["name=seccomp,profile=default", "name=rootless"]}
ROOTFUL_INFO = {"SecurityOptions": ["name=seccomp,profile=default"]}


@pytest.fixture
def short_dir():
    folder = Path(tempfile.mkdtemp(prefix="rl-", dir="/tmp" if os.path.isdir("/tmp") else None))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def listening(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(4)
    return server


# --- the framework prefers a rootless engine --------------------------------------------------------------------

@POSIX_SOCKETS
class TestARootlessEngineIsPreferred:

    def test_rootless_podman_wins_over_a_docker_daemon_that_runs_as_root(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        rootful = listening(short_dir / "docker.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, uid=1000, docker_sockets=[str(short_dir / "docker.sock")])
        assert choice.set_here and env["DOCKER_HOST"] == f"unix://{short_dir}/run/podman/podman.sock"
        assert "rootless" in choice.reason and str(short_dir) not in choice.reason
        podman.close(), rootful.close()

    def test_the_persons_rootless_docker_is_chosen_and_set(self, short_dir):
        """Before, the rootless Docker socket only kept 'Docker' without setting DOCKER_HOST, so docker-py looked at
        the system socket, where nothing runs."""
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        rootless = listening(short_dir / "run" / "docker.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, uid=1000, docker_sockets=[str(short_dir / "none.sock")])
        assert choice.set_here and env["DOCKER_HOST"] == f"unix://{short_dir}/run/docker.sock"
        assert choice.reason == docker_engine.ROOTLESS_DOCKER_CHOSEN
        podman.close(), rootless.close()

    def test_a_docker_host_the_person_set_still_wins(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        env = {"DOCKER_HOST": "unix:///var/run/docker.sock", "XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, uid=1000, docker_sockets=[str(short_dir / "docker.sock")])
        assert not choice.set_here and env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        podman.close()

    def test_root_keeps_the_docker_daemon(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        rootful = listening(short_dir / "docker.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert not ensure_docker_host(env, uid=0, docker_sockets=[str(short_dir / "docker.sock")]).set_here
        podman.close(), rootful.close()

    def test_only_a_rootful_docker_stays_dockers_default(self, short_dir):
        rootful = listening(short_dir / "docker.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, uid=1000, docker_sockets=[str(short_dir / "docker.sock")])
        assert not choice.set_here and "DOCKER_HOST" not in env
        rootful.close()

    def test_a_podman_docker_host_beside_a_rootful_docker_is_not_stale(self, short_dir):
        """A DOCKER_HOST at the person's Podman is what the framework itself prefers over Docker as root."""
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        rootful = listening(short_dir / "docker.sock")
        env = {"DOCKER_HOST": f"unix://{short_dir}/run/podman/podman.sock", "XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert not docker_engine.stale_podman_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        rootless = listening(short_dir / "run" / "docker.sock")
        assert docker_engine.stale_podman_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        podman.close(), rootful.close(), rootless.close()


# --- the group with root effect ---------------------------------------------------------------------------------

class TestTheGroupWithRootEffect:

    @pytest.mark.parametrize("platform, groups, expected", [
        ("linux", ["users", "docker"], "docker"),
        ("linux", ["users", "wheel"], ""),
        ("win32", ["Everyone", "EXAMPLE-PC\\docker-users"], "docker-users"),
        ("win32", ["BUILTIN\\Users"], ""),
        ("darwin", ["docker"], ""),
    ])
    def test_the_group_is_named_and_nothing_else(self, platform, groups, expected):
        assert docker_engine.root_effect_group(platform, groups=groups) == expected

    def test_reading_never_raises(self, monkeypatch):
        monkeypatch.setattr(docker_engine, "_own_groups", lambda key, group: (_ for _ in ()).throw(OSError("no")))
        assert docker_engine.root_effect_group("linux") == ""

    def test_windows_reads_whoami(self, monkeypatch):
        out = '"Everyone","Well-known group"\n"EXAMPLE-PC\\docker-users","Alias"\n'
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
        assert docker_engine.root_effect_group("win32") == "docker-users"

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the group database is Linux")
    def test_linux_reads_the_group_database(self, monkeypatch):
        import grp
        entry = types.SimpleNamespace(gr_gid=4242, gr_mem=[])
        monkeypatch.setattr(grp, "getgrnam", lambda name: entry)
        monkeypatch.setattr(os, "getgroups", lambda: [4242])
        assert docker_engine.root_effect_group("linux") == "docker"
        monkeypatch.setattr(os, "getgroups", lambda: [])
        assert docker_engine.root_effect_group("linux") == ""
        monkeypatch.setattr(grp, "getgrnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
        assert docker_engine.root_effect_group("linux") == ""


class _Engine:
    def __init__(self, version, info):
        self._version, self._info = version, info

    def version(self):
        return self._version

    def info(self):
        return self._info

    def ping(self):
        return True


def _mojo(client, monkeypatch, group=""):
    from orchestrator.Core.mojo.checks import docker as docker_check
    monkeypatch.setattr(docker_check, "_storage_findings", lambda framework: [])
    monkeypatch.setattr(docker_check, "_stage_user_findings", lambda framework, client: [])
    monkeypatch.setattr(docker_engine, "stale_podman_host", lambda *a, **k: False)
    monkeypatch.setattr(docker_engine, "root_effect_group", lambda platform=None, **k: group)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: subprocess.CompletedProcess(args, 0, "", ""))
    return docker_check.check_docker(types.SimpleNamespace(get_component=lambda name: client))


class TestMojoNamesTheRights:

    @pytest.mark.parametrize("platform, group", [("linux", "docker"), ("win32", "docker-users")])
    def test_a_group_with_root_effect_is_named_plainly_without_an_account(self, monkeypatch, platform, group):
        monkeypatch.setattr(sys, "platform", platform)
        findings = _mojo(_Engine(DOCKER_VERSION, ROOTFUL_INFO), monkeypatch, group=group)
        found = [f for f in findings if f.code == "docker.root_group"]
        assert len(found) == 1 and found[0].severity.value == "warn", findings
        f = found[0]
        assert f"'{group}'" in f.message and "root" in f.message and "permanently" in f.message
        assert "rootless" in f.fix_hint and f.why and f.where and f.cannot == "admin"
        assert f.steps == ("docker.rootless",)
        if platform == "linux":
            assert "sudo ./install.sh" in f.fix_hint and "gpasswd -d $USER docker" in f.fix_hint
        else:
            assert "Podman Desktop" in f.fix_hint and "docker-users" in f.fix_hint
        # the same fixed verdict for every account: nothing of this computer's accounts in it
        assert f.message == docker_engine.ROOT_GROUP_TEXT.format(group=group)
        assert f.where == f"the group '{group}' of your account"

    @pytest.mark.parametrize("version", [PODMAN_VERSION, DOCKER_VERSION], ids=["podman", "docker"])
    def test_a_rootless_engine_is_said_to_be_rootless(self, monkeypatch, version):
        monkeypatch.setattr(sys, "platform", "linux")
        findings = _mojo(_Engine(version, ROOTLESS_INFO), monkeypatch)
        rootless = [f for f in findings if f.code == "docker.rootless"]
        assert len(rootless) == 1 and rootless[0].severity.value == "ok"
        assert "docker.root_group" not in [f.code for f in findings]

    def test_a_rootful_engine_without_the_group_is_an_info_with_the_way(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        findings = _mojo(_Engine(DOCKER_VERSION, ROOTFUL_INFO), monkeypatch)
        line = [f for f in findings if f.code == "docker.rootless"][0]
        assert line.severity.value == "info" and "sudo ./install.sh" in line.fix_hint

    def test_rootless_podman_is_no_warning_any_more(self, monkeypatch):
        """Rootless Podman is the default the installer sets up: its limits are an info, not a warning."""
        monkeypatch.setattr(sys, "platform", "linux")
        findings = _mojo(_Engine(PODMAN_VERSION, ROOTLESS_INFO), monkeypatch)
        engine = [f for f in findings if f.code == "docker.engine"][0]
        assert engine.severity.value == "info" and "GPU" in engine.message and "BuildKit" in engine.message
        assert "--engine=docker" in engine.fix_hint

    def test_the_next_step_is_known_to_the_catalogue(self):
        from orchestrator.utils.next_steps import common
        step = [s for s in common.STEPS if s.id == "docker.rootless"][0]
        assert step.person_only == "admin" and step.literal == "sudo ./install.sh"
        for lang in ("en", "de"):
            assert common.TEXTS[lang]["step.docker.rootless"]


# --- builds keep working on a rootless engine -------------------------------------------------------------------

class _ApiClient:
    """A docker-py client whose create goes nowhere: what the framework would ask the engine for."""

    def __init__(self, version, info):
        self._version, self._info, self.created = version, info, []
        self.containers = types.SimpleNamespace(create=lambda **kw: self.created.append(kw) or "container")

    def version(self):
        return self._version

    def info(self):
        return self._info


@pytest.mark.parametrize("version", [PODMAN_VERSION, DOCKER_VERSION], ids=["rootless-podman", "rootless-docker"])
def test_a_stage_container_on_a_rootless_engine_runs_as_decided(version, tmp_path):
    """Under a rootless engine root in the container is the person outside: the stage runner keeps 0:0 (the
    person's folders stay writable without keep-id), the container starts as root with only the two switch rights,
    and the host's check of the created container (its inspect) passes."""
    from orchestrator.utils.sandbox import (RUNNER_CAPS, runner_ids, sandbox_problem, stage_user_sandbox)
    client = _ApiClient(version, ROOTLESS_INFO)
    runner = runner_ids(client)
    assert runner == (0, 0)
    options, env = stage_user_sandbox(runner, working_dir="/build-cache/work")
    assert env["STAGE_RUNNER_IDS"] == "0:0" and options["user"] == "0:0"
    volumes = {str(tmp_path / "work"): {"bind": "/build-cache/work", "mode": "rw"}}
    docker_engine.create_container(client, image="example", command=["/app/modules/build.sh"], volumes=volumes,
                                   environment=env, **options)
    created = client.created[0]
    assert created["user"] == "0:0" and sorted(created["cap_add"]) == sorted(RUNNER_CAPS)
    assert created["cap_drop"] == ["ALL"] and created["network_mode"] == "none" and created["read_only"] is True
    engine = docker_engine.engine_name(client)
    caps = {"CapDrop": ["ALL"], "CapAdd": ["CAP_SETUID", "CAP_SETGID"]} if engine == "docker" else \
        {"CapDrop": sorted(c for c in ("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "NET_BIND_SERVICE",
                                         "SETFCAP", "SETPCAP", "SYS_CHROOT")), "CapAdd": []}
    attrs = {"Config": {"User": "0:0", "Env": [f"{k}={v}" for k, v in env.items()]},
             "HostConfig": dict(caps, Privileged=False, ReadonlyRootfs=True, NetworkMode="none",
                                SecurityOpt=["no-new-privileges:true"], PidsLimit=4096, Binds=[
                                    f"{tmp_path / 'work'}:/build-cache/work:rw"]),
             "Mounts": [{"Source": str(tmp_path / "work"), "Destination": "/build-cache/work", "RW": True,
                         "Type": "bind"}]}
    if engine == "podman":
        attrs["EffectiveCaps"] = ["CAP_SETGID", "CAP_SETUID"]
    problem = sandbox_problem(attrs, options, volumes, engine=engine)
    assert problem is None, problem


@pytest.mark.parametrize("version", [PODMAN_VERSION, DOCKER_VERSION], ids=["rootless-podman", "rootless-docker"])
def test_the_stage_account_needs_the_persons_sub_ids_on_a_rootless_engine(version, tmp_path, monkeypatch):
    from orchestrator.utils import sandbox
    monkeypatch.setattr(sandbox, "_own_names", lambda: ("1000", "example"))
    subuid, subgid = tmp_path / "subuid", tmp_path / "subgid"
    subuid.write_text("example:100000:65536\n")
    subgid.write_text("example:100000:65536\n")
    client = _ApiClient(version, ROOTLESS_INFO)
    assert sandbox.stage_user_support(client, platform="linux", subuid=subuid, subgid=subgid)[0] is True
    subgid.write_text("example:100000:1000\n")
    supported, text = sandbox.stage_user_support(client, platform="linux", subuid=subuid, subgid=subgid)
    assert supported is False and "example" not in text and str(tmp_path) not in text
