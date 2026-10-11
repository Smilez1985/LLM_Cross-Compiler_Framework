"""The repository's secret scan (scripts/secret_scan.py), the pattern table it shares with runtime redaction
(orchestrator/security/secret_patterns.py), and its git hooks."""

import ast
import importlib.util
import io
import json
import random
import shutil
import string
import subprocess
import sys
import time

import pytest

from conftest import REPO_ROOT
from orchestrator.security import secret_patterns
from orchestrator.utils import helpers

spec = importlib.util.spec_from_file_location("secret_scan", REPO_ROOT / "scripts" / "secret_scan.py")
secret_scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secret_scan)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
ALNUM = string.ascii_letters + string.digits


def body(n, alphabet=ALNUM, seed=0):
    # Random-looking key material, built at runtime so this file itself stays clean.
    return "".join(random.Random(seed).choices(alphabet, k=n))


# One synthetic positive per rule of the scan; test_every_rule_has_a_sample keeps this complete.
SAMPLES = {
    "Anthropic key": "sk-ant-" + "api03-" + body(60),
    "OpenAI key": "sk-" + "proj-" + body(48),
    "OpenRouter key": "sk-or-" + "v1-" + body(64, "0123456789abcdef"),
    "Unsloth Studio key": "sk-" + "unsloth-" + body(32, "0123456789abcdef"),
    "Hugging Face token": "hf" + "_" + body(34),
    "GitHub token": "gh" + "p_" + body(36),
    "GitLab token": "glp" + "at-" + body(20),
    "AWS access key id": "AK" + "IA" + body(16, string.ascii_uppercase + string.digits),
    "AWS secret key": "aws_secret_" + "access_key = " + body(40),
    "Bedrock API key": "AB" + "SK" + body(120, ALNUM + "+/"),
    "Bedrock short-term key": "bedrock-" + "api-key-" + body(160, ALNUM + "+/") + "&Version=1",
    "Google API key": "AI" + "za" + body(35),
    "Google OAuth secret": "GOC" + "SPX-" + body(28),
    "Groq key": "gs" + "k_" + body(52),
    "xAI key": "xa" + "i-" + body(80),
    "Perplexity key": "pp" + "lx-" + body(48),
    "NVIDIA key": "nva" + "pi-" + body(64),
    "Cerebras key": "cs" + "k-" + body(48, string.ascii_lowercase + string.digits),
    "Replicate token": "r8" + "_" + body(36),
    "Fireworks key": "fw" + "_" + body(30),
    "LocalAI key": "la" + "i-" + body(32),
    "Slack token": "xo" + "xb-" + body(40),
    "Stripe key": "sk" + "_live_" + body(30),
    "PyPI token": "pypi-" + "AgE" + body(60),
    "age secret key": "AGE-SECRET-" + "KEY-1" + body(58, "QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L"),
    "JSON web token": "ey" + "J" + body(24) + ".ey" + "J" + body(40) + "." + body(43),
    "private key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "certificate": "-----BEGIN " + "CERTIFICATE-----",
    "URL with password": "https://deploy:" + body(16) + "@registry.internal.corp",
    "Authorization header": "Authorization: " + "Bearer " + body(40),
    "secret assignment": "client_" + "secret: '" + body(32) + "'",
}
# Rules only runtime redaction uses (too broad for the scan, or spanning lines).
REDACT_ONLY_SAMPLES = {
    "sk- key": "sk-" + body(32, "0123456789abcdef"),
    "private key block": ("-----BEGIN RSA " + "PRIVATE KEY-----\n" + body(64, ALNUM + "+/") + "\n"
                          + body(64, ALNUM + "+/") + "\n-----END RSA " + "PRIVATE KEY-----"),
    "Bearer token": "rejected bearer " + body(40, ALNUM + "._-"),
    "quoted secret": '{"access_' + 'token": "ya29.' + body(60, ALNUM + "._-") + '"}',
    "environment variable": "export LLAMA_" + "API_KEY=" + body(28, ALNUM + "-_"),
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


def test_scan_and_runtime_redaction_use_one_table():
    assert secret_scan.PATTERNS is secret_patterns.PATTERNS is helpers.SECRET_PATTERNS
    assert secret_scan.PLACEHOLDER is secret_patterns.PLACEHOLDER


def test_every_rule_has_a_sample():
    assert set(SAMPLES) == {p.name for p in secret_patterns.PATTERNS if p.scan}
    assert set(REDACT_ONLY_SAMPLES) == {p.name for p in secret_patterns.PATTERNS if not p.scan}


def test_the_pattern_table_needs_only_the_standard_library():
    tree = ast.parse(secret_scan.PATTERN_TABLE.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert {name.split(".")[0] for name in imported} <= sys.stdlib_module_names


def test_the_scan_runs_standalone_and_loads_the_table_by_path(tmp_path):
    leak = tmp_path / "settings.py"
    leak.write_text(f'KEY = "{SAMPLES["LocalAI key"]}"\n')
    # -I -S: no site-packages and no script directory on sys.path, as in a bare checkout without the framework.
    run = subprocess.run([sys.executable, "-I", "-S", str(REPO_ROOT / "scripts" / "secret_scan.py"),
                          "--repo", str(tmp_path), str(leak)], capture_output=True, text=True)
    assert run.returncode == 1 and "LocalAI key" in run.stderr, run.stderr


def test_the_scan_refuses_to_run_without_the_table(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "secret_scan.py", tmp_path / "scripts" / "secret_scan.py")
    run = subprocess.run([sys.executable, "-I", "-S", str(tmp_path / "scripts" / "secret_scan.py"),
                          "--repo", str(tmp_path)], capture_output=True, text=True)
    assert run.returncode == 2 and "cannot load the pattern table" in run.stderr


@pytest.mark.parametrize("broken", ["<<<<<<< HEAD\nPATTERNS = ()\n=======\n", "raise RuntimeError('x')\n",
                                    "PLACEHOLDER = None\n"])
def test_the_scan_refuses_to_run_with_a_broken_table(tmp_path, broken):
    # A table with merge conflict markers, one that fails on import, or one without PATTERNS: a usage error
    # (exit 2) with a clear message, never a traceback that reads like findings.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "orchestrator" / "security").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "secret_scan.py", tmp_path / "scripts" / "secret_scan.py")
    (tmp_path / "orchestrator" / "security" / "secret_patterns.py").write_text(broken)
    run = subprocess.run([sys.executable, "-I", "-S", str(tmp_path / "scripts" / "secret_scan.py"),
                          "--repo", str(tmp_path)], capture_output=True, text=True)
    assert run.returncode == 2 and "cannot load the pattern table" in run.stderr
    assert "Traceback" not in run.stderr


def test_a_key_is_reported_once_under_its_own_name():
    line = (f'OPENAI_API_KEY = "{SAMPLES["OpenAI key"]}"  # sent as '
            f'Authorization: Bearer {SAMPLES["JSON web token"]}')
    assert [f.kind for f in secret_scan.scan_text("f.py", line)] == ["OpenAI key", "JSON web token"]


@pytest.mark.parametrize("wrapped, kind", [
    (repr("token:\n" + SAMPLES["Hugging Face token"]), "Hugging Face token"),
    ("https://maps.invalid/api?q=1%20" + SAMPLES["Google API key"], "Google API key"),
    (repr(b"\x00" + SAMPLES["Hugging Face token"].encode()), "Hugging Face token"),
    (json.dumps(" " + SAMPLES["GitHub token"]), "GitHub token"),
    # JSON inside a JSON string: the quotes are escaped
    (json.dumps(json.dumps({"api_key": body(32)})), "secret assignment"),
    (json.dumps(json.dumps({"Authorization": "Bearer " + body(40)})), "Authorization header"),
])
def test_keys_after_escapes_are_found(wrapped, kind):
    assert [f.kind for f in secret_scan.scan_text("f.py", wrapped)] == [kind]
    assert "REDACTED" in helpers.redact_secrets(wrapped)


@pytest.mark.parametrize("line", [
    'key = "sk-ant-api03-' + "EXAMPLE" + body(40) + '"',
    'key = "hf_' + "abcdefghij" + body(24) + '"',
    "url = 'https://admin:" + body(12) + "@qdrant.example'",
    'key = "' + SAMPLES["OpenAI key"] + '"  # secret-scan: allow',
    'key = "sk-unsloth-' + "EXAMPLE" + body(24) + '"',
    'key = "lai-' + "x" * 30 + '"',
    'identity = "AGE-SECRET-KEY-1' + "X" * 58 + '"',  # the placeholder the documentation uses
    'token = "bedrock-api-key-' + "EXAMPLE" + body(60) + '"',
    "Authorization: Bearer <paste-your-token-here>",
    'curl -H "Authorization: Bearer ' + "X" * 40 + '"',
    'api_key = "your-api-key-' + "x" * 20 + '"',
    'api_key="sk-test-' + '1234567890abcdef"',  # the shape of the test fakes already in the history
    'value = "' + REDACT_ONLY_SAMPLES["sk- key"] + '"',  # redacted at runtime, too broad for the scan
    # The names of secrets, not their values (a community pack branch keeps such constants).
    '    ENCRYPTION_SECRET = "community_' + 'encryption_key"',
    "api_key: 'openai-compatible-" + "server-key'",
])
def test_placeholders_and_marked_lines_are_not_reported(line):
    assert secret_scan.scan_text("f.py", line) == []


def test_a_lower_case_key_with_digits_is_still_an_assignment():
    value = body(32, string.ascii_lowercase + string.digits, seed=3)
    assert any(c.isdigit() for c in value)
    hits = secret_scan.scan_text("f.py", 'client_secret = "' + value + '"')
    assert [h.kind for h in hits] == ["secret assignment"]
    assert value not in helpers.redact_secrets('client_secret = "' + value + '"')


# --- Exact formats that GitHub push protection blocks ----------------------------------------------------------

LETTERS = string.ascii_lowercase
# Values that look fake (the placeholder heuristic skips every one of them) but have a provider's exact token
# format, which GitHub's push protection blocks anyway. Built at runtime, so this file holds no such literal.
EXACT_SAMPLES = [
    ("Hugging Face token", "hf" + "_" + LETTERS + LETTERS.upper()[:8]),  # the value push protection blocked
    ("Hugging Face token", "api" + "_org_" + LETTERS + LETTERS[:8]),
    ("GitHub token", "gh" + "p_" + LETTERS + "0123456789"),  # was in tests/test_security.py
    ("GitHub token", "gh" + "s_" + "x" * 36),
    ("GitHub token", "github" + "_pat_" + "EXAMPLE" + "x" * 75),
    ("AWS access key id", "AK" + "IA" + "EXAMPLE" + "X" * 9),
    ("AWS access key id", "AS" + "IA" + "0" * 16),
    ("Slack token", "xo" + "xb-" + "1234567890-1234567890-" + LETTERS[:24]),
    ("Slack token", "xo" + "xp-" + "1234567890-" * 3 + "x" * 32),
    ("GitLab token", "glp" + "at-" + "x" * 20),
    ("GitLab token", "glp" + "at-" + "EXAMPLE" + "x" * 20 + "." + "01" + "abcdefg"),  # routable
    ("Stripe key", "sk" + "_live_" + "EXAMPLE" + "x" * 17),
    ("Stripe key", "rk" + "_test_" + "1234567890" * 3),
    ("PyPI token", "pypi" + "-AgEIcHlwaS5vcmc" + "EXAMPLE" + "x" * 70),
    ("OpenAI key", "sk-" + "proj-" + "x" * 74 + "T3Blbk" + "FJ" + "x" * 74),
    ("OpenAI key", "sk-" + "svcacct-" + "x" * 58 + "T3Blbk" + "FJ" + "x" * 58),
    ("OpenAI key", "sk-" + "x" * 20 + "T3Blbk" + "FJ" + "x" * 20),
    ("Anthropic key", "sk-ant-" + "api03-" + "EXAMPLE" + "x" * 86 + "AA"),
    ("Anthropic key", "sk-ant-" + "admin01-" + "x" * 93 + "AA"),
    ("OpenRouter key", "sk-or-" + "v1-" + "0" * 64),
    ("Groq key", "gs" + "k_" + "x" * 52),
    ("xAI key", "xa" + "i-" + "EXAMPLE" + "x" * 73),
    ("Perplexity key", "pp" + "lx-" + "abcdefghij" * 4 + "abcdefgh"),
    ("Replicate token", "r8" + "_" + "EXAMPLE" + "x" * 30),
]
# One character too few or too many, or one the format does not allow: these behave as before the exact formats.
NEAR_MISSES = [
    "hf" + "_" + LETTERS + LETTERS.upper()[:7],  # 33 letters
    "hf" + "_" + LETTERS + LETTERS.upper()[:9],  # 35 letters
    "hf" + "_" + LETTERS + "ABCD1234",  # digits in a Hugging Face token
    "hf" + "_" + body(34),  # digits, and no placeholder: reported by the Hugging Face rule
    "gh" + "p_" + LETTERS + "012345678",
    "gh" + "p_" + LETTERS + "0123456789a",
    "gh" + "p_" + body(37),  # reported by the GitHub rule
    "AK" + "IA" + "0" * 15,
    "AK" + "IA" + "0" * 17,
    "xo" + "xb-" + "123456789-1234567890-" + "x" * 24,  # a team id of 9 digits
    "xo" + "xb-" + body(40),  # reported by the Slack rule
    "glp" + "at-" + "x" * 21,
    "sk" + "_live_" + "x" * 19,
    "pypi" + "-AgEIcHlwaS5vcmc" + "x" * 69,
    "sk-" + "proj-" + "x" * 74 + "T3Blbk" + "FJ" + "x" * 73,
    "sk-ant-" + "api03-" + "x" * 92 + "AA",
    "sk-ant-" + "api03-" + "x" * 93 + "AB",
    "sk-ant-" + "api03-" + body(95),  # reported by the Anthropic rule
    "sk-or-" + "v1-" + "0" * 63,
    "gs" + "k_" + "x" * 53,
    "pp" + "lx-" + "x" * 47,
]


def test_every_exact_format_has_samples():
    assert {kind for kind, _ in EXACT_SAMPLES} == {f.name for f in secret_patterns.EXACT_FORMATS}
    assert secret_scan.EXACT_FORMATS is secret_patterns.EXACT_FORMATS


@pytest.mark.parametrize("kind, value", EXACT_SAMPLES)
def test_an_exact_format_is_reported_although_it_looks_fake(kind, value):
    line = f'value = "{value}"'
    assert secret_patterns.find_secrets(line) == [], "the placeholder heuristic alone lets it through"
    findings = secret_scan.scan_text("f.py", line)
    assert [(f.kind, f.exact) for f in findings] == [(kind, True)]
    assert value[8:] not in str(findings[0]) and "GitHub will block the push" in str(findings[0])


@pytest.mark.parametrize("kind, value", EXACT_SAMPLES)
def test_the_marker_does_not_silence_an_exact_format(kind, value):
    # GitHub does not know the marker, so it cannot excuse such a value.
    findings = secret_scan.scan_text("f.py", f'value = "{value}"  # {secret_scan.ALLOW_PRAGMA}')
    assert [(f.kind, f.exact) for f in findings] == [(kind, True)]


@pytest.mark.parametrize("kind, value", EXACT_SAMPLES)
def test_an_exact_format_is_no_redaction_gap(kind, value):
    # Scan only, but never something runtime redaction misses: the rule of the same name blanks the whole value.
    rule = next(p for p in secret_patterns.PATTERNS if p.name == kind)
    assert rule.redact and rule.regex.search(value).group(0) == value
    assert value not in helpers.redact_secrets(f"request failed: {value}\n")


@pytest.mark.parametrize("kind, value", EXACT_SAMPLES)
def test_a_value_split_into_literals_is_not_reported(kind, value):
    # How tests write such a value: the same string at runtime, but no exact-format text in the file.
    adjacent = f'"{value[:3]}" "{value[3:]}"'
    assert ast.literal_eval(adjacent) == value
    for source in (f"TOKEN = {adjacent}", f'TOKEN = "{value[:3]}" + "{value[3:]}"'):
        assert secret_scan.scan_text("f.py", source) == []


def test_a_real_looking_value_in_an_exact_format_is_reported_once():
    value = "gh" + "p_" + body(36)
    findings = secret_scan.scan_text("f.py", f'token = "{value}"  # Authorization: Bearer {value}')
    assert [(f.kind, f.exact) for f in findings] == [("GitHub token", True), ("GitHub token", True)]
    findings = secret_scan.scan_text("f.py", f'token = "{value}"')  # the GitHub rule and the assignment rule match
    assert [(f.kind, f.exact) for f in findings] == [("GitHub token", True)]


@pytest.mark.parametrize("value", NEAR_MISSES)
def test_near_misses_of_an_exact_format_behave_as_before(value):
    line = f'value = "{value}"'
    assert secret_patterns.find_exact_formats(line) == []
    findings = secret_scan.scan_text("f.py", line)
    assert [(f.kind, f.exact) for f in findings] == [(kind, False) for kind, _ in secret_patterns.find_secrets(line)]
    assert secret_scan.scan_text("f.py", f"{line}  # {secret_scan.ALLOW_PRAGMA}") == []


@pytest.mark.parametrize("text", ["glp" + "at-" * 20000, "sk-" + "proj-sk-" * 15000, "xo" + "xb-1234567890-" * 8000,
                                  "sk-ant-" + "api03-sk-ant-" * 8000, "pypi" + "-pypi" * 20000, "AK" + "IA" * 30000])
def test_exact_formats_stay_fast_on_adversarial_text(text):
    # The scan reads every line of every commit in CI: never quadratic.
    start = time.perf_counter()
    secret_patterns.find_exact_formats(text)
    assert time.perf_counter() - start < 1.0


def test_near_misses_include_values_the_ordinary_rules_report():
    reported = [v for v in NEAR_MISSES if secret_patterns.find_secrets(f'value = "{v}"')]
    assert len(reported) == 4 and len(NEAR_MISSES) - len(reported) > 10


def test_exact_formats_are_reported_in_staged_tracked_and_push_mode(repo, capsys, monkeypatch):
    marked = "".join(f'FAKE_{i} = "{value}"  # {secret_scan.ALLOW_PRAGMA}\n'
                     for i, (_, value) in enumerate(EXACT_SAMPLES))
    (repo / "test_fakes.py").write_text(marked)
    git(repo, "add", "test_fakes.py")
    expected = [(number, kind, True) for number, (kind, _) in enumerate(EXACT_SAMPLES, 1)]
    assert [(f.line, f.kind, f.exact) for f in secret_scan.scan_staged(repo, [])] == expected
    assert [(f.line, f.kind, f.exact) for f in secret_scan.scan_tracked(repo, [])] == expected

    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "GitHub push protection will block a push" in err
    assert f"it ignores the marker '{secret_scan.ALLOW_PRAGMA}': that marker does not silence" in err
    assert '"hf_" "abc..."' in err and '"hf_" + "abc..."' in err and "does not match the exact format" in err
    assert "Documented fake values may carry the marker" not in err, "the marker is no way out here"
    assert secret_scan.main(["--repo", str(repo)]) == 1

    git(repo, "commit", "-qm", "fakes", "--no-verify")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1

    # Split into adjacent literals, the same values pass.
    split = "".join(f'FAKE_{i} = "{value[:3]}" "{value[3:]}"\n' for i, (_, value) in enumerate(EXACT_SAMPLES))
    (repo / "test_fakes.py").write_text(split)
    git(repo, "add", "test_fakes.py")
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 0
    assert secret_scan.main(["--repo", str(repo)]) == 0


def test_mixed_findings_show_both_hints(capsys, tmp_path):
    leak = tmp_path / "mixed.py"
    leak.write_text(f'A = "{EXACT_SAMPLES[0][1]}"\nB = "{SAMPLES["LocalAI key"]}"\n')
    assert secret_scan.main(["--repo", str(tmp_path), str(leak)]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "Documented fake values may carry the marker" in err and "GitHub push protection will block" in err


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


def test_push_mode_under_the_pre_commit_tool_reads_the_pushed_ref(repo, monkeypatch):
    # pre-commit reads git's stdin itself; a hook gets an empty stdin and the pushed commit in the environment.
    (repo / "a.py").write_text(f'KEY = "{SAMPLES["Hugging Face token"]}"\n')
    git(repo, "add", "a.py")
    git(repo, "commit", "-qm", "leak", "--no-verify")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    branch = git(repo, "symbolic-ref", "HEAD").stdout.strip()
    for name in ("PRE_COMMIT_TO_REF", "PRE_COMMIT_FROM_REF", "PRE_COMMIT_LOCAL_BRANCH"):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 0, "nothing named, nothing to scan"
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "0" * 40)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", head)
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1
    monkeypatch.delenv("PRE_COMMIT_TO_REF")
    monkeypatch.setenv("PRE_COMMIT_LOCAL_BRANCH", branch)  # a push that contains the root commit
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1
    assert secret_scan.pre_commit_push_revs({"PRE_COMMIT_LOCAL_BRANCH": "--all"}) == [], "never an option"


def test_the_pre_commit_tool_runs_the_same_push_scan_as_the_git_hook():
    import yaml
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    assert hooks["secret-scan-push"]["entry"] == "python scripts/secret_scan.py --push"
    assert hooks["secret-scan-push"]["stages"] == ["pre-push"]
    assert hooks["secret-scan"]["entry"] == "python scripts/secret_scan.py --staged"
    assert "secret_scan.py\" --push" in (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")


def test_the_pre_commit_hook_refuses_a_commit_with_a_key(repo):
    (repo / "scripts").mkdir()
    (repo / "orchestrator" / "security").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "secret_scan.py", repo / "scripts" / "secret_scan.py")
    shutil.copy(secret_scan.PATTERN_TABLE, repo / "orchestrator" / "security" / "secret_patterns.py")
    (repo / "config.py").write_text(f'API_KEY = "{SAMPLES["OpenAI key"]}"\n')
    git(repo, "add", "config.py", "scripts/secret_scan.py", "orchestrator/security/secret_patterns.py")
    hooks = f"core.hooksPath={REPO_ROOT / '.githooks'}"
    refused = subprocess.run(["git", "-C", str(repo), "-c", hooks, "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                              "commit", "-qm", "leak"], capture_output=True, text=True)
    assert refused.returncode != 0 and "OpenAI key" in refused.stderr
    (repo / "config.py").write_text("import os\nAPI_KEY = os.environ.get('OPENAI_API_KEY')\n")
    git(repo, "add", "config.py")
    git(repo, "-c", hooks, "commit", "-qm", "clean")
