"""The repository's secret scan (scripts/secret_scan.py), the pattern table it shares with runtime redaction
(orchestrator/security/secret_patterns.py), and its git hooks."""

import ast
import hashlib
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


# Letters of other scripts are word characters for Python's \w and \b, but not for GitHub's detector or gitleaks
# (whose \b is ASCII): a token glued to "é" or to Chinese text is still a token, for the scan and for redaction.
NON_ASCII_NEIGHBOURS = ["é{}é", "令牌{}无效", "Ä{}ß", " {} "]


@pytest.mark.parametrize("kind, value", EXACT_SAMPLES)
def test_an_exact_format_next_to_letters_of_other_scripts_is_reported_and_redacted(kind, value):
    for context in NON_ASCII_NEIGHBOURS:
        text = context.format(value)
        assert secret_patterns.find_exact_formats(text) == [(kind, value)], context
        assert [(f.kind, f.exact) for f in secret_scan.scan_text("notes.md", text)] == [(kind, True)], context
        assert value not in helpers.redact_secrets(f"request failed: {text}\n"), context


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


def stage(repo, path, content, mode="100644"):
    """Put content into the index under path, without the file system (any name, symlinks on every platform)."""
    sha = git(repo, "hash-object", "-w", "--stdin", stdin=content).stdout.strip()
    git(repo, "-c", "core.protectNTFS=false", "update-index", "-z", "--add", "--index-info",
        stdin=f"{mode} {sha}\t{path}\0")


@pytest.mark.parametrize("name", ["line\nbreak.py", "0:stage.py", "2:x.py", "with space.py", "née.py"])
def test_staged_mode_scans_files_whatever_their_name(repo, name, monkeypatch):
    # A name is never read as a revision (":0:stage.py" is stage 0 of "stage.py") or split at a line break.
    if sys.platform == "win32" and any(c in name for c in ":\n"):
        pytest.skip("git for Windows refuses such a name in the index")
    kind, value = EXACT_SAMPLES[0]
    stage(repo, name, f'TOKEN = "{value}"\nKEY = "{SAMPLES["LocalAI key"]}"\n')
    findings = secret_scan.scan_staged(repo, [])
    assert [(f.where, f.line, f.kind, f.exact) for f in findings] == [
        (f"{name} (staged)", 1, kind, True), (f"{name} (staged)", 2, "LocalAI key", False)]
    git(repo, "commit", "-qm", "odd name", "--no-verify")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1


def test_staged_mode_scans_a_file_that_replaced_a_symlink(repo):
    # A type change (symlink -> file) is neither added nor modified for git diff's filter.
    stage(repo, "link.py", "target.py", mode="120000")
    git(repo, "commit", "-qm", "link", "--no-verify")
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 0
    stage(repo, "link.py", f'TOKEN = "{EXACT_SAMPLES[2][1]}"\n')
    assert "T\tlink.py" in git(repo, "diff", "--cached", "--name-status").stdout
    assert [(f.where, f.kind, f.exact) for f in secret_scan.scan_staged(repo, [])] == [
        ("link.py (staged)", "GitHub token", True)]


def test_two_staged_files_with_the_same_content_are_both_reported(repo):
    for name in ("a.py", "b.py"):
        stage(repo, name, f'TOKEN = "{EXACT_SAMPLES[0][1]}"\n')
    assert [f.where for f in secret_scan.scan_staged(repo, [])] == ["a.py (staged)", "b.py (staged)"]


def test_a_text_file_too_big_for_the_full_scan_is_still_checked_for_exact_formats(repo, monkeypatch):
    # GitHub reads files of any size in a push up to 50 MB; only the cheap exact-format check runs on them.
    monkeypatch.setattr(secret_scan, "MAX_BYTES", 1000)
    big = "x = 1\n" * 300 + f'TOKEN = "{EXACT_SAMPLES[0][1]}"  # {secret_scan.ALLOW_PRAGMA}\n'
    big += f'KEY = "{SAMPLES["LocalAI key"]}"\n'
    findings = secret_scan.scan_blob("big.py", big.encode(), [])
    assert [(f.line, f.kind, f.exact) for f in findings] == [(301, EXACT_SAMPLES[0][0], True)]
    assert secret_scan.scan_blob("big.bin", b"\0" + big.encode(), []) == []  # binary: skipped as before
    (repo / "big.py").write_text(big)
    stage(repo, "big.py", big)
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 1
    assert secret_scan.main(["--repo", str(repo)]) == 1


