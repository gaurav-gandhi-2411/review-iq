from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "rotate_admin_password",
    Path(__file__).resolve().parents[2] / "scripts" / "rotate_admin_password.py",
)
assert _SPEC and _SPEC.loader
rap = importlib.util.module_from_spec(_SPEC)
sys.modules["rotate_admin_password"] = rap
_SPEC.loader.exec_module(rap)


class FakeGcloud:
    """Records every call; plays Secret Manager well enough to round-trip the hash."""

    def __init__(self, *, clipboard_ok: bool = True, tamper_readback: bool = False) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.clipboard_ok = clipboard_ok
        self.tamper_readback = tamper_readback
        self.stored_hash = ""
        self.clipboard_texts: list[str] = []

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str]:
        self.calls.append((argv, stdin))
        if argv[0] == "powershell":
            if not self.clipboard_ok:
                return 1, ""
            self.clipboard_texts.append(stdin or "")
            return 0, ""
        joined = " ".join(argv)
        if "versions add" in joined:
            self.stored_hash = stdin or ""
            return 0, "Created version [3] of the secret [admin-password-hash].\n"
        if "versions access" in joined:
            return 0, ("$argon2id$bogus" if self.tamper_readback else self.stored_hash) + "\n"
        if "run services update" in joined:
            svc = argv[4]
            return 0, f"Service [{svc}] revision [{svc}-00099-xyz] has been deployed\n"
        if "versions list" in joined:
            return 0, "1\n2\n3\n"
        if "versions disable" in joined:
            return 0, "Disabled\n"
        raise AssertionError(f"unexpected call {argv}")

    def kinds(self) -> list[str]:
        out = []
        for argv, _ in self.calls:
            j = " ".join(argv)
            for k in (
                "powershell",
                "versions add",
                "versions access",
                "run services update",
                "versions list",
                "versions disable",
            ):
                if k in j or argv[0] == k:
                    out.append(k)
                    break
        return out


def test_happy_path_never_leaks_password_and_pins_version(capsys, monkeypatch):
    monkeypatch.setattr(rap, "health_ok", lambda url: True)
    g = FakeGcloud()
    rc = rap.rotate(runner=g, today="2026-10-12", wait_for_enter=lambda: None)
    assert rc == 0
    password = g.clipboard_texts[0]
    assert len(password) >= 40  # token_urlsafe(32)
    captured = capsys.readouterr()
    assert password not in captured.out + captured.err
    for argv, _ in g.calls:
        assert password not in " ".join(argv)  # never on a command line
    assert "password copied to clipboard — paste into your password manager now" in captured.out
    assert g.stored_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    updates = [a for a, _ in g.calls if "run" in a and "update" in a]
    assert [u[4] for u in updates] == ["review-iq-admin", "review-iq"]
    for u in updates:
        assert "--update-secrets=ADMIN_PASSWORD_HASH=admin-password-hash:3" in u
        assert "--update-env-vars=SECRET_ROTATED_AT=2026-10-12" in u
        assert not any("latest" in part for part in u)
        assert "--account=gaurav.gandhi1129@gmail.com" in u
    disabled = [a for a, _ in g.calls if "disable" in a]
    assert sorted(d[4] for d in disabled) == ["1", "2"]  # never the new version 3
    assert g.clipboard_texts[-1] == ""  # cleared at the end
    # order: clipboard, add, read-back, both services, list, disable..., clear clipboard
    assert g.kinds()[:3] == ["powershell", "versions add", "versions access"]


def test_clipboard_failure_aborts_before_any_gcloud_call(capsys):
    g = FakeGcloud(clipboard_ok=False)
    assert rap.rotate(runner=g) == 2
    assert [argv[0] for argv, _ in g.calls] == ["powershell"]


def test_readback_mismatch_leaves_services_untouched(capsys):
    g = FakeGcloud(tamper_readback=True)
    assert rap.rotate(runner=g, wait_for_enter=lambda: None) == 3
    assert not any("run" in a and "update" in a for a, _ in g.calls)
    out = capsys.readouterr().out
    assert "verification FAILED" in out
    assert g.clipboard_texts[0] not in out


def test_health_failure_keeps_old_versions_enabled(monkeypatch):
    monkeypatch.setattr(rap, "health_ok", lambda url: False)
    g = FakeGcloud()
    assert rap.rotate(runner=g, wait_for_enter=lambda: None) == 4
    assert not any("disable" in a for a, _ in g.calls)


def test_keep_old_and_test_mode_skip_services_and_clipboard(monkeypatch):
    g = FakeGcloud()
    rc = rap.rotate(secret="scratch", services=(), use_clipboard=False, keep_old=True, runner=g)
    assert rc == 0
    assert g.kinds() == ["versions add", "versions access"]


def test_password_is_unique_per_run():
    a, b = FakeGcloud(), FakeGcloud()
    rap.rotate(
        runner=a, services=(), check_health=False, keep_old=True, wait_for_enter=lambda: None
    )
    rap.rotate(
        runner=b, services=(), check_health=False, keep_old=True, wait_for_enter=lambda: None
    )
    assert a.clipboard_texts[0] != b.clipboard_texts[0]


@pytest.mark.parametrize("bad", ["", "not-a-hash"])
def test_verify_stored_rejects_wrong_hash(bad):
    def runner(argv, stdin):
        return 0, bad

    assert rap.verify_stored("s", 1, "pw", runner) is False
