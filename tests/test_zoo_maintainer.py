"""The maintainer actions of the community repository (orchestrator/Core/community_maintainer.py): the signing key in
a fake keyring, review and publish and revoke against a fake GitHub CLI, the CLI commands and the hub's Maintainer
tab, and the guards (the person only, no token, never a push to main, never reachable by an AI).

No network and never the real home: gh is a fake that answers from a dictionary of pull requests, and the git
commands run against a bare repository in the test's temporary folder (git reaches no other place, its identity
comes from the environment of the test)."""

import ast
import inspect
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from conftest import REPO_ROOT
from orchestrator.Core import community_maintainer as cmaint
from orchestrator.Core import community_pack as cp
from orchestrator.Core.community_maintainer import Cancelled, Maintainer, MaintainerError
from orchestrator.Core.community_manager import CommunityIndex, CommunityManager
from orchestrator.tools.audit import AuditLog
from test_zoo import make_target, pack_bytes

REPO = "lcf-example/community"
KEY = "lcf-test-2026"
PACK = "contribution-target-demo-board-1.2.0.zip"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="runs git against a local bare repository")


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def rollup(state):
    if state == "none":
        return []
    status = "COMPLETED" if state in ("pass", "fail") else "IN_PROGRESS"
    conclusion = {"pass": "SUCCESS", "fail": "FAILURE"}.get(state, "")
    return [{"__typename": "CheckRun", "name": "validate", "status": status, "conclusion": conclusion},
            {"__typename": "CheckRun", "name": "other", "status": "COMPLETED", "conclusion": "FAILURE"}]