def test_mixed_findings_show_both_hints(capsys, tmp_path):
    leak = tmp_path / "mixed.py"
    leak.write_text(f'A = "{EXACT_SAMPLES[0][1]}"\nB = "{SAMPLES["LocalAI key"]}"\n')
    assert secret_scan.main(["--repo", str(tmp_path), str(leak)]) == 1
    err = " ".join(capsys.readouterr().err.split())
    assert "Documented fake values may carry the marker" in err and "GitHub push protection will block" in err


# --- The allowlist of test mocks in old commits (.secret-scan-allow) ---------------------------------------------

# The two test mocks the history holds, built at runtime: the allowlist names them by their digest, and neither it
# nor this file may hold them as one piece.
HISTORY_MOCKS = {
    "GitHub token": "gh" + "p_" + LETTERS + "0123456789",  # old versions of tests/test_security.py
    "Hugging Face token": "hf" + "_" + LETTERS + LETTERS.upper()[:8],  # old local commits of tests/test_cli_builds.py
}
# The same formats, one detail off: each is another value and must still be reported.
MOCK_VARIANTS = [
    "gh" + "p_" + LETTERS + "0123456788",
    "gh" + "o_" + LETTERS + "0123456789",
    "gh" + "p_" + LETTERS.upper() + "0123456789",
    "hf" + "_" + LETTERS + LETTERS.upper()[:7] + "I",
    "hf" + "_" + LETTERS.upper() + LETTERS[:8],
    "api" + "_org_" + LETTERS + LETTERS.upper()[:8],
]


def hint(value):
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


def allow(repo, *values, extra=""):
    lines = ["# documented test mocks"] + [f"{secret_scan.digest(v)}  {hint(v)}: a test mock" for v in values]
    (repo / secret_scan.ALLOWLIST).write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")


def commit(repo, name, content):
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"add {name}", "--no-verify")


def test_the_history_mocks_are_exact_formats_the_samples_cover():
    for kind, value in HISTORY_MOCKS.items():
        assert secret_patterns.find_exact_formats(f'"{value}"') == [(kind, value)]
        assert (kind, value) in EXACT_SAMPLES
    for value in MOCK_VARIANTS:
        assert len(secret_patterns.find_exact_formats(value)) == 1 and value not in HISTORY_MOCKS.values()


def test_history_skips_a_listed_mock_in_old_commits(repo, capsys):
    mocks = list(HISTORY_MOCKS.values())
    commit(repo, "test_old.py", "".join(f'V{i} = "{value}"\n' for i, value in enumerate(mocks)))
    git(repo, "rm", "-q", "test_old.py")
    git(repo, "commit", "-qm", "build the mocks at runtime", "--no-verify")
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1
    capsys.readouterr()
    allow(repo, *mocks)
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 0
    err = capsys.readouterr().err
    assert "2 exact-format match(es) in old commits skipped" in err and secret_scan.ALLOWLIST in err
    allow(repo, mocks[0])  # each entry allows its own value only
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1
    assert "Hugging Face token in its exact format" in capsys.readouterr().err


def test_history_reports_every_other_exact_format_value(repo):
    allow(repo, *HISTORY_MOCKS.values())
    others = [value for _, value in EXACT_SAMPLES if value not in HISTORY_MOCKS.values()] + MOCK_VARIANTS
    assert len(others) == len(EXACT_SAMPLES) - 2 + len(MOCK_VARIANTS)
    lines = [f'V = "{value}"' for value in list(HISTORY_MOCKS.values()) + others]
    commit(repo, "test_old.py", "\n".join(lines) + "\n")
    findings = secret_scan.scan_revs(repo, ["--all"], [], secret_scan.load_allowlist(repo))
    assert [(f.line, f.exact) for f in findings if f.allowed] == [(1, True), (2, True)]
    assert [(f.line, f.exact) for f in findings if not f.allowed] == [(n, True) for n in range(3, len(lines) + 1)]
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1
    for value in others:  # one other value next to the mocks is enough
        commit(repo, "test_old.py", f'A = "{HISTORY_MOCKS["GitHub token"]}"\nB = "{value}"\n')
        findings = secret_scan.scan_revs(repo, ["HEAD^!"], [], secret_scan.load_allowlist(repo))
        assert [(f.line, f.allowed) for f in findings] == [(1, True), (2, False)], value


