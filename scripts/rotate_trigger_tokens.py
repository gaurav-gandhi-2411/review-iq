"""Rotate the three internal trigger tokens (ingest tick, digest, detector sweep) without ever exposing a value.

    python scripts/rotate_trigger_tokens.py            # rotate all three
    python scripts/rotate_trigger_tokens.py --dry-run  # names and checks only, changes nothing

What it does, in order (a failure stops the run; the previous revision keeps serving until the new one is Ready):
  1. generates each new token in memory (secrets.token_urlsafe(32));
  2. adds it as a NEW VERSION of the existing Secret Manager secret, via stdin (never argv), and reads it back to compare;
  3. one `gcloud run services update`: removes the plain env var and binds the secret at the explicit new version;
  4. rewrites the Cloud Scheduler header through the REST API (the value never appears in a command line);
  5. checks the endpoints and prints HTTP STATUS CODES ONLY: the new ingest token is accepted, the retired ingest value and a
     random well-formed value are rejected with 401, and a random value is rejected by the digest and detector endpoints.
     The digest/detector endpoints are never called with a valid token (they send email / run sweeps).
  6. disables the previous secret versions. Destroying them is a separate, later step.

No secret value is printed, logged, written to a file or passed as an argument. Every gcloud call carries --account.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

PROJECT = "reviewiq-prod-260813"
REGION = "asia-south1"
ACCOUNT = "--account=gaurav.gandhi1129@gmail.com"
SERVICE = "review-iq"
GCLOUD = shutil.which("gcloud") or "gcloud"
# env var, secret, scheduler job, header, endpoint path
TOKENS = [
    ("INGEST_TICK_TOKEN", "ingest-tick-token", "review-iq-ingest-tick", "X-Ingest-Tick-Token", "/internal/ingest/tick"),
    ("DIGEST_TRIGGER_TOKEN", "digest-trigger-token", "review-iq-digest-daily", "X-Digest-Trigger-Token", "/internal/digest/run"),
    ("DETECTOR_SWEEP_TRIGGER_TOKEN", "detector-sweep-trigger-token", "review-iq-detector-sweep", "X-Detector-Sweep-Token", "/internal/detectors/run"),
]  # fmt: skip


def gc(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [GCLOUD, *args, ACCOUNT, f"--project={PROJECT}"],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def die(msg: str) -> None:
    print("STOP:", msg)
    sys.exit(1)


def rest(method: str, url: str, access: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
    )  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}  # body deliberately dropped: an error body must never be echoed


def post_status(url: str, headers: dict[str, str], body: bytes) -> int:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    dry = ap.parse_args().dry_run

    r = gc("run", "services", "describe", SERVICE, f"--region={REGION}", "--format=json")
    if r.returncode:
        die("cannot describe the Cloud Run service")
    svc = json.loads(r.stdout)
    base_url = svc["status"]["url"]
    env = {
        e["name"]: e
        for c in svc["spec"]["template"]["spec"]["containers"]
        for e in c.get("env", [])
    }
    old_plain = {
        k: env[k].get("value") for k, *_ in TOKENS if k in env and "valueFrom" not in env[k]
    }
    print("service:", SERVICE, "| plain env vars to replace:", sorted(old_plain), "| dry-run:", dry)
    if dry:
        for _, secret, *_ in TOKENS:
            v = gc(
                "secrets", "versions", "list", secret, "--format=value(name.scope(versions),state)"
            )
            print(secret, "versions:", v.stdout.split())
        return

    access = gc("auth", "print-access-token").stdout.strip()
    if not access:
        die("no access token for the account")

    new: dict[str, str] = {}
    versions: dict[str, str] = {}
    prev: dict[str, list[str]] = {}
    for envname, secret, *_ in TOKENS:
        tok = secrets.token_urlsafe(32)
        listing = gc(
            "secrets",
            "versions",
            "list",
            secret,
            "--filter=state=ENABLED",
            "--format=value(name.scope(versions))",
        )
        prev[secret] = listing.stdout.split()
        add = gc("secrets", "versions", "add", secret, "--data-file=-", stdin=tok)
        if add.returncode:
            die(f"could not add a version to {secret}")
        latest = gc(
            "secrets",
            "versions",
            "list",
            secret,
            "--sort-by=~createTime",
            "--limit=1",
            "--format=value(name.scope(versions))",
        )
        ver = latest.stdout.strip()
        back = gc("secrets", "versions", "access", ver, f"--secret={secret}")
        if back.stdout != tok:
            die(f"read-back of {secret} v{ver} did not match")
        new[envname], versions[secret] = tok, ver
        print(
            f"secret {secret}: new version {ver} written and read back OK (previous enabled: {prev[secret]})"
        )

    binds = ",".join(f"{e}={s}:{versions[s]}" for e, s, *_ in TOKENS)
    upd = gc("run", "services", "update", SERVICE, f"--region={REGION}",
             f"--remove-env-vars={','.join(e for e, *_ in TOKENS)}", f"--update-secrets={binds}", "--quiet")  # fmt: skip
    if upd.returncode:
        print(upd.stderr[-600:])
        die(
            "Cloud Run update failed; previous revision keeps serving; new secret versions exist and are unused"
        )
    print("Cloud Run updated: plain vars removed, secrets bound at explicit versions")

    for envname, _secret, job, header, _path in TOKENS:
        url = f"https://cloudscheduler.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs/{job}"
        st, j = rest("GET", url, access)
        if st != 200:
            die(f"cannot read scheduler job {job} (HTTP {st})")
        headers = dict(j["httpTarget"].get("headers", {}))
        headers[header] = new[envname]
        st, _ = rest(
            "PATCH",
            f"{url}?updateMask=httpTarget.headers",
            access,
            {"httpTarget": {"headers": headers}},
        )
        print(f"scheduler {job}: header {header} updated (HTTP {st})")
        if st != 200:
            die(f"scheduler update failed for {job}")

    # --- checks: status codes only ---
    st, j = rest(
        "GET",
        f"https://cloudscheduler.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs/review-iq-ingest-tick",
        access,
    )
    body = base64.b64decode(j["httpTarget"].get("body", "")) if st == 200 else b""
    ok = True
    for envname, _secret, _job, header, path in TOKENS:
        h = {"Content-Type": "application/json", header: secrets.token_urlsafe(32)}
        code = post_status(base_url + path, h, body if envname == "INGEST_TICK_TOKEN" else b"{}")
        print(f"check {path}: random well-formed token ->", code)
        ok &= code == 401
    ing = TOKENS[0]
    if old_plain.get(ing[0]):
        h = {"Content-Type": "application/json", ing[3]: old_plain[ing[0]]}
        code = post_status(base_url + ing[4], h, body)
        print(f"check {ing[4]}: RETIRED ingest value ->", code)
        ok &= code == 401
    h = {"Content-Type": "application/json", ing[3]: new[ing[0]]}
    code = post_status(base_url + ing[4], h, body)
    print(f"check {ing[4]}: NEW ingest value ->", code)
    ok &= code == 200
    if not ok:
        die(
            "a check did not return the expected status; previous versions are NOT disabled; investigate"
        )

    for _, secret, *_ in TOKENS:
        for v in prev[secret]:
            d = gc("secrets", "versions", "disable", v, f"--secret={secret}")
            print(
                f"secret {secret}: previous version {v} disabled"
                if not d.returncode
                else f"secret {secret}: disable v{v} FAILED"
            )
    print("done. Destroy of the disabled previous versions is a separate step after verification.")


if __name__ == "__main__":
    main()