class FakeGitHub:
    """The community repository: a bare repository (origin) and the pull requests gh would report; `runner` is the
    Maintainer's runner (gh answered here, git run for real on the local folders only)."""

    def __init__(self, tmp_path: Path, monkeypatch):
        for name, value in (("GIT_AUTHOR_NAME", "Maintainer"), ("GIT_COMMITTER_NAME", "Maintainer"),
                            ("GIT_AUTHOR_EMAIL", ""), ("GIT_COMMITTER_EMAIL", ""), ("GIT_CONFIG_NOSYSTEM", "1"),
                            ("GIT_TERMINAL_PROMPT", "0")):
            monkeypatch.setenv(name, value)
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        git("init", "-q", "--bare", str(self.origin))
        git("-C", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main")
        self.seed = tmp_path / "seed"
        git("init", "-q", str(self.seed))
        git("-C", str(self.seed), "config", "core.autocrlf", "false")
        git("-C", str(self.seed), "remote", "add", "origin", str(self.origin))
        self.prs, self.calls, self.new_checks, self.auth_ok = {}, [], ["pending", "pass"], True
        self.merges, self.deleted = [], []
        self.commit({"README.md": b"community\n", "contributions/target/README.md": b"targets\n"}, "refs/heads/main",
                    base=None)

    # -- the repository -----------------------------------------------------------------------------------------
    def commit(self, files, ref, base="refs/heads/main", message="change", remove=()):
        seed = str(self.seed)
        if base is None:
            git("-C", seed, "checkout", "-q", "--orphan", "work")
        else:
            git("-C", seed, "fetch", "-q", "origin", f"+{base}:refs/remotes/base")
            git("-C", seed, "checkout", "-q", "--detach", "refs/remotes/base")
        for path in remove:
            git("-C", seed, "rm", "-q", "--", path)
        for path, data in files.items():
            target = self.seed / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            git("-C", seed, "add", "--", path)
        git("-C", seed, "commit", "-q", "-m", message)
        git("-C", seed, "push", "-q", "--force", "origin", f"HEAD:{ref}")
        return git("-C", seed, "rev-parse", "HEAD")

    def main(self, path):
        result = subprocess.run(["git", "-C", str(self.origin), "show", f"refs/heads/main:{path}"],
                                capture_output=True, stdin=subprocess.DEVNULL)
        return result.stdout if result.returncode == 0 else None

    def main_files(self):
        return set(git("-C", str(self.origin), "ls-tree", "-r", "--name-only", "refs/heads/main").splitlines())

    def contribution(self, data, number=7, name=PACK, check="pass", kind="target"):
        path = f"contributions/{kind}/{name}"
        head = self.commit({path: data}, f"refs/pull/{number}/head")
        self.prs[number] = {"number": number, "title": f"Add {kind} pack {name}", "author": {"login": "alice"},
                            "url": f"https://github.com/{REPO}/pull/{number}", "headRefOid": head,
                            "files": [{"path": path, "additions": 0, "deletions": 0}], "state": "OPEN",
                            "checks": [check], "ref": f"refs/pull/{number}/head"}
        return head

    def sign_main(self, index, key, message="index"):
        data = json.dumps(index).encode()
        sig = cp.sign_index(data, key).model_dump_json().encode()
        self.commit({"index.json": data, "index.json.sig": sig}, "refs/heads/main", message=message)

    # -- gh --------------------------------------------------------------------------------------------------------
    def runner(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        assert "env" not in kwargs, "gh and git get the environment as it is; nothing is passed or read"
        assert kwargs.get("stdin") is subprocess.DEVNULL
        tool = Path(cmd[0]).name
        if tool == "git":
            assert not any(a.startswith(("http://", "https://", "git@", "ssh://")) for a in cmd[1:])
            return subprocess.run(["git", *cmd[1:]], **kwargs)
        assert tool == "gh", cmd
        out, code, err = self.gh(cmd[1:])
        if not kwargs.get("text"):
            out, err = out.encode(), err.encode()
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, cmd, out, err)
        return subprocess.CompletedProcess(cmd, code, out, err)

    def _state(self, pr):
        return pr["checks"].pop(0) if len(pr["checks"]) > 1 else pr["checks"][0]

    def gh(self, args):
        if args[:2] == ["auth", "status"]:
            return ("", 0, "") if self.auth_ok else ("", 1, "You are not logged into any GitHub hosts. To log in, "
                                                             "run: gh auth login")
        if args[:2] == ["repo", "clone"]:
            assert args[2] == REPO
            flags = args[args.index("--") + 1:]
            subprocess.run(["git", "clone", "-q", *flags, str(self.origin), args[3]], check=True,
                           capture_output=True, stdin=subprocess.DEVNULL)
            return "", 0, ""
        if args[:2] == ["pr", "list"]:
            rows = [{k: v for k, v in pr.items() if k not in ("checks", "ref", "state")}
                    | {"statusCheckRollup": rollup(pr["checks"][0])}
                    for pr in self.prs.values() if pr["state"] == "OPEN"]
            return json.dumps(rows), 0, ""
        if args[:2] == ["pr", "view"]:
            pr = self.prs[int(args[2])]
            return json.dumps({"number": pr["number"], "state": pr["state"], "headRefOid": pr["headRefOid"],
                               "url": pr["url"], "statusCheckRollup": rollup(self._state(pr))}), 0, ""
        if args[:2] == ["pr", "create"]:
            branch = args[args.index("--head") + 1]
            assert args[args.index("--base") + 1] == "main" and branch != "main"
            head = git("-C", str(self.origin), "rev-parse", f"refs/heads/{branch}")
            number = max([7, *self.prs]) + 1
            self.prs[number] = {"number": number, "title": args[args.index("--title") + 1], "author": {"login": "m"},
                                "url": f"https://github.com/{REPO}/pull/{number}", "headRefOid": head, "files": [],
                                "state": "OPEN", "checks": list(self.new_checks), "ref": f"refs/heads/{branch}"}
            return f"https://github.com/{REPO}/pull/{number}\n", 0, ""
        if args[:2] == ["pr", "merge"]:
            pr = self.prs[int(args[2])]
            assert "--squash" in args and args[args.index("--repo") + 1] == REPO
            if args[args.index("--match-head-commit") + 1] != pr["headRefOid"]:
                return "", 1, "Head branch was modified. Review and try the merge again."
            if pr["checks"][0] != "pass":   # main is protected: the required check must pass
                return "", 1, 'Required status check "validate" is expected.'
            origin = str(self.origin)
            tree = git("-C", origin, "rev-parse", f"{pr['headRefOid']}^{{tree}}")
            new = git("-C", origin, "commit-tree", tree, "-p", "refs/heads/main", "-m", f"{pr['title']} (#{pr['number']})")
            git("-C", origin, "update-ref", "refs/heads/main", new)
            pr["state"] = "MERGED"
            self.merges.append(pr["number"])
            return "", 0, ""
        if args[:3] == ["api", "--method", "DELETE"]:
            branch = args[3].split("/git/refs/heads/", 1)[1]
            git("-C", str(self.origin), "update-ref", "-d", f"refs/heads/{branch}")
            self.deleted.append(branch)
            return "", 0, ""
        raise AssertionError(f"unexpected gh call {args}")

    def pushes(self):
        return [c for c in self.calls if Path(c[0]).name == "git" and "push" in c]


@pytest.fixture
def world(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("runs git against a local bare repository")
    github = FakeGitHub(tmp_path, monkeypatch)
    keyring = FakeKeyring()
    entry = cmaint.create_signing_key(KEY, keyring=keyring)
    audit = AuditLog(tmp_path / "audit" / "audit.db")
    progress = []
    m = Maintainer(REPO, tmp_path / "state" / "maintainer", actor="cli", audit=audit, runner=github.runner,
                   which=lambda name: f"/fake/bin/{name}", keyring=keyring, trusted_keys=[], sleep=lambda s: None,
                   progress=lambda code, values: progress.append((code, values)))
    key = cmaint.load_signing_key(KEY, keyring=keyring)
    return SimpleNamespace(github=github, keyring=keyring, entry=entry, audit=audit, m=m, key=key, progress=progress,
                           tmp=tmp_path)


def demo_pack(tmp_path, name="demo-board", version="1.2.0", script="echo build\n"):
    (tmp_path / "src").mkdir(exist_ok=True)
    root = make_target(Path(tempfile.mkdtemp(prefix=f"{name}-{version}-", dir=tmp_path / "src")) / name,
                       script=script)
    yml = (root / "target.yml").read_text().replace("version: 1.2.0", f"version: {version}")
    (root / "target.yml").write_text(yml)
    return pack_bytes(root, pack_id=name, version=version)


def codes(world):
    return [code for code, _ in world.progress]


def audit_rows(world):
    return [e for e in reversed(world.audit.entries(500)) if e["tool"] == "zoo_maintainer"]   # oldest first


# --- 1. the signing key ----------------------------------------------------------------------------------------------

def test_the_signing_key_is_made_once_and_its_public_entry_is_saved(tmp_path):
    keyring = FakeKeyring()
    entry = cmaint.create_signing_key("lcf-community-2026", keyring=keyring)
    trusted = cp.TrustedKey.model_validate(yaml.safe_load(entry.text())[0])
    assert trusted.key_id == entry.key_id and trusted.name == "lcf-community-2026"
    stored = keyring.store[(cmaint.SERVICE, "lcf-community-2026")]
    assert stored not in entry.text(), "the private key never appears in the entry"
    with pytest.raises(MaintainerError) as refused:
        cmaint.create_signing_key("lcf-community-2026", keyring=keyring)
    assert refused.value.code == "key_exists" and "never replaced" in str(refused.value)
    assert "Next step: choose another name." in str(refused.value)
    assert keyring.store[(cmaint.SERVICE, "lcf-community-2026")] == stored
    saved = entry.save(tmp_path / "public-key.txt")
    assert saved.read_text(encoding="utf-8") == entry.text()
    with pytest.raises(MaintainerError, match="never replaced"):
        entry.save(saved)
    for bad in ("", "my key", "../x", "a" * 65):
        with pytest.raises(MaintainerError):
            cmaint.create_signing_key(bad, keyring=keyring)
    with pytest.raises(PermissionError):
        cmaint.create_signing_key("other", keyring=keyring, actor="mcp")


def test_a_broken_keyring_is_a_plain_reason():
    class Broken:
        def get_password(self, *a):
            raise RuntimeError("No recommended backend was available")
    with pytest.raises(MaintainerError) as refused:
        cmaint.create_signing_key("lcf-x", keyring=Broken())
    assert refused.value.code == "keyring" and "Secret Service" in str(refused.value)


def test_the_script_is_a_thin_wrapper_over_the_core():
    source = (REPO_ROOT / "scripts" / "zoo_sign_index.py").read_text(encoding="utf-8")
    assert "community_maintainer" in source
    for own in ("Ed25519PrivateKey", "set_password", "sign_index(", "verify_file("):
        assert own not in source, f"the script does {own} itself"


# --- 2. review and publish -------------------------------------------------------------------------------------------

@needs_git
def test_review_shows_every_file_and_publish_goes_through_pull_requests(world):
    data = demo_pack(world.tmp)
    head = world.github.contribution(data)
    items = world.m.contributions()
    assert [(i.label, i.check, i.packs) for i in items] == [("#7", "pass", [f"contributions/target/{PACK}"])]
    assert items[0].head == head and items[0].author == "alice"
    review = world.m.review(items[0])
    assert review.ok, review.problems()
    pack = review.packs[0]
    shown = [f.path for f in pack.files]
    assert shown[:2] == ["pack.json", "pack.sig"] or shown[0] == "pack.json"
    assert {"target.yml", "Dockerfile", "modules/build.sh"} <= set(shown)
    assert all(f.text is not None for f in pack.files)
    assert "echo build" in next(f.text for f in pack.files if f.path == "modules/build.sh")
    assert world.github.merges == [] and not world.github.pushes(), "a review changes nothing"

    result = world.m.publish(review, key_name=KEY, title="Demo Board")
    review.discard()
    assert not review.folder.exists()
    github = world.github
    assert github.merges == [7, 8] and github.prs[8]["state"] == "MERGED"
    target = f"packs/target/{PACK}"
    files = github.main_files()
    assert target in files and f"contributions/target/{PACK}" not in files
    assert "contributions/target/README.md" in files
    assert github.main(target) == data
    index = CommunityManager._verify_index(github.main("index.json"), github.main("index.json.sig"),
                                           [world.key.public_key()])
    assert result.version == index.version == 1 and result.url.endswith("/pull/8")
    (entry,) = index.entries
    assert entry.url == f"https://raw.githubusercontent.com/{REPO}/main/{target}"
    assert (entry.id, entry.version, entry.trust, entry.title, entry.size) == (
        "demo-board", "1.2.0", "community", "Demo Board", len(data))
    assert entry.sha256 == cp.hashlib.sha256(data).hexdigest()
    # never a push to main: the one push names the new branch, which is deleted after the merge
    (push,) = github.pushes()
    assert push[-1].endswith(f":refs/heads/{result.branch}") and result.branch.startswith("lcf-publish-")
    assert "main" not in push[-1].split(":", 1)[1] and github.deleted == [result.branch]
    assert codes(world)[-1] == "published"
    for step in ("merge_contribution", "move", "entry", "version", "sign", "commit", "push", "pr", "wait", "check",
                 "merge"):
        assert step in codes(world), step
    rows = audit_rows(world)
    assert rows[0]["outcome"] == "started" and rows[-1]["outcome"] == "ok"
    assert {json.loads(r["args_preview"])["step"] for r in rows} >= {"merge_contribution", "sign", "push", "merge",
                                                                    "published"}
    assert all(r["actor"] == "cli" and r["decision"] == "person" for r in rows)
    assert world.audit.check().intact
    assert world.m.remembered_key() == KEY
    assert world.m.contributions() == [], "published: neither open nor waiting"


@needs_git
def test_the_next_publish_raises_the_version_on_top_of_the_signed_index(world):
    world.github.contribution(demo_pack(world.tmp))
    world.m.publish(world.m.review(world.m.find("7")), key_name=KEY)
    world.github.contribution(demo_pack(world.tmp, version="1.3.0"), number=9,
                              name="contribution-target-demo-board-1.3.0.zip")
    result = world.m.publish(world.m.review(world.m.find("#9")), key_name=KEY, trust="verified")
    index = CommunityIndex.model_validate_json(world.github.main("index.json"))
    assert result.version == index.version == 2
    assert [(e.version, e.trust) for e in index.entries] == [("1.2.0", "community"), ("1.3.0", "verified")]


@needs_git
def test_an_index_on_main_that_does_not_verify_is_never_signed_over(world):
    world.github.sign_main({"schema_version": 1, "version": 3, "generated": "2026-10-20T10:00:00Z", "entries": [],
                            "revoked": []}, world.key)
    tampered = world.github.main("index.json").replace(b'"version": 3', b'"version": 4')
    world.github.commit({"index.json": tampered}, "refs/heads/main")
    world.github.contribution(demo_pack(world.tmp))
    review = world.m.review(world.m.find("7"))
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(review, key_name=KEY)
    assert refused.value.code == "bad_index" and "Nothing was changed on GitHub." in str(refused.value)
    assert world.github.merges == [] and not world.github.pushes()
    assert audit_rows(world)[-1]["outcome"] == "error"


@needs_git
def test_a_pull_request_whose_check_has_not_passed_is_not_merged(world):
    world.github.contribution(demo_pack(world.tmp), check="pending")
    item = world.m.find("7")
    assert item.check == "pending"
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(world.m.review(item), key_name=KEY)
    assert refused.value.code == "check_not_passed" and "approve the workflow run" in str(refused.value)
    assert world.github.merges == []


@needs_git
def test_a_pull_request_that_changed_after_the_review_is_not_merged(world):
    world.github.contribution(demo_pack(world.tmp))
    review = world.m.review(world.m.find("7"))
    world.github.contribution(demo_pack(world.tmp, script="echo other\n"))   # the contributor pushed again
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(review, key_name=KEY)
    assert refused.value.code == "changed" and world.github.merges == []


@needs_git
def test_a_rejected_pack_is_shown_but_never_published(world):
    world.github.contribution(demo_pack(world.tmp, script="cp /home/alice/secret.txt .\n"))
    review = world.m.review(world.m.find("7"))
    assert not review.ok and any("privacy scrub" in p for p in review.problems())
    assert any(f.path == "modules/build.sh" for f in review.packs[0].files), "still shown for the review"
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(review, key_name=KEY)
    assert refused.value.code == "rejected" and world.github.merges == [] and audit_rows(world) == []


@needs_git
def test_cancel_before_the_last_merge_leaves_the_pull_request_open(world):
    world.github.contribution(demo_pack(world.tmp))
    review = world.m.review(world.m.find("7"))
    stop = {"now": False}
    world.m.progress = lambda code, values: world.progress.append((code, values)) or \
        stop.update(now=stop["now"] or code == "pr")
    with pytest.raises(Cancelled) as cancelled:
        world.m.publish(review, key_name=KEY, cancelled=lambda: stop["now"])
    text = str(cancelled.value)
    assert "Cancelled." in text and "stays open" in text and "/pull/8" in text
    assert world.github.merges == [7], "the contribution was merged, the publication was not"
    assert world.github.prs[8]["state"] == "OPEN" and world.github.main("index.json") is None
    assert audit_rows(world)[-1]["outcome"] == "cancelled"
    assert not world.m._git("status", "--porcelain").stdout.strip(), "the clone is back on main, clean"


@needs_git
def test_cancel_at_the_start_changes_nothing(world):
    world.github.contribution(demo_pack(world.tmp))
    review = world.m.review(world.m.find("7"))
    with pytest.raises(Cancelled) as cancelled:
        world.m.publish(review, key_name=KEY, cancelled=lambda: True)
    assert "Nothing was changed on GitHub." in str(cancelled.value)
    assert world.github.merges == [] and not world.github.pushes()


@needs_git
def test_a_failed_check_on_the_publish_pull_request_merges_nothing(world):
    world.github.contribution(demo_pack(world.tmp))
    world.github.new_checks = ["pending", "fail"]
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(world.m.review(world.m.find("7")), key_name=KEY)
    assert refused.value.code == "check_failed" and "stays open" in str(refused.value)
    assert world.github.merges == [7] and world.github.main("index.json") is None


@needs_git
def test_a_merged_pack_that_was_not_published_is_listed_and_published(world):
    data = demo_pack(world.tmp)
    world.github.commit({f"contributions/target/{PACK}": data}, "refs/heads/main")
    (item,) = world.m.contributions()
    assert item.number is None and item.check == "merged" and item.label == f"main:contributions/target/{PACK}"
    result = world.m.publish(world.m.review(world.m.find(item.label)), key_name=KEY)
    assert result.version == 1 and world.github.merges == [8], "only the publication's pull request"
    assert world.github.main(f"packs/target/{PACK}") == data


@needs_git
def test_a_published_file_is_never_replaced(world):
    data = demo_pack(world.tmp)
    world.github.commit({f"packs/target/{PACK}": b"older"}, "refs/heads/main")
    world.github.contribution(data)
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(world.m.review(world.m.find("7")), key_name=KEY)
    assert refused.value.code == "exists" and world.github.merges == []


@needs_git
def test_changes_of_the_person_in_the_clone_are_never_thrown_away(world):
    world.github.contribution(demo_pack(world.tmp))
    review = world.m.review(world.m.find("7"))
    (world.m.clone / "notes.txt").write_text("mine")
    with pytest.raises(MaintainerError) as refused:
        world.m.publish(review, key_name=KEY)
    assert refused.value.code == "clone_dirty" and (world.m.clone / "notes.txt").read_text() == "mine"
    assert world.github.merges == []
    assert str(world.m.clone) in str(refused.value), "the person is told where"
    assert str(world.m.clone) not in audit_rows(world)[-1]["detail"], "the audit row names no folder of the computer"


@needs_git
def test_a_review_folder_of_the_person_must_be_new_or_empty(world):
    world.github.contribution(demo_pack(world.tmp))
    folder = world.tmp / "review"
    folder.mkdir()
    (folder / "keep.txt").write_text("x")
    with pytest.raises(MaintainerError, match="not empty"):
        world.m.review(world.m.find("7"), folder)
    review = world.m.review(world.m.find("7"), world.tmp / "new-review")
    assert (world.tmp / "new-review" / PACK).read_bytes() == review.packs[0].data
    assert (world.tmp / "new-review" / Path(PACK).stem / "modules" / "build.sh").is_file()
    assert (world.tmp / "new-review" / Path(PACK).stem / "pack.json").is_file()
    review.discard()
    assert (world.tmp / "new-review").is_dir(), "a folder the person named is never removed"


# --- 3. revoke -------------------------------------------------------------------------------------------------------

@needs_git
def test_revoke_adds_the_revocation_through_a_pull_request(world):
    world.github.contribution(demo_pack(world.tmp))
    world.m.publish(world.m.review(world.m.find("7")), key_name=KEY)
    result = world.m.revoke("target", "demo-board", version="1.2.0", reason="breaks the board", key_name=KEY)
    index = CommunityManager._verify_index(world.github.main("index.json"), world.github.main("index.json.sig"),
                                           [world.key.public_key()])
    assert result.version == index.version == 2 and result.branch.startswith("lcf-revoke-target-demo-board")
    assert [(r.id, r.version, r.reason) for r in index.revoked] == [("demo-board", "1.2.0", "breaks the board")]
    assert len(index.entries) == 1, "the entry stays: the hub shows it as revoked"
    with pytest.raises(MaintainerError) as again:
        world.m.revoke("target", "demo-board", version="1.2.0", reason="breaks the board", key_name=KEY)
    assert again.value.code == "revoke_exists"
    assert world.m.published(KEY).version == 2
    with pytest.raises(MaintainerError, match="not valid"):
        world.m.revoke("target", "demo-board", reason="", key_name=KEY)


@needs_git
def test_nothing_to_revoke_before_the_first_index(world):
    with pytest.raises(MaintainerError) as refused:
        world.m.revoke("target", "x", reason="r", key_name=KEY)
    assert refused.value.code == "no_index" and not world.github.pushes()


# --- refusals: plain reasons and next steps --------------------------------------------------------------------------

def plain_maintainer(tmp_path, runner, which=lambda n: f"/usr/bin/{n}", audit="audit"):
    audit = AuditLog(tmp_path / "audit.db") if audit == "audit" else audit
    return Maintainer(REPO, tmp_path / "m", actor="gui", audit=audit, runner=runner, which=which,
                      keyring=FakeKeyring(), trusted_keys=[])


def test_without_gh_or_git_the_reason_names_the_next_step(tmp_path):
    m = plain_maintainer(tmp_path, lambda *a, **k: pytest.fail("ran"), which=lambda n: None)
    with pytest.raises(MaintainerError) as refused:
        m.contributions()
    assert refused.value.code == "no_gh" and "https://cli.github.com" in str(refused.value)
    m = plain_maintainer(tmp_path, lambda *a, **k: pytest.fail("ran"), which=lambda n: "/bin/gh" if n == "gh" else None)
    with pytest.raises(MaintainerError) as refused:
        m.contributions()
    assert refused.value.code == "no_git"
    de = refused.value.text("de")
    assert "git ist nicht installiert." in de and "Nächster Schritt:" in de


@pytest.mark.parametrize("stderr,code", [
    ("You are not logged into any GitHub hosts. To log in, run: gh auth login", "gh_login"),
    ("error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com", "offline"),
])
def test_not_logged_in_or_offline(tmp_path, stderr, code):
    m = plain_maintainer(tmp_path, lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "", stderr))
    with pytest.raises(MaintainerError) as refused:
        m.contributions()
    assert refused.value.code == code and "Next step:" in str(refused.value)


def test_credentials_in_tool_output_are_never_shown(tmp_path):
    token = "ghp_" + "Q" * 36

    def runner(cmd, **kwargs):
        if cmd[1] == "auth":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise subprocess.CalledProcessError(128, cmd, stderr=f"fatal: could not read from 'https://octo:{token}@"
                                                             "github.com/o/r': permission denied")
    with pytest.raises(MaintainerError) as refused:
        plain_maintainer(tmp_path, runner).contributions()
    text = str(refused.value)
    assert token not in text and "octo" not in text and "https://github.com/o/r" in text


def test_without_the_audit_log_nothing_runs(tmp_path):
    m = plain_maintainer(tmp_path, lambda *a, **k: pytest.fail("ran"), audit=None)
    review = SimpleNamespace(item=cmaint.Contribution(7, "t", "a", "u", "h", ["contributions/target/x.zip"], "pass"),
                             ok=True, packs=[SimpleNamespace(path="contributions/target/x.zip", sha256="0" * 64)],
                             problems=lambda: [])
    with pytest.raises(MaintainerError) as refused:
        m.publish(review, key_name=KEY)
    assert refused.value.code == "audit"
    with pytest.raises(MaintainerError) as refused:
        m.revoke("target", "x", reason="r", key_name=KEY)
    assert refused.value.code == "audit"


def test_only_the_person_and_only_a_repository():
    with pytest.raises(PermissionError):
        Maintainer(REPO, Path("x"), actor="ditto", audit=None)
    with pytest.raises(MaintainerError) as refused:
        Maintainer("", Path("x"), actor="cli", audit=None)
    assert refused.value.code == "no_repo"


def test_the_github_code_reads_no_credentials():
    source = inspect.getsource(cmaint.Maintainer) + inspect.getsource(cmaint._Run)
    for forbidden in ("environ", "getenv", "get_secret", "getpass", "env=", "GH_TOKEN", "GITHUB_TOKEN"):
        assert forbidden not in source, forbidden


def test_nothing_pushes_to_main():
    """Every push names the new branch by its explicit refspec; the branch rule never matches main."""
    tree = ast.parse(inspect.getsource(cmaint))
    pushes = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(("_git", "_run"))
              and any(isinstance(a, ast.Constant) and a.value == "push" for a in n.args)]
    assert len(pushes) == 1 and "refs/heads/{branch}" in ast.unparse(pushes[0])
    assert not cmaint.BRANCH.fullmatch("main") and not cmaint.BRANCH.fullmatch("lcf-publish-")


