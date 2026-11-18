#!/bin/bash
# SessionStart hook of Claude Code: turns the secret-scan git hooks on in this clone (decided by the maintainer on 2026-11-18).
#
# The hooks (.githooks/pre-commit and .githooks/pre-push, CONTRIBUTING.md section 6) stop a commit or a push that holds a
# credential, but git uses them only after `git config core.hooksPath .githooks` (`make hooks`). Every fresh clone, and a
# Claude Code session in the cloud starts in one, has them off: the test values with the shape of a key that were pushed on
# 2026-11-18 got past for that reason. This puts the one setting in place at every start, resume, clear and compact.
#
# Idempotent and quiet when nothing is to do. It installs nothing and needs no network, so it runs in every session, in the
# cloud and on a person's own computer.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}"

# Not a checkout with the hooks (an export, another repository): nothing to do, and no error.
if [ ! -d .githooks ] || ! git rev-parse --git-dir >/dev/null 2>&1; then
    exit 0
fi

if [ "$(git config --get core.hooksPath || true)" != ".githooks" ]; then
    git config core.hooksPath .githooks
    echo "secret scan: git hooks enabled (core.hooksPath = .githooks)"
fi

# A hook that is not executable is skipped by git without a word: make sure these two run.
for hook in .githooks/pre-commit .githooks/pre-push; do
    if [ -f "$hook" ] && [ ! -x "$hook" ]; then
        chmod +x "$hook"
    fi
done
