"""Unit tests for the SPA mount verdicts in scripts/check_app_mounted.py and
scripts/probe_web_surfaces.py (S19 Z8).

Defect found by induction: web/src/main.tsx renders `<h1>Samidha Reviews is misconfigured</h1>`
when a required VITE_* variable is missing. Both mount checks looked for an <h1> CONTAINING
"Samidha Reviews", so the configuration-error screen counted as a mounted app. The verdict
logic is pure and tested here without a browser; the markers are pinned to the strings that
web/src/main.tsx and web/src/pages/Login.tsx actually render, so a copy edit there cannot
silently re-open the hole.

SURFACE: verdict logic and marker drift only. The browser half (that Playwright reports the
right counts for a real page) was exercised by hand against a real `vite build` with and
without VITE_* env; the transcript is in docs/control-audit-s19.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_app_mounted as cam
from scripts import probe_web_surfaces as pws

REPO_ROOT = Path(__file__).resolve().parents[2]
URL = "https://app.example.test/"


def test_healthy_login_screen_counts_as_mounted() -> None:
    assert cam.mount_problem(URL, "<div>...</div>", 1, 0) is None
    assert pws.spa_mount_problem("<div>...</div>", 1, 0, "Samidha Reviews") is None


def test_configuration_error_screen_is_not_a_mounted_app() -> None:
    """The decorative case: heading_count is 1 because the error h1 contains the brand."""
    assert "VITE_" in (
        cam.mount_problem(URL, "<h1>Samidha Reviews is misconfigured</h1>", 1, 1) or ""
    )
    assert "configuration-error" in (
        pws.spa_mount_problem("<h1>Samidha Reviews is misconfigured</h1>", 1, 1, "Samidha Reviews")
        or ""
    )


@pytest.mark.parametrize("root_html", ["", "   \n", None])
def test_empty_root_is_not_mounted(root_html: str | None) -> None:
    assert cam.mount_problem(URL, root_html, 0, 0) is not None
    assert pws.spa_mount_problem(root_html, 0, 0, "Samidha Reviews") is not None


def test_mounted_into_an_unexpected_state_is_flagged() -> None:
    assert cam.mount_problem(URL, "<div>500</div>", 0, 0) is not None
    assert pws.spa_mount_problem("<div>500</div>", 0, 0, "Samidha Reviews") is not None


def test_markers_match_the_strings_the_web_app_renders() -> None:
    main_tsx = (REPO_ROOT / "web" / "src" / "main.tsx").read_text(encoding="utf-8")
    login_tsx = (REPO_ROOT / "web" / "src" / "pages" / "Login.tsx").read_text(encoding="utf-8")
    assert cam.MISCONFIGURED_MARKER_TEXT == pws._MISCONFIGURED_MARKER
    assert f"Samidha Reviews {cam.MISCONFIGURED_MARKER_TEXT}" in main_tsx
    assert "<h1" in main_tsx.split("Samidha Reviews is misconfigured")[0].splitlines()[-1]
    assert cam.MOUNT_MARKER_TEXT in login_tsx
    assert cam.MOUNT_MARKER_TEXT in main_tsx  # why the substring match alone cannot be trusted