def test_a_listed_mock_still_fails_in_every_other_mode(repo, capsys, monkeypatch):
    # GitHub may block a new commit with it, so only --history, which reads the old commits, skips it.
    allow(repo, *HISTORY_MOCKS.values())
    (repo / "test_new.py").write_text("".join(f'V{i} = "{value}"\n' for i, value in enumerate(HISTORY_MOCKS.values())))
    git(repo, "add", "test_new.py")
    assert secret_scan.main(["--repo", str(repo), "--staged"]) == 1
    assert secret_scan.main(["--repo", str(repo)]) == 1
    assert secret_scan.main(["--repo", str(repo), str(repo / "test_new.py")]) == 1
    err = capsys.readouterr().err
    assert err.count("GitHub token in its exact format") == err.count("Hugging Face token in its exact format") == 3
    assert "skipped" not in err
    git(repo, "commit", "-qm", "mocks", "--no-verify")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1
    for name in ("PRE_COMMIT_FROM_REF", "PRE_COMMIT_LOCAL_BRANCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", head)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert secret_scan.main(["--repo", str(repo), "--push"]) == 1
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 0


def test_the_allowlist_never_silences_the_other_rules(repo):
    # Listing a digest only drops the exact-format finding in --history: the provider rules still report a
    # real-looking value, and local patterns and forbidden file names never look at the list.
    real_looking = "gh" + "p_" + body(36, seed=7)  # an exact format the GitHub rule reports as well
    personal = "jane-doe-laptop"
    commit(repo, "settings.py", f'TOKEN = "{real_looking}"\nKEY = "{SAMPLES["Anthropic key"]}"\n')
    commit(repo, ".env", "DEBUG=1\n")
    commit(repo, "notes.md", f"built on {personal}\n")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "secret-patterns").write_text(personal + "\n")
    allow(repo, *HISTORY_MOCKS.values(), real_looking, SAMPLES["Anthropic key"], ".env", "DEBUG=1", personal)
    local = secret_scan.load_local_patterns(repo)
    findings = secret_scan.scan_revs(repo, ["--all"], local, secret_scan.load_allowlist(repo))
    assert [(f.line, f.kind) for f in findings if f.allowed] == [(1, "GitHub token")]
    assert sorted((f.where.split(" ")[0], f.line, f.kind, f.exact) for f in findings if not f.allowed) == [
        (".env", 0, "file that holds credentials or per-user state", False),
        ("notes.md", 1, "local pattern #1", False),
        ("settings.py", 1, "GitHub token", False),
        ("settings.py", 2, "Anthropic key", False),
    ]
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1


GH_MOCK, HF_MOCK = HISTORY_MOCKS["GitHub token"], HISTORY_MOCKS["Hugging Face token"]
# A value of another exact format that holds a listed mock: GitHub blocks it as that format, so it is no mock.
AROUND_A_MOCK = [
    ("PyPI token", "pypi" + "-" * 51 + GH_MOCK + "-" * 5),
    ("PyPI token", "pypi" + "-" * 51 + HF_MOCK + "-" * 5),
    ("Anthropic key", "sk-ant-" + "api03-" + "-" * 52 + GH_MOCK + "-" + "AA"),
    ("xAI key", "xa" + "i-" + GH_MOCK + "_" + "x" * 39),
    ("OpenAI key", "sk-" + "proj-" + "-" * 33 + GH_MOCK + "-" + "T3Blbk" + "FJ" + "x" * 74),
    ("GitLab token", "glp" + "at-" + "-" + GH_MOCK + "-" + "." + "0" * 9),
    ("Slack token", "xo" + "xb-" + "1234567890-1234567890-" + GH_MOCK),  # ends inside the mock
]


@pytest.mark.parametrize("kind, value", AROUND_A_MOCK)
def test_a_listed_mock_hides_no_longer_value_of_another_format_around_it(repo, kind, value):
    regex = next(f.regex for f in secret_patterns.EXACT_FORMATS if f.name == kind)
    match = regex.search(value)
    mock = GH_MOCK if GH_MOCK in value else HF_MOCK
    start = value.index(mock)
    assert match and match.start() < start + len(mock) and start < match.end(), "the value overlaps the mock"
    allowed = frozenset(secret_scan.digest(v) for v in HISTORY_MOCKS.values())
    findings = secret_scan.scan_text("f.py", f'V = "{value}"', allowed=allowed)
    assert (kind, True) in [(f.kind, f.exact) for f in findings if not f.allowed]
    allow(repo, *HISTORY_MOCKS.values())
    commit(repo, "test_old.py", f'V = "{value}"\n')
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1


