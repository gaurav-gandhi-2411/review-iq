"""Leakage ledger (spec Amendment 2): no sealed-test item may appear anywhere it could have been learned from.

    python -m engine.labelling.leakage_ledger build   # (re)write reports/labelling/ledger.json
    python -m engine.labelling.leakage_ledger check   # CI: exit 1 on any violation

The ledger lists the content hash (`scale_sample.text_sha`) of every item used for anything other than final
evaluation: the three pilots, and the training and validation items of the scale sample. `check` fails when
(a) a sealed hash is in the ledger, (b) a sealed hash is in a training or validation manifest entry, (c) the
sealed-hash digest differs from the one recorded in the ledger, (d) any line or JSON string in a tracked text
file (prompts, few-shots, fixtures, demo data, docs) hashes to a sealed hash. Hashes only, so review text never
enters the repository. Scope of (d): every tracked file under the size cap that decodes as UTF-8, whole lines
and every JSON string value, normalised with `pilot_sample.clean`; a sealed review quoted as part of a longer
line is NOT detected (stated limit).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from engine.labelling.pilot_sample import clean
from engine.labelling.scale_sample import text_sha

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "reports" / "labelling" / "ledger.json"
PILOTS = [f"reports/labelling/pilot{i}/manifest.json" for i in (1, 2, 3)]
SCALE = "reports/labelling/scale/manifest.json"
SAFETY = "reports/labelling/safety/split.json"
FRESH15 = "reports/labelling/fresh15/manifest.json"  # Amendment 15: every item is test-only
FRESH16 = "reports/labelling/fresh16/manifest.json"  # Amendment 16: sealed blocks A, B, C (every item is test-only)
FRESH17 = "reports/labelling/fresh17/manifest.json"  # Amendment 17: sealed blocks A, B (every item is test-only)
TRAIN16 = "reports/labelling/train16/manifest.json"  # Amendment 16: training additions (splits train16 / val16), never sealed
MAX_BYTES = 5_000_000
MIN_CHARS = 20  # shorter strings are not distinctive enough to be a meaningful hit
SKIP_PARTS = ("reports/labelling/", "reports/intent/")  # id/label tables hold hashes, never text


def _load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def safety_items() -> list[dict]:
    return _load(SAFETY)["items"] if (ROOT / SAFETY).exists() else []


def fresh_items() -> list[dict]:
    """Every fresh sealed item (Amendments 15 and 16), all test-only."""
    return [
        i
        for rel in (FRESH15, FRESH16, FRESH17)
        if (ROOT / rel).exists()
        for i in _load(rel)["items"]
    ]


def train16_items() -> list[dict]:
    return _load(TRAIN16)["items"] if (ROOT / TRAIN16).exists() else []


def sealed_hashes() -> set[str]:
    """Scale-sample test hashes, the safety stratum's test hashes (Amendment 6) and the fresh15 set (Amendment 15, all test)."""
    scale = {i["sha"] for i in _load(SCALE)["items"] if i["split"] == "test"}
    return (
        scale
        | {i["sha"] for i in safety_items() if i["split"] == "test"}
        | {i["sha"] for i in fresh_items()}
    )


def exclusions() -> set[str]:
    """Sealed hashes found in tracked files at ledger-build time: removed from every sealed evaluation."""
    if not LEDGER.exists():
        return set()
    return set(json.loads(LEDGER.read_text(encoding="utf-8")).get("sealed_excluded", []))


def digest(hashes: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(hashes)).encode()).hexdigest()


def build_ledger(excluded: set[str] | None = None) -> dict:
    entries: dict[str, str] = {}
    for rel in PILOTS:
        for i in _load(rel)["items"]:
            entries[i["sha"]] = Path(rel).parent.name
    for i in _load(SCALE)["items"]:
        if i["split"] != "test":
            entries[i["sha"]] = f"scale-{i['split']}"
    for i in safety_items():
        if (
            i["split"] != "test"
        ):  # train, val, and items dropped for near-duplicating a safety test item
            entries[i["sha"]] = f"safety-{i['split']}"
    for i in (
        train16_items()
    ):  # training additions are seen items (never sealed): later draws must avoid them
        entries[i["sha"]] = f"train16-{i['split']}"
    sealed = sealed_hashes()
    return {
        "note": "content hashes of items used for anything but final evaluation (Amendment 2)",
        "sealed_digest": digest(sealed),
        "sealed_count": len(sealed),
        "sealed_excluded": sorted(excluded or set()),
        "entries": dict(sorted(entries.items())),
    }


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    )
    return [p for p in out.stdout.splitlines() if not p.startswith(SKIP_PARTS)]


