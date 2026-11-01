#!/bin/sh
# Vercel "Ignored Build Step" for web/ (S17 X1, S18 Z4). Exit 0 = SKIP the build, exit 1 = BUILD.
#
# The old rule was `git diff --quiet HEAD^ HEAD -- .`: it looked only at the LAST commit. A push
# of several commits whose tip is docs-only (PR #263's web work, 2026-10-05) was skipped although
# web/ had changed, and a merge commit (whose first-parent diff drags in everything main gained)
# built although nothing new in web/ was in the PR. Both are the same mistake: the unit that
# matters is "has web/ changed since the last thing we deployed / since the base branch", not "in
# this one commit".
#
# What Vercel's clone actually looks like (S18 Z4a, probed with exit-code-encoded diagnostics on
# throwaway branches, review-iq): it is a SHALLOW clone, there is no origin/main ref, the first
# push of a new branch has no VERCEL_GIT_PREVIOUS_SHA, and `git fetch --depth=200 origin main`
# does not yield a usable merge-base. So the PR #264 version failed OPEN on every first preview
# push and a docs-only PR was built (PR #267). Base, in order:
#   1. VERCEL_GIT_PREVIOUS_SHA (the last successfully deployed commit for this branch) if that
#      commit exists in the clone;
#   2. the merge-base of HEAD and the base branch: origin/<base> (fetched shallowly if missing),
#      else a fetch of the PUBLIC repo URL built from VERCEL_GIT_REPO_OWNER/VERCEL_GIT_REPO_SLUG;
#   3. SHALLOW CLONE ONLY, no base found: the files changed across the commits Vercel did clone
#      (oldest available ancestor .. HEAD). Skip when none of them is under web/. This can over-
#      build (a recent web/ change on the base branch is inside the window) but never skips a
#      change that is inside the window. It never skips a PRODUCTION deploy on this guess.
# Build when `web/` differs between the base and HEAD. Fail OPEN (build) when even tier 3 cannot
# be computed: a wasted build costs a deployment, a skipped real change ships stale UI.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "ignore-step: not a git repo; building"; exit 1; }
cd "$ROOT" || exit 1

# A fetch must never hang the build or prompt for credentials.
export GIT_TERMINAL_PROMPT=0 GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=20

BASE_BRANCH="${VERCEL_IGNORE_BASE_BRANCH:-main}"
REMOTE_REF="${VERCEL_IGNORE_BASE_REF:-origin/$BASE_BRANCH}"

BASE=""
if [ -n "${VERCEL_GIT_PREVIOUS_SHA:-}" ] && git cat-file -e "${VERCEL_GIT_PREVIOUS_SHA}^{commit}" 2>/dev/null; then
  BASE="$VERCEL_GIT_PREVIOUS_SHA"
  echo "ignore-step: base = previous deployed commit $BASE"
fi

if [ -z "$BASE" ]; then
  if ! git rev-parse --verify -q "$REMOTE_REF" >/dev/null 2>&1; then
    git fetch --no-tags --depth=200 origin "$BASE_BRANCH" >/dev/null 2>&1 || true
  fi
  if git rev-parse --verify -q "$REMOTE_REF" >/dev/null 2>&1; then
    BASE=$(git merge-base HEAD "$REMOTE_REF" 2>/dev/null || true)
  fi
  [ -n "$BASE" ] && echo "ignore-step: base = merge-base with $REMOTE_REF $BASE"
fi

if [ -z "$BASE" ]; then
  PUBLIC_URL="${VERCEL_IGNORE_PUBLIC_URL:-}"
  if [ -z "$PUBLIC_URL" ] && [ -n "${VERCEL_GIT_REPO_OWNER:-}" ] && [ -n "${VERCEL_GIT_REPO_SLUG:-}" ]; then
    PUBLIC_URL="https://github.com/${VERCEL_GIT_REPO_OWNER}/${VERCEL_GIT_REPO_SLUG}.git"
  fi
  if [ -n "$PUBLIC_URL" ] && git fetch --no-tags --depth=200 "$PUBLIC_URL" "$BASE_BRANCH" >/dev/null 2>&1; then
    BASE=$(git merge-base HEAD FETCH_HEAD 2>/dev/null || true)
    [ -n "$BASE" ] && echo "ignore-step: base = merge-base with public $BASE_BRANCH $BASE"
  fi
fi

if [ -z "$BASE" ]; then
  # Tier 3: shallow clone with no usable base.
  HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || true)
  OLDEST=""
  if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
    # In a shallow clone the boundary commit is grafted parentless, so it is the one root.
    OLDEST=$(git rev-list --max-parents=0 HEAD 2>/dev/null | tail -n 1)
  fi
  if [ "${VERCEL_ENV:-}" = "production" ]; then
    echo "ignore-step: no base and this is a production deploy; building (never skip production on a guess)"
    exit 1
  fi
  if [ -n "$OLDEST" ] && [ -n "$HEAD_SHA" ] && [ "$OLDEST" != "$HEAD_SHA" ]; then
    if git diff --quiet "$OLDEST" HEAD -- web; then
      echo "ignore-step: no base; shallow window $OLDEST..HEAD has no web/ change; skipping build"
      exit 0
    fi
    echo "ignore-step: no base; shallow window $OLDEST..HEAD touches web/; building"
    exit 1
  fi
  echo "ignore-step: could not determine a base or a usable window; building (fail open)"
  exit 1
fi

if git diff --quiet "$BASE" HEAD -- web; then
  echo "ignore-step: web/ unchanged since $BASE; skipping build"
  exit 0
fi
echo "ignore-step: web/ changed since $BASE; building"
exit 1
