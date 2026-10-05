"""Failure-mode tests for scripts/check_site_deploy_content.py (hermetic: httpx is stubbed).

This is the post-deploy check for the Cloudflare Pages marketing site. Its docstring claims it
fails when the deploy "shipped the wrong content, an empty build, or nothing new at all". The
first two hold; the third does not (see the KNOWN_GAP test).
"""

from __future__ import annotations

import types

import httpx
import scripts.check_site_deploy_content as mod


def _page_with(markers: list[str]) -> str:
    return "<html>" + "".join(markers) + "</html>"


def _stub_get(monkeypatch, *, text: str = "", status: int = 200, exc: Exception | None = None):
    calls: list[str] = []

    def fake_get(url: str, **_kw):
        calls.append(url)
        if exc is not None:
            raise exc
        return types.SimpleNamespace(status_code=status, text=text)

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    return calls


def test_all_markers_present_passes(monkeypatch) -> None:
    _stub_get(monkeypatch, text=_page_with(mod.REQUIRED_STRINGS))
    assert mod.main() == 0


def test_missing_marker_fails(monkeypatch, capsys) -> None:
    _stub_get(monkeypatch, text=_page_with(mod.REQUIRED_STRINGS[:-1]))
    assert mod.main() == 1
    assert "missing expected markers" in capsys.readouterr().out


def test_empty_body_fails(monkeypatch) -> None:
    _stub_get(monkeypatch, text="")
    assert mod.main() == 1


def test_non_200_fails(monkeypatch) -> None:
    _stub_get(monkeypatch, text=_page_with(mod.REQUIRED_STRINGS), status=503)
    assert mod.main() == 1


def test_fetch_error_fails_instead_of_skipping(monkeypatch) -> None:
    _stub_get(monkeypatch, exc=httpx.ConnectError("unreachable"))
    assert mod.main() == 1


def test_KNOWN_GAP_a_stale_previous_deploy_with_the_same_markers_passes(monkeypatch) -> None:
    """Pins the gap in the docstring's 'nothing new at all' claim: the check compares the served
    HTML against a fixed marker list, never against the commit that was just deployed, so a
    no-op or failed deploy that leaves the previous version (which has every marker) live
    reports OK. UNVERIFIED end to end: needs the real Pages deployment, not run here.
    site-deploy.yml's later 'ancestor-of-main' step is what covers the deployed commit."""
    previous_deploy_html = _page_with(mod.REQUIRED_STRINGS) + "<!-- built from an older commit -->"
    _stub_get(monkeypatch, text=previous_deploy_html)
    assert mod.main() == 0