def _strings(node: object) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def candidates(path: Path) -> Iterator[str]:
    try:
        if path.stat().st_size > MAX_BYTES:
            return
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    yield from raw.splitlines()
    if path.suffix == ".json":
        try:
            yield from _strings(json.loads(raw))
        except ValueError:
            return
    elif path.suffix == ".jsonl":
        for ln in raw.splitlines():
            try:
                yield from _strings(json.loads(ln))
            except ValueError:
                continue


def scan_repo(sealed: set[str]) -> list[tuple[str, str]]:
    hits = []
    for rel in tracked_files():
        for s in candidates(ROOT / rel):
            c = clean(s)
            if len(c) >= MIN_CHARS and text_sha(c) in sealed:
                hits.append((rel, text_sha(c)))
    return hits


def near_hits() -> set[str]:
    """Sealed hashes with a near-duplicate or quoted copy in a tracked file (engine.labelling.leakage_neardup)."""
    from engine.labelling import leakage_neardup as nd

    return {h for _, h, _ in nd.scan(json.loads(nd.FP.read_text(encoding="utf-8")))}


def check() -> list[str]:
    problems: list[str] = []
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    sealed = sealed_hashes()
    if overlap := sealed & set(ledger["entries"]):
        problems.append(f"(a) {len(overlap)} sealed hashes are in the ledger")
    manifest = _load(SCALE)
    if bad := [i["id"] for i in manifest["items"] if i["split"] != "test" and i["sha"] in sealed]:
        problems.append(f"(b) {len(bad)} train/val manifest items share a sealed hash")
    if bad16 := [i["id"] for i in train16_items() if i["sha"] in sealed]:
        problems.append(f"(b) {len(bad16)} train16 items share a sealed hash")
    if digest(sealed) != ledger["sealed_digest"]:
        problems.append("(c) sealed-set digest differs from the one recorded in the ledger")
    expected = build_ledger()["entries"]
    if expected != ledger["entries"]:
        problems.append("ledger entries are stale: run `build`")
    leaked = {h for _, h in scan_repo(sealed)} | near_hits()
    if unrecorded := leaked - set(ledger["sealed_excluded"]):
        problems.append(
            f"(d) {len(unrecorded)} sealed texts found in tracked files and not excluded"
        )
    if stale := set(ledger["sealed_excluded"]) - sealed:
        problems.append(f"(d) {len(stale)} excluded hashes are not in the sealed set")
    # (e) S31: the near-duplicate scan only protects items that have a fingerprint. Amendment 16 added 3,900 sealed items without
    # rebuilding the fingerprint file, and every check above still passed: a sealed set the scan cannot see must fail loudly.
    from engine.labelling import leakage_neardup as nd

    fingerprinted = set(json.loads(nd.FP.read_text(encoding="utf-8"))["items"])
    if uncovered := sealed - fingerprinted:
        problems.append(
            f"(e) {len(uncovered)} sealed hashes have no near-duplicate fingerprint: rebuild "
            "reports/labelling/sealed_fingerprints.json (leakage_neardup build)"
        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "check"])
    cmd = ap.parse_args().cmd
    if cmd == "build":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        found = {h for _, h in scan_repo(sealed_hashes())} | near_hits()
        LEDGER.write_text(json.dumps(build_ledger(found), indent=1) + "\n", encoding="utf-8")
        print(f"{len(found)} sealed items found in tracked files: excluded from sealed evaluation")
        print(f"wrote {LEDGER}")
        return 0
    problems = check()
    for p in problems:
        print(p)
    print("leakage ledger: " + ("FAIL" if problems else "ok"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