def test_check_state_reads_check_runs_and_statuses():
    assert cmaint.check_state(rollup("pass")) == "pass"
    assert cmaint.check_state(rollup("fail")) == "fail"
    assert cmaint.check_state(rollup("pending")) == "pending"
    assert cmaint.check_state([]) == "none" and cmaint.check_state(None) == "none"
    assert cmaint.check_state([{"context": "validate", "state": "SUCCESS"}]) == "pass"
    assert cmaint.check_state([{"context": "validate", "state": "PENDING"}]) == "pending"


def test_pack_text_is_shown_with_hidden_characters_escaped():
    shown = cmaint.shown_text("echo ok\x1b[2J\n‮evil\tx")
    assert "\x1b" not in shown and "‮" not in shown and "\\u001b" in shown and "\tx" in shown


# --- the texts -------------------------------------------------------------------------------------------------------

def test_every_text_exists_in_both_languages_with_the_same_placeholders():
    import string
    from orchestrator.utils.l10n_maintainer import TEXTS
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    fields = string.Formatter()
    for key, text in TEXTS["en"].items():
        en = {f[1] for f in fields.parse(text) if f[1]}
        de = {f[1] for f in fields.parse(TEXTS["de"][key]) if f[1]}
        assert en == de, key


def test_every_code_the_core_uses_has_its_text():
    from orchestrator.utils.l10n_maintainer import TEXTS
    tree = ast.parse(inspect.getsource(cmaint))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            value = node.args[0].value
            if name == "MaintainerError":
                used.add(f"zoo.maint.err.{value}")
            elif name in ("report", "step"):
                used.add(f"zoo.maint.step.{value}")
            elif name == "happened" and value:
                used.add(value)
    assert len(used) > 40
    assert not used - set(TEXTS["en"]), sorted(used - set(TEXTS["en"]))


