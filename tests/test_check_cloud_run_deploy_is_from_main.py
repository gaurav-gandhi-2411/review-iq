"""Unit tests for scripts/check_cloud_run_deploy_is_from_main.py.

scripts/ has no __init__.py (matches this repo's existing convention), so the module is
imported by inserting its directory onto sys.path rather than as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import check_cloud_run_deploy_is_from_main as mod  # noqa: E402 -- must follow the sys.path insert

_FAKE_SHA = "a" * 40


class TestGetRunningImageTag:
    def test_returns_none_on_gcloud_failure(self) -> None:
        with patch.object(mod, "run", return_value=(1, "ERROR: not authenticated")):
            tag, detail = mod.get_running_image_tag("review-iq")
        assert tag is None
        assert "not authenticated" in detail

    def test_returns_none_on_empty_output(self) -> None:
        with patch.object(mod, "run", return_value=(0, "")):
            tag, detail = mod.get_running_image_tag("review-iq")
        assert tag is None
        assert "empty" in detail

    def test_returns_none_on_digest_reference(self) -> None:
        image = "asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api@sha256:deadbeef"
        with patch.object(mod, "run", return_value=(0, image)):
            tag, detail = mod.get_running_image_tag("review-iq")
        assert tag is None
        assert "digest" in detail

    def test_extracts_tag_from_real_image_reference(self) -> None:
        image = f"asia-south1-docker.pkg.dev/review-iq-prod/review-iq/api:sha-{_FAKE_SHA}"
        with patch.object(mod, "run", return_value=(0, image)):
            tag, detail = mod.get_running_image_tag("review-iq")
        assert tag == f"sha-{_FAKE_SHA}"


class TestResolveCommitOnMain:
    def test_rejects_tag_not_matching_sha_format(self) -> None:
        # This is exactly the real 2026-08-01 incident shape: "v0-19-0" traces to nothing.
        commit, detail = mod.resolve_commit_on_main("v0-19-0")
        assert commit is None
        assert "does not match" in detail

    def test_rejects_commit_missing_from_local_history(self) -> None:
        with patch.object(mod, "run", return_value=(1, "fatal: Not a valid object name")):
            commit, detail = mod.resolve_commit_on_main(f"sha-{_FAKE_SHA}")
        assert commit is None
        assert "does not exist" in detail

    def test_rejects_commit_not_ancestor_of_main(self) -> None:
        def fake_run(args: list[str]) -> tuple[int, str]:
            if args[:2] == [mod.GIT, "cat-file"]:
                return 0, ""
            if args[:2] == [mod.GIT, "fetch"]:
                return 0, ""
            if args[:2] == [mod.GIT, "merge-base"]:
                return 1, ""
            raise AssertionError(f"unexpected call: {args}")

        with patch.object(mod, "run", side_effect=fake_run):
            commit, detail = mod.resolve_commit_on_main(f"sha-{_FAKE_SHA}")
        assert commit is None
        assert "NOT an ancestor" in detail

    def test_accepts_commit_that_is_ancestor_of_main(self) -> None:
        def fake_run(args: list[str]) -> tuple[int, str]:
            if args[:2] == [mod.GIT, "cat-file"]:
                return 0, ""
            if args[:2] == [mod.GIT, "fetch"]:
                return 0, ""
            if args[:2] == [mod.GIT, "merge-base"]:
                return 0, ""
            raise AssertionError(f"unexpected call: {args}")

        with patch.object(mod, "run", side_effect=fake_run):
            commit, detail = mod.resolve_commit_on_main(f"sha-{_FAKE_SHA}")
        assert commit == _FAKE_SHA

    def test_fails_closed_when_fetch_fails(self) -> None:
        def fake_run(args: list[str]) -> tuple[int, str]:
            if args[:2] == [mod.GIT, "cat-file"]:
                return 0, ""
            if args[:2] == [mod.GIT, "fetch"]:
                return 1, "network error"
            raise AssertionError(f"unexpected call: {args}")

        with patch.object(mod, "run", side_effect=fake_run):
            commit, detail = mod.resolve_commit_on_main(f"sha-{_FAKE_SHA}")
        assert commit is None
        assert "could not fetch" in detail


class TestMain:
    def test_exits_nonzero_if_any_service_fails(self) -> None:
        with (
            patch.object(mod, "get_running_image_tag", return_value=("v0-19-0", "running image tag: 'v0-19-0'")),
        ):
            assert mod.main() == 1

    def test_exits_zero_when_every_service_resolves(self) -> None:
        with (
            patch.object(
                mod, "get_running_image_tag", return_value=(f"sha-{_FAKE_SHA}", "detail")
            ),
            patch.object(mod, "resolve_commit_on_main", return_value=(_FAKE_SHA, "detail")),
        ):
            assert mod.main() == 0
