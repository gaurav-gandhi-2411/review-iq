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
not every conceivable one.
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
        chunk = review_text[:MIN_LEAK_CHUNK_LEN]
        for prompt_file, content in prompt_contents.items():
            if chunk in content:
                leaks.append((fixture_name, prompt_file, chunk))
    return leaks


def main() -> int:
    held_out_texts = _held_out_texts()
    if not held_out_texts:
        print("OK: no held-out fixtures exist yet -- nothing to check.")
        return 0

    prompt_contents = _prompt_file_contents()
    leaks = find_leaks(held_out_texts, prompt_contents)

    if leaks:
        print(f"FAIL: {len(leaks)} held-out corpus leakage(s) found:", file=sys.stderr)
        for fixture_name, prompt_file, chunk in leaks:
            print(f"  {fixture_name} -> {prompt_file}: {chunk!r}", file=sys.stderr)
        return 1

    print(
        f"OK: checked {len(held_out_texts)} held-out fixtures against "
        f"{len(prompt_contents)} prompt-development files -- no leakage found."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
