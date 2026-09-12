"""Unit tests for eval/provenance.py."""

from __future__ import annotations

import subprocess

from eval.provenance import get_git_sha, now_iso


class TestGetGitSha:
    def test_returns_sha_in_a_git_repo(self):
        # This test itself runs inside a git worktree, so a real SHA should come back.
        sha = get_git_sha()
        assert sha is not None
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_returns_none_when_git_missing(self, monkeypatch):
        def _raise(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", _raise)
        assert get_git_sha() is None

    def test_returns_none_when_not_a_repo(self, monkeypatch):
        def _raise(*args, **kwargs):
            raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

        monkeypatch.setattr(subprocess, "run", _raise)
        assert get_git_sha() is None


class TestNowIso:
    def test_ends_with_z_suffix(self):
        assert now_iso().endswith("Z")

    def test_matches_iso_shape(self):
        ts = now_iso()
        assert len(ts) == len("2026-07-30T12:00:00Z")
        assert ts[4] == "-" and ts[7] == "-" and ts[10] == "T"
