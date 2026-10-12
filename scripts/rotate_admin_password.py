"""Rotate the admin password. Run this YOURSELF, in your own terminal, OUTSIDE Claude Code.

    python scripts/rotate_admin_password.py

What it does, in order (aborts before touching production if any step before 5 fails):
  1. generates a strong random password (secrets.token_urlsafe(32));
  2. copies it to the clipboard and prints ONLY a fixed confirmation line (the password is never printed,
     logged, put on a command line or in the environment);
  3. hashes it with argon2id (m=65536 KiB, t=3, p=4);
  4. adds the hash as a NEW version of the Secret Manager secret `admin-password-hash`, reads it back
     and verifies the generated password against the stored hash, in memory;
  5. points BOTH Cloud Run services (review-iq-admin, review-iq) at that explicit version and stamps
     SECRET_ROTATED_AT=<today>. Pinning a version number (not `latest`) is deliberate: Secret Manager
     answers `latest` with FAILED_PRECONDITION when the newest version is disabled, which would stop new
     instances from starting;
  6. checks https://api.samidhareviews.xyz/health, then disables the older enabled versions
     (use --keep-old to skip), then waits for you to press Enter (so you can paste the password into
     your password manager) and clears the clipboard.

Every gcloud call passes --account explicitly. Nothing secret is printed at any point; the in-memory
password and hash variables are dropped before exit (CPython cannot zero strings, so this is best effort).
Windows clipboard history (Win+V) can keep a copy: clear it after saving the password.
"""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from datetime import date

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError

ACCOUNT = "gaurav.gandhi1129@gmail.com"
PROJECT = "reviewiq-prod-260813"
REGION = "asia-south1"
SECRET = "admin-password-hash"
ENV_NAME = "ADMIN_PASSWORD_HASH"
SERVICES = ("review-iq-admin", "review-iq")
HEALTH_URL = "https://api.samidhareviews.xyz/health"
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, type=Type.ID)

# (argv, stdin text or None) -> (returncode, combined stdout+stderr)
Runner = Callable[[list[str], str | None], tuple[int, str]]


def run_cmd(argv: list[str], stdin: str | None = None) -> tuple[int, str]:
    # On Windows `gcloud` is gcloud.cmd: resolve the real path, a bare name is not found.
    resolved = [shutil.which(argv[0]) or argv[0], *argv[1:]]
    proc = subprocess.run(  # noqa: S603 - argv is a fixed list, never a shell string
        resolved, input=stdin, capture_output=True, text=True, check=False
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def gcloud(*args: str) -> list[str]:
    return ["gcloud", *args, f"--account={ACCOUNT}", f"--project={PROJECT}"]


def set_clipboard(text: str, runner: Runner) -> bool:
    # The text goes through stdin so it never appears on a command line.
    code, _ = runner(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$p=[Console]::In.ReadToEnd(); Set-Clipboard -Value $p",
        ],
        text,
    )
    return code == 0


def clear_clipboard(runner: Runner) -> bool:
    # An empty-string -Value is rejected by Set-Clipboard; $null is the supported way to clear it.
    code, _ = runner(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value $null"],
        None,
    )
    return code == 0


def add_version(secret: str, hashed: str, runner: Runner) -> int:
    code, out = runner(gcloud("secrets", "versions", "add", secret, "--data-file=-"), hashed)
    m = re.search(r"Created version \[(\d+)\]", out)
    if code != 0 or not m:
        raise RuntimeError("could not add the new secret version (gcloud output withheld)")
    return int(m.group(1))


def verify_stored(secret: str, version: int, password: str, runner: Runner) -> bool:
    code, stored = runner(
        gcloud("secrets", "versions", "access", str(version), f"--secret={secret}"), None
    )
    if code != 0:
        return False
    try:
        return bool(HASHER.verify(stored.strip(), password))
    except (VerificationError, ValueError):  # argon2's InvalidHash is a ValueError
        return False


