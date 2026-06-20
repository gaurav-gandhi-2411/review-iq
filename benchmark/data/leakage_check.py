"""
Leakage checker: identifies benchmark candidates whose review text appears in any
CI eval fixture (or any other text review-iq was developed against).

Standard: exact SHA256 match on normalize(text) = strip().lower().
Coverage: all JSON/JSONL files under eval/fixtures/, eval/authenticity/fixtures/,
          eval/reply/fixtures/. Text field is "review_text" or "text" depending on fixture type.

Usage:
    checker = LeakageChecker.from_eval_dir(Path("eval"))
    result = checker.check(candidates)   # returns LeakageReport
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


def _normalize(text: str) -> str:
    """Canonical form for comparison: strip whitespace, collapse internal runs, lowercase."""
    return re.sub(r"\s+", " ", text.strip()).lower()


def _sha256(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def _extract_text_from_obj(obj: dict[str, object]) -> str | None:
    """Return the review text from a fixture object, regardless of field name."""
    for field_name in ("review_text", "text"):
        val = obj.get(field_name)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _load_fixture_texts(fixture_root: Path) -> dict[str, str]:
    """Return {sha256: source_path_str} for every review text in all fixture files."""
    seen: dict[str, str] = {}
    glob_patterns = ["**/*.json", "**/*.jsonl"]
    for pat in glob_patterns:
        for fpath in fixture_root.glob(pat):
            source = str(fpath.relative_to(fixture_root.parent))
            try:
                content = fpath.read_text(encoding="utf-8")
            except OSError:
                continue
            if fpath.suffix == ".jsonl":
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        text = _extract_text_from_obj(obj)
                        if text:
                            seen[_sha256(text)] = source
                    except json.JSONDecodeError:
                        continue
            else:
                try:
                    obj = json.loads(content)
                    text = _extract_text_from_obj(obj)
                    if text:
                        seen[_sha256(text)] = source
                except json.JSONDecodeError:
                    continue
    return seen


@dataclass
class LeakageReport:
    total_candidates: int
    leaked: list[dict[str, str]] = field(default_factory=list)  # [{id, text_preview, source}]
    clean: list[str] = field(default_factory=list)  # candidate ids that passed

    @property
    def n_leaked(self) -> int:
        return len(self.leaked)

    @property
    def n_clean(self) -> int:
        return len(self.clean)

    def summary(self) -> str:
        lines = [
            f"Leakage check: {self.total_candidates} candidates",
            f"  Clean:  {self.n_clean}",
            f"  Leaked: {self.n_leaked}",
        ]
        for entry in self.leaked:
            preview = entry["text_preview"][:60].replace("\n", " ")
            lines.append(f"    [LEAKED] id={entry['id']} matched {entry['source']}")
            lines.append(f"             text: {preview!r}...")
        return "\n".join(lines)


class LeakageChecker:
    """Checks candidate review texts against the CI fixture corpus."""

    def __init__(self, fixture_hashes: dict[str, str]) -> None:
        # fixture_hashes: {sha256_hex: source_description}
        self._hashes = fixture_hashes

    @classmethod
    def from_eval_dir(cls, eval_dir: Path) -> LeakageChecker:
        """Load all fixture texts from the three subdirectories under eval_dir."""
        hashes: dict[str, str] = {}
        for subdir in ("fixtures", "authenticity/fixtures", "reply/fixtures"):
            target = eval_dir / subdir
            if target.exists():
                hashes.update(_load_fixture_texts(target))
        return cls(hashes)

    @property
    def fixture_count(self) -> int:
        return len(self._hashes)

    def is_leaked(self, text: str) -> str | None:
        """Return source description if text matches a CI fixture, else None."""
        return self._hashes.get(_sha256(text))

    def check(self, candidates: list[dict[str, object]]) -> LeakageReport:
        """
        Check a list of candidate dicts. Each must have 'id' (str) and 'text' (str).
        Returns a LeakageReport.
        """
        report = LeakageReport(total_candidates=len(candidates))
        for cand in candidates:
            cid = str(cand.get("id", ""))
            text = str(cand.get("text", ""))
            source = self.is_leaked(text)
            if source:
                report.leaked.append(
                    {"id": cid, "text_preview": text[:120], "source": source}
                )
            else:
                report.clean.append(cid)
        return report
