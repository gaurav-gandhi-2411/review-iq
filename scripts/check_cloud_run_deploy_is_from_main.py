"""Fail loudly if a running Cloud Run revision's image does not trace to a commit on `main`.

Background (2026-08-01): the `review-iq` and `review-iq-admin` services were both found
running an image tagged `v0-19-0` -- a tag that resolves to no git tag (highest is `v0.10.2`)
and no CI pipeline (deploy.yml only ever pushed to Hugging Face Spaces; every Cloud Run deploy
was a manual `gcloud builds submit`/`gcloud run deploy` run against whatever a local checkout
happened to be at the time). There was no way to audit or reproduce what was actually serving
customers. This is the same failure class as rule 31a's `vercel deploy --prod` incident: a
deployed artifact with no traceable relationship to what's actually in version control.

Fix: `.github/workflows/deploy-cloud-run.yml` now tags every image `sha-<full 40-hex commit
SHA>` and deploys only from a push to `main`. This script is the detective control -- run after
every deploy and on a standing schedule -- that verifies a running revision's tag actually
decodes to a real commit that IS an ancestor of `main`, so a future manual deploy (or a bug in
the pipeline itself) gets caught rather than silently trusted.

Per rule 98a: every failure mode here is a DENY, never a silent pass -- an ambiguous result
(gcloud not authenticated, image not found, tag doesn't match the expected format, commit not
resolvable, commit not an ancestor of main) is exactly the class of thing that must fail closed.

Usage: uv run python scripts/check_cloud_run_deploy_is_from_main.py
Exit 0: every checked service's running image resolves to a commit that IS on main.
Exit 1: any service fails that check, for any reason -- the reason is printed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

# gcloud on Windows is a .cmd shim -- subprocess.run() without shell=True won't resolve it via
# CreateProcess, only via PATH search through the shell. shutil.which() finds the real
# executable (gcloud.cmd on Windows, the plain binary on Linux/CI) so this script runs the same
# way in both places instead of silently working on Linux only.
GCLOUD = shutil.which("gcloud") or "gcloud"
GIT = shutil.which("git") or "git"

PROJECT = "review-iq-prod"
REGION = "asia-south1"
SERVICES = ("review-iq", "review-iq-admin")
IMAGE_TAG_PATTERN = re.compile(r"^sha-([0-9a-f]{40})$")


def run(args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except Exception as e:
        return 1, f"ERROR running {args}: {e}"


def get_running_image_tag(service: str) -> tuple[str | None, str]:
    """Returns (tag, detail). tag is None on any failure -- never a guessed/default value."""
    code, out = run(
        [
            GCLOUD,
            "run",
            "services",
            "describe",
            service,
            "--project",
            PROJECT,
            "--region",
            REGION,
            "--format",
            "value(spec.template.spec.containers[0].image)",
        ]
    )
    if code != 0:
        return None, f"gcloud exited {code}: {out[:300]!r}"
    image = out.strip()
    if not image:
        return None, "gcloud succeeded but returned an empty image reference"
    if "@sha256:" in image:
        return None, (
            f"image '{image}' is referenced by digest, not by our sha-<commit> tag -- cannot "
            "recover which commit built it from the digest alone"
        )
    if ":" not in image:
        return None, f"image '{image}' has no tag at all"
    tag = image.rsplit(":", 1)[1]
    return tag, f"running image tag: {tag!r}"


def resolve_commit_on_main(tag: str) -> tuple[str | None, str]:
    m = IMAGE_TAG_PATTERN.match(tag)
    if not m:
        return None, (
            f"tag {tag!r} does not match the expected 'sha-<40-hex-commit>' format -- cannot "
            "extract a commit to verify (this is exactly the v0-19-0 incident's shape)"
        )
    commit = m.group(1)

    code, out = run([GIT, "cat-file", "-e", commit])
    if code != 0:
        return (
            None,
            f"commit {commit} from tag {tag!r} does not exist in this checkout's git history",
        )

    code, out = run([GIT, "fetch", "origin", "main"])
    if code != 0:
        return None, f"could not fetch origin/main to verify ancestry: {out[:300]!r}"

    code, out = run([GIT, "merge-base", "--is-ancestor", commit, "origin/main"])
    if code != 0:
        return None, f"commit {commit} (from tag {tag!r}) is NOT an ancestor of origin/main"

    return commit, f"commit {commit} confirmed on origin/main"


def main() -> int:
    failures: list[str] = []
    for service in SERVICES:
        tag, tag_detail = get_running_image_tag(service)
        if tag is None:
            print(f"[FAIL] {service}: {tag_detail}")
            failures.append(service)
            continue

        commit, commit_detail = resolve_commit_on_main(tag)
        if commit is None:
            print(f"[FAIL] {service}: {tag_detail} -- {commit_detail}")
            failures.append(service)
            continue

        print(f"[PASS] {service}: {tag_detail} -- {commit_detail}")

    print("")
    if failures:
        print(f"DEPLOY-PROVENANCE CHECK: FAILED for {', '.join(failures)}")
        return 1
    print("DEPLOY-PROVENANCE CHECK: all services trace to a commit on main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
