from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES = REPO_ROOT / ".vercelignore"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

IGNORED = [
    "app/main.py",
    "data/x.csv",
    "eval/results.json",
    "reports/x.png",
    "pyproject.toml",
    "uv.lock",
    "ops/x",
    "ops/runbooks/vercel-deploys.md",
    "Dockerfile",
    ".env",
    "web/node_modules/react/index.js",
    "web/dist/index.html",
    "web/.vercel/project.json",
    "web/.env",
    "web/.env.local",
]
UPLOADED = [
    "web/src/App.tsx",
    "web/package.json",
    "web/vercel.json",
    "web/scripts/vercel-ignore.sh",
    "web/index.html",
]


@pytest.fixture(scope="module")
def probe_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Same gitignore engine semantics as the rule file's syntax, without a pathspec dependency.
    repo = tmp_path_factory.mktemp("vercelignore")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shutil.copy(RULES, repo / ".gitignore")
    return repo


def _is_ignored(repo: Path, path: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--no-index", "-q", path],
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(f"git check-ignore failed ({proc.returncode}) for {path}")
    return proc.returncode == 0


def test_rule_file_exists() -> None:
    assert RULES.is_file()


@pytest.mark.parametrize("path", IGNORED)
def test_non_web_paths_are_ignored(probe_repo: Path, path: str) -> None:
    assert _is_ignored(probe_repo, path), f"{path} would be uploaded"


@pytest.mark.parametrize("path", UPLOADED)
def test_web_sources_are_not_ignored(probe_repo: Path, path: str) -> None:
    assert not _is_ignored(probe_repo, path), f"{path} must be uploaded"
