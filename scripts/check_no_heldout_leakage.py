"""CI gate: fail if any held-out corpus review text has leaked into a prompt-development file.

Why this exists: eval/fixtures/_held_out_hindi_hinglish/ (Session 8 P3) only has value as a
genuine held-out measurement if nobody develops app/core/prompts/** having seen its content.
Directory-naming keeps it out of eval/runner.py's fixture scan (structural, verified there),
but that alone doesn't stop a human or agent from copy-pasting a held-out review into a
few-shot example while iterating on a prompt. This check makes that mechanically detectable:
it fingerprints every held-out fixture's review text and fails the build if a long enough
chunk of it shows up in a prompt file.

What this checks: every *.json file under eval/fixtures/_held_out_hindi_hinglish/ against
every file under app/core/prompts/ and PROMPTS.md (if it exists).

LIMITATION: this is a substring match on a rolling window, not semantic paraphrase
detection -- a prompt author who paraphrases a held-out review instead of copying it
verbatim would not be caught. It catches the actual observed leakage risk (copy-paste),
not every conceivable one. It also only reads `*.py` under app/core/prompts/ and PROMPTS.md:
a few-shot example living in another file type or directory is out of its surface.

History: the docstring always said "rolling window" but the code only compared the FIRST
MIN_LEAK_CHUNK_LEN characters of each review, so a review pasted with its opening trimmed
(or a longer review pasted from the middle) passed. It now tests every window. It also fails
closed when the corpus or the prompt files are missing, instead of printing OK for a scan of
nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HELDOUT_DIR = REPO_ROOT / "eval" / "fixtures" / "_held_out_hindi_hinglish"
PROMPT_PATHS: tuple[Path, ...] = (
    REPO_ROOT / "app" / "core" / "prompts",
    REPO_ROOT / "PROMPTS.md",
)

# A held-out review must contribute at least this many characters of unbroken text before
# it's treated as a leakage signal -- long enough that an accidental short overlap (a common
# short phrase two unrelated texts might share) can't false-positive.
MIN_LEAK_CHUNK_LEN = 40

# (held-out fixture file, prompt-development file) -> why this overlap is accepted. Only add an
# entry with a reason a reviewer can check; anything else is a real leak.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("hien-0014.json", "PROMPTS.md"): (
        "PROMPTS.md line ~51 quotes 'Boat ko thoda Volume Me sudhar karna chahiye' while "
        "annotating benchmark fixture bench-hien-011 (d87c3e5, 2026-07-26 -- six weeks BEFORE "
        "this held-out corpus was built, 60eac99). It is a changelog note, not text fed to a "
        "model; app/core/prompts/ has no overlap. Found only after the window fix (the old "
        "prefix-only comparison missed it). Whether the same review sitting in the benchmark "
        "gold set contaminates the held-out claim is UNVERIFIED -- surfaced to GG in "
        "docs/decorative-control-sweep.md."
    ),
}


def _held_out_texts() -> dict[str, str]:
    if not HELDOUT_DIR.is_dir():
        return {}
    texts: dict[str, str] = {}
    for path in sorted(HELDOUT_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data.get("text") or data.get("review_text") or ""
        if text:
            texts[path.name] = text
    return texts


def _prompt_file_contents() -> dict[str, str]:
    contents: dict[str, str] = {}
    for base in PROMPT_PATHS:
        if base.is_file():
            contents[str(base.relative_to(REPO_ROOT))] = base.read_text(encoding="utf-8")
        elif base.is_dir():
            for path in sorted(base.rglob("*.py")):
                contents[str(path.relative_to(REPO_ROOT))] = path.read_text(encoding="utf-8")
    return contents


def find_leaks(
    held_out_texts: dict[str, str], prompt_contents: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Returns [(held_out_fixture_name, prompt_file, matched_chunk), ...]."""
    leaks: list[tuple[str, str, str]] = []
    for fixture_name, review_text in held_out_texts.items():
        if len(review_text) < MIN_LEAK_CHUNK_LEN:
            continue
        windows = [
            review_text[i : i + MIN_LEAK_CHUNK_LEN]
            for i in range(len(review_text) - MIN_LEAK_CHUNK_LEN + 1)
        ]
        for prompt_file, content in prompt_contents.items():
            chunk = next((w for w in windows if w in content), None)
            if chunk is not None:
                leaks.append((fixture_name, prompt_file, chunk))
    return leaks


def main() -> int:
    held_out_texts = _held_out_texts()
    if not held_out_texts:
        # This used to print OK ("no held-out fixtures exist yet"). The corpus is committed
        # now, so an empty result means the directory was renamed/emptied or the fixtures'
        # text key changed -- and the quarantine would silently stop being enforced.
        print(
            f"FAIL: no held-out review text found under {HELDOUT_DIR} -- refusing to pass a "
            "scan of nothing.",
            file=sys.stderr,
        )
        return 1

    prompt_contents = _prompt_file_contents()
    if not prompt_contents:
        print("FAIL: no prompt-development files found to scan.", file=sys.stderr)
        return 1
    leaks = [
        leak
        for leak in find_leaks(held_out_texts, prompt_contents)
        if (leak[0], leak[1]) not in ALLOWLIST
    ]
    stale = [
        k
        for k in ALLOWLIST
        if not find_leaks(
            {k[0]: held_out_texts.get(k[0], "")}, {k[1]: prompt_contents.get(k[1], "")}
        )
    ]
    if stale:
        print(f"FAIL: stale ALLOWLIST entries (overlap no longer exists): {stale}", file=sys.stderr)
        return 1

    if leaks:
        print(f"FAIL: {len(leaks)} held-out corpus leakage(s) found:", file=sys.stderr)
        for fixture_name, prompt_file, chunk in leaks:
            print(f"  {fixture_name} -> {prompt_file}: {chunk!r}", file=sys.stderr)
        return 1

    print(
        f"OK: checked {len(held_out_texts)} held-out fixtures against "
        f"{len(prompt_contents)} prompt-development files -- no un-allowlisted leakage found "
        f"({len(ALLOWLIST)} allowlisted overlap(s), see ALLOWLIST)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