NEXT_TO_A_MOCK = [  # a real-looking value in one match of an ordinary rule with a mock, which makes it a placeholder
    ("URL with password", "https://deploy:" + GH_MOCK + "-" + body(16, seed=11) + "@registry.internal.corp"),
    ("URL with password", "https://deploy:" + HF_MOCK + "-" + body(16, seed=12) + "@registry.internal.corp"),
    ("Anthropic key", "sk-ant-" + "api03-" + GH_MOCK + "-" + body(40, seed=13)),
    ("secret assignment", 'client_secret = "' + GH_MOCK + "-" + body(30, seed=14) + '"'),
]


@pytest.mark.parametrize("kind, line", NEXT_TO_A_MOCK)
def test_a_listed_mock_does_not_make_a_value_next_to_it_a_placeholder(repo, kind, line):
    assert [f.kind for f in secret_scan.scan_text("f.py", line) if not f.exact] == [], "only the mock is reported"
    allowed = frozenset(secret_scan.digest(v) for v in HISTORY_MOCKS.values())
    findings = secret_scan.scan_text("f.py", line, allowed=allowed)
    assert [(f.kind, f.exact) for f in findings if not f.allowed] == [(kind, False)]
    allow(repo, *HISTORY_MOCKS.values())
    commit(repo, "test_old.py", line + "\n")
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1


def test_a_listed_value_hides_no_value_of_its_own_format_inside_it():
    # Not so for the two mocks (no format can start inside them), but for any value the list may name.
    inner = "xo" + "xp-" + "1234567890-1234567890-" + "z" * 8
    outer = "xo" + "xb-" + "1234567890-1234567890-" + inner
    assert secret_patterns.find_exact_formats(outer) == [("Slack token", outer)]
    findings = secret_scan.scan_text("f.py", outer, allowed={secret_scan.digest(outer)})
    assert [(f.kind, f.sample, f.allowed) for f in findings] == [
        ("Slack token", secret_scan.redact(outer), True), ("Slack token", secret_scan.redact(inner), False)]


def test_the_old_lines_with_the_mocks_hold_nothing_else():
    # The lines the history holds (old tests/test_security.py and tests/test_cli_builds.py): reading them without
    # the mock finds nothing, so --history passes on them.
    allowed = frozenset(secret_scan.digest(v) for v in HISTORY_MOCKS.values())
    for line in (f'        ("{GH_MOCK}", "gh-REDACTED"),', f'            RuntimeError("token {HF_MOCK} broke it")))'):
        assert [f.allowed for f in secret_scan.scan_text("f.py", line, allowed=allowed)] == [True]


@pytest.mark.parametrize("text", ["pypi" + "-pypi" * 20000, "glp" + "at-" * 20000, "xo" + "xb-1234567890-" * 8000,
                                  "sk-ant-" + "api03-sk-ant-" * 8000, "sk-" + "proj-sk-" * 15000])
def test_a_line_with_a_listed_mock_stays_fast(text):
    allowed = frozenset(secret_scan.digest(v) for v in HISTORY_MOCKS.values())
    line = f"{GH_MOCK} {text}-{GH_MOCK}-{HF_MOCK} {text}"
    start = time.perf_counter()
    secret_scan.scan_text("f.py", line, allowed=allowed)
    assert time.perf_counter() - start < 2.0


def test_a_digest_of_a_part_or_a_framing_of_a_value_allows_nothing(repo):
    # Only the digest of exactly the value counts: not of a prefix, a suffix, the value in quotes or with a line
    # break, in upper case, or in another encoding.
    values = list(HISTORY_MOCKS.values()) + ["gh" + "s_" + "x" * 36]
    near = [v for value in values for v in (value[:-1], value[1:], f'"{value}"', value + "\n", " " + value,
                                             value.upper(), value + "_")]
    lines = ["# documented test mocks"] + [f"{secret_scan.digest(v)}  near the value, not the value" for v in near]
    lines += [hashlib.sha256(value.encode("utf-16")).hexdigest() + "  other encoding" for value in values]
    (repo / secret_scan.ALLOWLIST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    commit(repo, "test_old.py", "".join(f'V{i} = "{value}"\n' for i, value in enumerate(values)))
    findings = secret_scan.scan_revs(repo, ["--all"], [], secret_scan.load_allowlist(repo))
    assert [(f.line, f.exact, f.allowed) for f in findings] == [(n, True, False) for n in (1, 2, 3)]
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 1


DIGEST = secret_scan.digest("not a token")
MALFORMED_LINES = [
    DIGEST.upper() + "  upper-case digits",
    DIGEST + " one space",
    DIGEST + "\ta tab",
    DIGEST + "   three spaces",
    DIGEST[:-1] + "  63 digits",
    DIGEST + "0  65 digits",
    "g" + DIGEST[1:] + "  not hex",
    DIGEST,
    DIGEST + "  ",
    " " + DIGEST + "  indented",
    "sha256:" + DIGEST + "  with a label",
    " # an indented comment",
    HISTORY_MOCKS["GitHub token"] + "  the value instead of its digest",
    secret_scan.digest(HISTORY_MOCKS["GitHub token"]) + "  " + "x" * 4 + "\n" + "*",  # a valid entry, then junk
    # A character a viewer may show as nothing or that other tools take as a line break: what looks like one comment
    # line to a reviewer must never hold an entry for the scan.
    *("# removed:" + sep + secret_scan.digest(HISTORY_MOCKS["GitHub token"]) + "  hidden entry"
      for sep in ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " ")),
    DIGEST + "  a note\x00 with a control character",
    "﻿# a byte order mark",
    DIGEST + "  zero​width space",
]


