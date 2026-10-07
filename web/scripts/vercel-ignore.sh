#!/bin/sh
# Vercel "Ignored Build Step" for web/ (S17 X1). Exit 0 = SKIP the build, exit 1 = BUILD.
#
# The old rule was `git diff --quiet HEAD^ HEAD -- .`: it looked only at the LAST commit. A push
# of several commits whose tip is docs-only (PR #263's web work, 2026-10-05) was skipped although
# web/ had changed, and a merge commit (whose first-parent diff drags in everything main gained)
# built although nothing new in web/ was in the PR. Both are the same mistake: the unit that
# matters is "has web/ changed since the last thing we deployed / since the base branch", not "in
# this one commit".
#
# Base, in order:
#   1. VERCEL_GIT_PREVIOUS_SHA (Vercel sets it to the last successfully deployed commit for this
#      branch) if that commit exists in the clone;
#   2. otherwise the merge-base of HEAD and origin/main (fetched shallowly if needed).
# Build when `web/` differs between that base and HEAD. Fail OPEN (build) whenever the base cannot
# be determined: a wasted build costs a deployment, a skipped real change ships stale UI.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "ignore-step: not a git repo; building"; exit 1; }
cd "$ROOT" || exit 1

BASE=""
if [ -n "${VERCEL_GIT_PREVIOUS_SHA:-}" ] && git cat-file -e "${VERCEL_GIT_PREVIOUS_SHA}^{commit}" 2>/dev/null; then
  BASE="$VERCEL_GIT_PREVIOUS_SHA"
  echo "ignore-step: base = previous deployed commit $BASE"
else
  REMOTE_REF="${VERCEL_IGNORE_BASE_REF:-origin/main}"
  if ! git rev-parse --verify -q "$REMOTE_REF" >/dev/null 2>&1; then
    git fetch --no-tags --depth=200 origin main >/dev/null 2>&1 || true
  fi
  if git rev-parse --verify -q "$REMOTE_REF" >/dev/null 2>&1; then
    BASE=$(git merge-base HEAD "$REMOTE_REF" 2>/dev/null || true)
  fi
  [ -n "$BASE" ] && echo "ignore-step: base = merge-base with $REMOTE_REF $BASE"
fi

if [ -z "$BASE" ]; then
  echo "ignore-step: could not determine a base; building (fail open)"
  exit 1
fi

if git diff --quiet "$BASE" HEAD -- web; then
  echo "ignore-step: web/ unchanged since $BASE; skipping build"
  exit 0
fi
echo "ignore-step: web/ changed since $BASE; building"
exit 1