def update_service(service: str, secret: str, version: int, today: str, runner: Runner) -> str:
    code, out = runner(
        gcloud(
            "run",
            "services",
            "update",
            service,
            f"--region={REGION}",
            f"--update-secrets={ENV_NAME}={secret}:{version}",
            f"--update-env-vars=SECRET_ROTATED_AT={today}",
        ),
        None,
    )
    m = re.search(r"revision \[([^\]]+)\]", out)
    if code != 0 or not m:
        raise RuntimeError(f"updating service {service} failed (gcloud output withheld)")
    return m.group(1)


def disable_old(secret: str, keep: int, runner: Runner) -> list[int]:
    code, out = runner(
        gcloud(
            "secrets",
            "versions",
            "list",
            secret,
            "--filter=state=ENABLED",
            "--format=value(name.basename())",
        ),
        None,
    )
    if code != 0:
        raise RuntimeError("could not list secret versions")
    disabled = []
    for token in out.split():
        if token.isdigit() and int(token) != keep:
            code, _ = runner(
                gcloud("secrets", "versions", "disable", token, f"--secret={secret}"), None
            )
            if code != 0:
                raise RuntimeError(f"could not disable version {token}")
            disabled.append(int(token))
    return disabled


def health_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310 - fixed https URL
            return r.status == 200
    except OSError:
        return False


def rotate(
    *,
    secret: str = SECRET,
    services: tuple[str, ...] = SERVICES,
    use_clipboard: bool = True,
    check_health: bool = True,
    keep_old: bool = False,
    runner: Runner = run_cmd,
    today: str | None = None,
    wait_for_enter: Callable[[], None] | None = None,
) -> int:
    today = today or date.today().isoformat()
    password = secrets.token_urlsafe(32)
    try:
        if use_clipboard:
            if not set_clipboard(password, runner):
                print("clipboard copy failed; nothing was changed. Aborting.")
                return 2
            print("password copied to clipboard — paste into your password manager now")
        else:
            print("password NOT copied (--no-clipboard): test mode, the password is discarded")
        hashed = HASHER.hash(password)
        version = add_version(secret, hashed, runner)
        print(f"new secret version {version} added")
        if not verify_stored(secret, version, password, runner):
            print(
                f"read-back verification FAILED for version {version}; services untouched. Aborting."
            )
            return 3
        print("read-back verification OK (stored hash matches the generated password)")
        for service in services:
            revision = update_service(service, secret, version, today, runner)
            print(f"service {service}: revision {revision} serving, pinned to version {version}")
        if check_health and services:
            if not health_ok(HEALTH_URL):
                print("health check did not return 200; older versions NOT disabled. Investigate.")
                return 4
            print("health check 200")
        if not keep_old:
            disabled = disable_old(secret, version, runner)
            print(f"disabled older versions: {disabled}")
        if use_clipboard:
            (
                wait_for_enter
                or (
                    lambda: input(
                        "Press Enter once the password is saved (clipboard will be cleared)... "
                    )
                )
            )()
            if clear_clipboard(runner):
                print("clipboard cleared (also clear Windows clipboard history: Win+V)")
            else:
                print("could not clear the clipboard: clear it and Win+V history yourself")
        return 0
    finally:
        password = ""  # best effort: CPython cannot zero the original string
        del password


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--secret", default=SECRET, help="secret name (default: admin-password-hash)")
    ap.add_argument(
        "--services", default=",".join(SERVICES), help="comma list; empty = none (test mode)"
    )
    ap.add_argument(
        "--no-clipboard", action="store_true", help="test mode: do not copy the password"
    )
    ap.add_argument("--no-health", action="store_true", help="skip the /health check")
    ap.add_argument("--keep-old", action="store_true", help="do not disable older enabled versions")
    a = ap.parse_args()
    services = tuple(s for s in a.services.split(",") if s)
    return rotate(
        secret=a.secret,
        services=services,
        use_clipboard=not a.no_clipboard,
        check_health=not a.no_health,
        keep_old=a.keep_old,
    )


if __name__ == "__main__":
    sys.exit(main())
