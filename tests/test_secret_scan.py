"""The repository's secret scan (scripts/secret_scan.py) and its git hooks."""

import importlib.util
import io
import random
import shutil
import string
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

spec = importlib.util.spec_from_file_location("secret_scan", REPO_ROOT / "scripts" / "secret_scan.py")
secret_scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secret_scan)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def body(n, alphabet=string.ascii_letters + string.digits, seed=0):
    # Random-looking key material, built at runtime so this file itself stays clean.
    return "".join(random.Random(seed).choices(alphabet, k=n))


SAMPLES = {
    "Anthropic key": "sk-ant-" + "api03-" + body(60),
    "OpenAI key": "sk-" + "proj-" + body(48),
    "OpenRouter key": "sk-or-" + "v1-" + body(64, "0123456789abcdef"),
    "Hugging Face token": "hf" + "_" + body(34),
    "GitHub token": "gh" + "p_" + body(36),
    "AWS access key id": "AK" + "IA" + body(16, string.ascii_uppercase + string.digits),
    "Google API key": "AI" + "za" + body(35),
    "Groq key": "gs" + "k_" + body(52),
    "private key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "certificate": "-----BEGIN " + "CERTIFICATE-----",
    "URL with password": "https://deploy:" + body(16) + "@registry.internal.corp",
}


def git(repo, *args, stdin=None):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", *args],
                          input=stdin, capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    return path


def test_the_repository_contains_no_credentials():
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert secret_scan.main(["--repo", str(REPO_ROOT)]) == 0


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_each_kind_of_credential_is_found(kind):
    findings = secret_scan.scan_text("f.py", f'value = "{SAMPLES[kind]}"\n')
    assert [f.kind for f in findings] == [kind]
    assert SAMPLES[kind][8:] not in str(findings[0])  # reported redacted


@pytest.mark.parametrize("line", [
    'key = "sk-ant-api03-' + "EXAMPLE" + body(40) + '"',
    'key = "hf_' + "abcdefghij" + body(24) + '"',
    "url = 'https://admin:" + body(12) + "@qdrant.example'",
    'key = "' + SAMPLES["OpenAI key"] + '"  # secret-scan: allow',
])
def test_placeholders_and_marked_lines_are_not_reported(line):
    assert secret_scan.scan_text("f.py", line) == []


@pytest.mark.parametrize("path,hit", [
    (".env", True), ("app/.env.local", True), (".env.example", False), ("configs/secrets.store", True),
    ("certs/ca-bundle.crt", True), ("home/.ssh/id_ed25519", True), ("deploy/server.pem", True),
    ("state/audit.db", True), ("orchestrator/tools/audit.py", False), ("tests/fixtures/server.pem", False),
])
def test_files_that_hold_credentials_are_refused_by_name(path, hit):
    assert (secret_scan.check_name(path) is not None) == hit


def test_local_patterns_are_found_but_never_printed(repo, capsys):
    personal = "jane.doe@personal-mail.net"
    (repo / "notes.md").write_text(f"contact: {personal}\n")
    git(repo, "add", "notes.md")
    assert secret_scan.main(["--repo", str(repo)]) == 0
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "secret-patterns").write_text(f"# personal\n{personal.upper()}\n")
    assert secret_scan.main(["--repo", str(repo)]) == 1
    err = capsys.readouterr().err
    assert "notes.md:1: local pattern #1" in err and personal not in err.lower()


def test_staged_mode_checks_the_index_not_the_working_tree(repo):
    target = repo / "settings.py"
    target.write_text(f'TOKEN = "{SAMPLES["GitHub token"]}"\n')
    git(repo, "add", "settings.py")
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 1
    target.write_text("TOKEN = None\n")  # fixed in the working tree only: the commit would still leak it
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 1
    git(repo, "add", "settings.py")
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 0


def test_history_mode_finds_a_key_removed_in_a_later_commit(repo):
    (repo / "a.py").write_text(f'KEY = "{SAMPLES["Anthropic key"]}"\n')
    git(repo, "add", "a.py")
    git(repo, "commit", "-qm", "add", "--no-verify")
    (repo / "a.py").write_text("KEY = None\n")
    git(repo, "commit", "-qam", "remove", "--no-verify")
    assert secret_scan.main(["--repo", str(repo)]) == 0
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1


def test_push_mode_scans_only_commits_not_on_a_remote(repo, monkeypatch):
    (repo / "a.py").write_text(f'KEY = "{SAMPLES["Hugging Face token"]}"\n')
    git(repo, "add", "a.py")
    git(repo, "commit", "-qm", "leak", "--no-verify")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    zero = "0" * 40
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {head} refs/heads/main {zero}\n"))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"(delete) {zero} refs/heads/old {head}\n"))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 0


def test_the_pre_commit_hook_refuses_a_commit_with_a_key(repo):
    (repo / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "secret_scan.py", repo / "scripts" / "secret_scan.py")
    (repo / "config.py").write_text(f'API_KEY = "{SAMPLES["OpenAI key"]}"\n')
    git(repo, "add", "config.py", "scripts/secret_scan.py")
    hooks = f"core.hooksPath={REPO_ROOT / '.githooks'}"
    refused = subprocess.run(["git", "-C", str(repo), "-c", hooks, "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                              "commit", "-qm", "leak"], capture_output=True, text=True)
    assert refused.returncode != 0 and "OpenAI key" in refused.stderr
    (repo / "config.py").write_text("import os\nAPI_KEY = os.environ.get('OPENAI_API_KEY')\n")
    git(repo, "add", "config.py")
    git(repo, "-c", hooks, "commit", "-qm", "clean")
