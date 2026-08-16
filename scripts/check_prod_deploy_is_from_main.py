"""Verify the live production Cloudflare Pages deployment's commit is on `main`.

Wave 2 (production-alias integrity, P1): the root cause of an unmerged react-router
migration reaching `app.samidhareviews.xyz` was `vercel deploy --prod` run from a local
working tree -- a deploy with no relationship to any git ref at all. `wrangler pages
deploy` has the identical property (it uploads whatever's in the given directory,
regardless of git state), so switching providers alone doesn't fix the underlying
problem; production deploys must happen from CI on `main` only (see
.github/workflows/web-deploy.yml), and this script is the standing check that they
still are -- it doesn't prevent a future manual `wrangler pages deploy --branch
production` from bypassing CI, but it detects it after the fact, the same day, rather
than staying silent indefinitely.

Queries Cloudflare's own Pages API for the current production deployment's recorded
commit hash and checks it's an ancestor of `main` in the actual git history. Fails
closed on every ambiguous case (API error, missing commit hash, unreachable git ref)
rather than treating "couldn't verify" as "must be fine" -- per this repo's own
standing rule against guards that pass open on a failed lookup.

Usage:
    uv run python scripts/check_prod_deploy_is_from_main.py

Requires: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID env vars (Pages:Read is enough --
this script never writes anything).
"""

from __future__ import annotations

import os
import subprocess

import httpx

PROJECT_NAME = "samidha-reviews-web"
API_BASE = "https://api.cloudflare.com/client/v4"


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def get_production_commit_hash() -> str | None:
    """Return the commit hash Cloudflare recorded for the current production
    deployment, or None if the API call didn't clearly succeed -- never guess."""
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not token or not account_id:
        return None

    url = f"{API_BASE}/accounts/{account_id}/pages/projects/{PROJECT_NAME}/deployments"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"env": "production"},
            timeout=15.0,
        )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    data = resp.json()
    if not data.get("success"):
        return None

    deployments = [d for d in data.get("result", []) if d.get("environment") == "production"]
    if not deployments:
        return None

    # Cloudflare returns deployments newest-first.
    latest = deployments[0]
    commit_hash = latest.get("deployment_trigger", {}).get("metadata", {}).get("commit_hash")
    return commit_hash if isinstance(commit_hash, str) and commit_hash else None


def is_ancestor_of_main(commit_hash: str) -> bool:
    """True iff `commit_hash` is an ancestor of (or equal to) origin/main."""
    subprocess.run(["git", "fetch", "origin", "main", "--quiet"], check=False)
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit_hash, "origin/main"],
        capture_output=True,
    )
    return result.returncode == 0


def main() -> int:
    commit_hash = get_production_commit_hash()
    if commit_hash is None:
        return _fail(
            "could not determine the production deployment's commit hash "
            "(API error, missing credentials, or no production deployment found) -- "
            "treating as unverified, not as a pass"
        )

    print(f"Production deployment commit: {commit_hash}")

    try:
        on_main = is_ancestor_of_main(commit_hash)
    except FileNotFoundError:
        return _fail("git is not available to verify ancestry")

    if not on_main:
        return _fail(
            f"production is serving commit {commit_hash}, which is NOT an ancestor of "
            f"main -- this deployment did not go through CI. Investigate how it was "
            f"deployed before assuming it's safe."
        )

    print(f"OK: {commit_hash} is an ancestor of main.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
