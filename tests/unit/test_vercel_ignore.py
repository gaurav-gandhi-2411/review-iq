"""web/scripts/vercel-ignore.sh decides whether Vercel builds web/ (exit 1) or skips (exit 0).

Each test builds a throwaway git repo with a local bare `origin` and runs the real script, so the
exact scenarios that burned deployments / skipped real changes are proven, not described.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "web" / "scripts" / "vercel-ignore.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )
    return out.stdout.strip()


def commit(repo: Path, rel: str, text: str) -> str:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", f"change {rel}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "checkout", "-q", "-b", "main")
    commit(work, "web/src/App.tsx", "v1")
    commit(work, "README.md", "r1")
    git(work, "push", "-q", "origin", "main")
    (work / "web" / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, work / "web" / "scripts" / "vercel-ignore.sh")
    git(work, "checkout", "-q", "-b", "feature")
    return work


def run(repo: Path, **env: str) -> int:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VERCEL_")}
    r = subprocess.run(
        [BASH or "bash", str(repo / "web" / "scripts" / "vercel-ignore.sh")],
        cwd=repo / "web",
        capture_output=True,
        text=True,
        env={**clean, **env},
        check=False,
    )
    return r.returncode


def test_docs_only_multi_commit_pr_is_skipped(repo: Path) -> None:
    commit(repo, "docs/a.md", "1")
    commit(repo, "docs/b.md", "2")
    commit(repo, "ops/c.md", "3")
    assert run(repo) == 0


def test_web_change_followed_by_docs_only_tip_still_builds(repo: Path) -> None:
    # The PR 263 shape: the old HEAD^..HEAD rule saw only the docs tip and skipped.
    commit(repo, "web/src/Dashboard.tsx", "new")
    commit(repo, "docs/notes.md", "tip is docs only")
    assert (
        subprocess.run(
            ["git", "diff", "--quiet", "HEAD^", "HEAD", "--", "web"], cwd=repo
        ).returncode
        == 0
    )
    assert run(repo) == 1


def test_previous_deployed_sha_lets_a_docs_push_after_a_built_web_change_skip(repo: Path) -> None:
    web_sha = commit(repo, "web/src/Dashboard.tsx", "new")
    commit(repo, "docs/notes.md", "docs after the deployed web commit")
    assert run(repo, VERCEL_GIT_PREVIOUS_SHA=web_sha) == 0


def test_web_change_after_previous_deploy_builds(repo: Path) -> None:
    prev = commit(repo, "docs/a.md", "1")
    commit(repo, "web/src/Other.tsx", "x")
    assert run(repo, VERCEL_GIT_PREVIOUS_SHA=prev) == 1


def test_merge_of_main_does_not_build_when_the_pr_itself_has_no_web_change(repo: Path) -> None:
    commit(repo, "docs/a.md", "pr docs")
    git(repo, "checkout", "-q", "main")
    commit(repo, "web/src/FromMain.tsx", "someone else's web change")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "feature")
    git(repo, "fetch", "-q", "origin")
    git(repo, "merge", "-q", "--no-edit", "origin/main")
    # HEAD^..HEAD of a merge commit includes FromMain.tsx (old rule: build); the PR has none.
    assert run(repo) == 0


def test_unknown_base_fails_open_and_builds(repo: Path) -> None:
    commit(repo, "docs/a.md", "1")
    git(repo, "remote", "remove", "origin")
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert run(repo, VERCEL_GIT_PREVIOUS_SHA="0" * 40) == 1