# --- never an AI ----------------------------------------------------------------------------------------------------

def test_no_ai_reachable_code_reaches_the_maintainer_actions():
    """The MCP server, the tools, the provider layer, Ditto and the steps they call never import the maintainer
    module or name its actions or the keyring service of the signing key."""
    from test_deploy_tools import AI_REACHABLE
    forbidden = {"community_maintainer", "create_signing_key", "load_signing_key", "sign_index_file", "MaintainerError",
                 "l10n_maintainer", "lcf-community-index", "zoo_sign_index"}
    files = []
    for entry in AI_REACHABLE:
        path = REPO_ROOT / entry
        files += [path] if path.is_file() else sorted(path.rglob("*.py"))
    offenders = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.append(node.value)
            elif isinstance(node, ast.ImportFrom):
                names += [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            offenders += [f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {n}" for n in names
                          if any(f == n or n.endswith(f".{f}") for f in forbidden)]
    assert len(files) > 5 and offenders == []


def test_no_tool_publishes_revokes_or_signs(booted_framework):
    names = {spec.name for spec in booted_framework.tool_registry.list()}
    assert not any(word in name for name in names for word in ("maintainer", "revoke", "sign", "publish", "merge"))


# --- CLI -------------------------------------------------------------------------------------------------------------

def cli_invoke(framework, *args, input=None):
    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", "zoo", "maintainer", *args], obj=Ctx(), input=input)


@needs_git
def test_cli_key_review_publish_and_revoke(world, monkeypatch, tmp_path):
    from orchestrator import cli as cli_module
    framework = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: {"community_repo": REPO}.get(k, d)),
                                tool_registry=SimpleNamespace(audit=world.audit))

    def made(fw, actor, **kwargs):
        assert fw is framework and actor == "cli"
        world.m.progress = kwargs.get("progress")
        return world.m
    monkeypatch.setattr(cmaint, "for_framework", made)
    monkeypatch.setattr(cmaint, "_keyring", lambda keyring=None: world.keyring)
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)

    result = cli_invoke(framework, "key", "lcf-cli-2026", "--save", str(tmp_path / "pub.txt"))
    assert result.exit_code == 0, result.output
    assert "key_id:" in result.output and (tmp_path / "pub.txt").read_text().startswith("# Add to")
    assert world.keyring.get_password(cmaint.SERVICE, "lcf-cli-2026")
    result = cli_invoke(framework, "key", "lcf-cli-2026")
    assert result.exit_code == 1 and "never replaced" in result.output
    result = cli_invoke(framework, "key", "lcf-cli-2027", "--save", str(tmp_path / "pub.txt"))
    assert result.exit_code == 1 and not world.keyring.get_password(cmaint.SERVICE, "lcf-cli-2027"), \
        "an existing file is refused before a key is made"

    world.github.contribution(demo_pack(world.tmp))
    result = cli_invoke(framework, "review")
    assert result.exit_code == 0 and "#7" in result.output and "passed" in result.output, result.output
    result = cli_invoke(framework, "review", "7", "--key", KEY, input="n\n")
    assert result.exit_code == 1 and "Not published" in result.output, result.output
    assert "modules/build.sh" in result.output and "echo build" in result.output, "every file is shown"
    assert world.github.merges == []
    folder = tmp_path / "look"
    result = cli_invoke(framework, "review", "#7", "--key", KEY, "--review-dir", str(folder), "--title", "Demo",
                        input="y\n")
    assert result.exit_code == 0, result.output
    assert (folder / Path(PACK).stem / "target.yml").is_file() and "Publish?" in result.output
    assert world.github.merges == [7, 8] and "index version 1 is on main" in result.output

    result = cli_invoke(framework, "revoke", "target", "demo-board", "--version", "1.2.0", "--reason", "broken",
                        "--key", KEY, input="n\n")
    assert result.exit_code == 1 and "Not revoked" in result.output
    result = cli_invoke(framework, "revoke", "target", "demo-board", "--version", "1.2.0", "--reason", "broken",
                        input="y\n")   # the key used last
    assert result.exit_code == 0, result.output
    assert CommunityIndex.model_validate_json(world.github.main("index.json")).revoked[0].reason == "broken"