@pytest.mark.parametrize("line", MALFORMED_LINES)
def test_a_malformed_allowlist_line_is_a_usage_error_in_every_mode(repo, capsys, line):
    # A typo must never silently allow more; the pre-commit hook already stops it. The line is never echoed: it may
    # hold a value pasted by mistake.
    allow(repo, *HISTORY_MOCKS.values(), extra=line + "\n")
    number = 4 + line.count("\n")
    for mode in (["--history"], ["--staged"], [], [str(repo / secret_scan.ALLOWLIST)]):
        assert secret_scan.main(["--repo", str(repo), *mode]) == 2, mode
        err = capsys.readouterr().err
        assert f"{secret_scan.ALLOWLIST}:{number}: malformed line" in err
        assert line.strip().splitlines()[-1] not in err.replace(str(repo), "")
        assert HISTORY_MOCKS["GitHub token"] not in err


def test_the_allowlist_takes_comments_blank_lines_and_crlf(repo, capsys):
    assert secret_scan.load_allowlist(repo) == frozenset(), "no file, nothing allowed"
    first, second = (secret_scan.digest(value) for value in HISTORY_MOCKS.values())
    text = f"# comment\r\n\r\n   \r\n{first}  note: Prüfung, ünïcode\r\n#{second}  commented out\r\n"
    (repo / secret_scan.ALLOWLIST).write_bytes(text.encode("utf-8"))
    assert secret_scan.load_allowlist(repo) == {first}
    (repo / secret_scan.ALLOWLIST).write_bytes(first.encode() + b"  note \xff\n")  # not UTF-8
    assert secret_scan.main(["--repo", str(repo), "--history"]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_an_allowlist_with_a_byte_order_mark_or_old_mac_line_ends_is_malformed(repo, capsys):
    entry = f"{secret_scan.digest(GH_MOCK)}  {hint(GH_MOCK)}: a test mock"
    for text in ("﻿" + entry + "\n", "# comment\r" + entry + "\r"):
        (repo / secret_scan.ALLOWLIST).write_bytes(text.encode("utf-8"))
        assert secret_scan.main(["--repo", str(repo), "--history"]) == 2
        assert f"{secret_scan.ALLOWLIST}:1: malformed line" in capsys.readouterr().err


def test_the_repository_allowlist_names_exactly_the_two_history_mocks():
    text = (REPO_ROOT / secret_scan.ALLOWLIST).read_text(encoding="utf-8")
    assert secret_scan.load_allowlist(REPO_ROOT) == {hashlib.sha256(value.encode()).hexdigest()
                                                     for value in HISTORY_MOCKS.values()}
    entries = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert len(entries) == len(HISTORY_MOCKS) == 2
    for (kind, value), where in zip(HISTORY_MOCKS.items(), ["tests/test_security.py", "tests/test_cli_builds.py"]):
        entry = next(e for e in entries if e.startswith(secret_scan.digest(value) + "  "))
        note = entry.split("  ", 1)[1]
        assert note.startswith(f"{hint(value)}: {kind} format; test mock ") and where in note


def test_the_repository_allowlist_holds_no_value():
    # Neither an exact-format literal (GitHub would block the file) nor any other plaintext piece of the mocks.
    text = (REPO_ROOT / secret_scan.ALLOWLIST).read_text(encoding="utf-8")
    assert secret_patterns.find_exact_formats(text) == []
    assert secret_scan.scan_text(secret_scan.ALLOWLIST, text) == []
    for value in HISTORY_MOCKS.values():
        assert not [value[i:i + 8] for i in range(len(value) - 7) if value[i:i + 8] in text]


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
