#!/bin/bash
# Pre-flight checks for an autonomous work cycle.
#
# Runs before starting a new PR in /loop mode. Refuses to proceed if
# the working state isn't clean — prevents the agent layering changes
# on top of stale or dirty trees.
#
# Usage:
#   tools/preflight.sh         # exits 0 if OK, non-zero with reason if not
#
# Output is human-readable + LLM-readable; the agent parses the final
# "OK to proceed" / "BLOCKED" line.

set -e

cd "$(git rev-parse --show-toplevel)"

# 1. We're on main.
BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "main" ]; then
    echo "BLOCKED: not on main (currently on '$BRANCH')"
    echo "Recovery: git checkout main"
    exit 1
fi

# 2. Working tree is clean (no unstaged or staged changes).
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "BLOCKED: working tree has uncommitted changes"
    echo "Recovery: git status, then commit / stash / discard"
    exit 1
fi

# 3. No untracked files (would suggest a previous cycle left state).
if [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "BLOCKED: untracked files present"
    echo "Recovery: review with 'git status'; remove or stash"
    exit 1
fi

# 4. Local main is up to date with remote.
git fetch --quiet origin main
LOCAL="$(git rev-parse main)"
REMOTE="$(git rev-parse origin/main)"
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "BLOCKED: local main is behind / diverged from origin/main"
    echo "Recovery: git pull --ff-only"
    exit 1
fi

# 5. No open PRs of mine still pending (avoid stacking)
#    Skipped by default — `gh` may not be available or auth'd in every
#    environment. Uncomment if you want strict serialisation.
# OPEN=$(gh pr list --author "@me" --state open --json number --jq 'length' 2>/dev/null || echo "0")
# if [ "$OPEN" != "0" ]; then
#     echo "BLOCKED: $OPEN of my PRs still open"
#     exit 1
# fi

echo "OK to proceed: branch=main, clean tree, up-to-date with origin"
exit 0