def test_cli_without_a_terminal_publishes_nothing(monkeypatch):
    """Without a person at a terminal the question cannot be asked: refused, nothing done (no --yes exists)."""
    from orchestrator import cli as cli_module
    assert "yes" not in {p.name for c in cli_module.cli.commands["zoo"].commands["maintainer"].commands.values()
                         for p in c.params}
    monkeypatch.setattr(cmaint, "for_framework", lambda fw, actor, **kw: SimpleNamespace(
        remembered_key=lambda: KEY, revoke=lambda *a, **k: pytest.fail("revoked")))
    result = cli_invoke(SimpleNamespace(), "revoke", "target", "x", "--reason", "r")
    assert result.exit_code == 1 and "Not revoked" in result.output


def test_cli_refusal_is_the_plain_reason_with_the_next_step(monkeypatch):
    def made(framework, actor, **kwargs):
        raise MaintainerError("no_gh")
    monkeypatch.setattr(cmaint, "for_framework", made)
    result = cli_invoke(SimpleNamespace(), "review")
    output = " ".join(result.output.split())
    assert result.exit_code == 1 and "The GitHub CLI (gh) is not installed." in output
    assert "Next step: install it from https://cli.github.com" in output


# --- the workflow template -------------------------------------------------------------------------------------------

def test_the_contribution_check_runs_on_every_pull_request_and_passes_one_without_a_pack(tmp_path):
    """main of the community repository requires the check, so it runs on every pull request (no paths filter, as
    the community repository's copy) and a pull request that adds no pack (the publication's: a moved pack, the
    index) passes."""
    workflow = yaml.safe_load((REPO_ROOT / "scripts" / "community_repo" / "validate-contribution.yml")
                              .read_text(encoding="utf-8"))
    trigger = workflow.get("on", workflow.get(True))
    assert "pull_request" in trigger and not (trigger["pull_request"] or {}).get("paths")
    assert workflow["jobs"]["validate"], "the job name is the required check's name"
    if shutil.which("bash") is None or not hasattr(subprocess, "run") or __import__("sys").platform == "win32":
        return
    step = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("name") == "Check every added pack")
    (tmp_path / "changed.txt").write_text("")
    done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


@needs_git
def test_the_publication_pull_request_lists_no_contribution_file(tmp_path, monkeypatch):
    """The workflow's "Changed files" step on the maintainer's pull request (the pack moved from contributions/ to
    packs/, the index and its signature changed) lists nothing under contributions/, so the check passes it."""
    if shutil.which("bash") is None or __import__("sys").platform == "win32":
        pytest.skip("runs the workflow step in bash")
    workflow = yaml.safe_load((REPO_ROOT / "scripts" / "community_repo" / "validate-contribution.yml")
                              .read_text(encoding="utf-8"))
    step = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("name") == "Changed files")
    github = FakeGitHub(tmp_path, monkeypatch)
    github.commit({f"contributions/target/{PACK}": b"PK pack"}, "refs/heads/main")
    work = tmp_path / "work"
    git("clone", "-q", str(github.origin), str(work))
    git("-C", str(work), "switch", "-q", "-c", "lcf-publish-x")
    (work / "packs" / "target").mkdir(parents=True)
    git("-C", str(work), "mv", f"contributions/target/{PACK}", f"packs/target/{PACK}")
    (work / "index.json").write_text("{}")
    git("-C", str(work), "add", "index.json")
    git("-C", str(work), "commit", "-q", "-m", "publish")
    listed = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, capture_output=True, text=True,
                            env={**__import__("os").environ, "BASE_REF": "main"})
    assert listed.returncode == 0, listed.stderr
    assert (work / "changed.txt").read_text() == ""


# --- GUI -------------------------------------------------------------------------------------------------------------

def wait_tasks(owner, qapp):
    for _ in range(200):
        tasks = [t for t in getattr(owner, "_tasks", ()) if t.isRunning()]
        for task in tasks:
            task.wait(5000)
        qapp.processEvents()
        if not any(t.isRunning() for t in getattr(owner, "_tasks", ())):
            qapp.processEvents()
            return


@needs_git
def test_hub_maintainer_tab(qapp, world, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import community_hub, community_maintainer as panel_module, plain

    asked = []
    answers = {"publish": QMessageBox.No}

    def fake_exec(box):
        asked.append((box.text(), box.defaultButton() is box.button(QMessageBox.No)))
        return answers["publish"]
    monkeypatch.setattr(plain, "_exec", fake_exec)
    monkeypatch.setattr(panel_module, "_maintainer", lambda framework, progress: world.m.__setattr__(
        "progress", progress) or world.m)
    monkeypatch.setattr(cmaint, "_keyring", lambda keyring=None: world.keyring)
    framework = SimpleNamespace(community_manager=None, target_manager=None, secrets_manager=None,
                                config=SimpleNamespace(get=lambda k, d=None: {"community_repo": REPO}.get(k, d)))
    hub = community_hub.CommunityHubWindow(framework)
    try:
        panel = hub.maintainer_panel
        assert hub.tabs.tabText(hub.tabs.indexOf(panel)) == "Maintainer"
        for name in ("btn_zoo_maint_key", "btn_zoo_maint_load", "btn_zoo_maint_review", "btn_zoo_maint_publish",
                     "btn_zoo_maint_revoke", "btn_zoo_maint_cancel", "lst_zoo_maint_files", "txt_zoo_maint_file"):
            assert panel.findChild(object, name) is not None, name
        assert panel.file_view.isReadOnly() and not panel.publish_btn.isEnabled()

        world.github.contribution(demo_pack(world.tmp))
        panel.key_edit.setText(KEY)
        panel.load_contributions()
        wait_tasks(panel, qapp)
        assert panel.table.rowCount() == 1 and panel.table.item(0, 0).text() == "#7"
        panel.table.selectRow(0)
        panel.review_selected()
        wait_tasks(panel, qapp)
        files = [panel.file_list.item(i).text() for i in range(panel.file_list.count())]
        assert any(f.endswith("modules/build.sh") for f in files) and any(f.endswith("pack.json") for f in files)
        row = next(i for i, f in enumerate(files) if f.endswith("modules/build.sh"))
        panel.file_list.setCurrentRow(row)
        assert "echo build" in panel.file_view.toPlainText()
        assert panel.publish_btn.isEnabled()

        panel.publish()   # "No": nothing happens
        assert asked and asked[-1][1] and "Publish?" in asked[-1][0]
        wait_tasks(panel, qapp)
        assert world.github.merges == []
        answers["publish"] = QMessageBox.Yes
        panel.publish()
        wait_tasks(panel, qapp)
        assert world.github.merges == [7, 8], panel.log.toPlainText()
        assert "index version 1" in panel.log.toPlainText().lower()
    finally:
        hub.close()
