"""Unit tests for scripts/check_eval_model_matches_config.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_eval_model_matches_config.py"


def _run_with_results(tmp_path: Path, payload: dict | None) -> subprocess.CompletedProcess:
    results_path = tmp_path / "results.json"
    if payload is not None:
        results_path.write_text(json.dumps(payload), encoding="utf-8")

    # Run the script as a subprocess with RESULTS_PATH monkeypatched via a tiny wrapper,
    # since the script hardcodes its own path relative to the repo root -- simplest way
    # to test both branches without touching the real eval/results.json.
    wrapper = tmp_path / "run_check.py"
    wrapper.write_text(
        f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(REPO_ROOT)!r})
import scripts.check_eval_model_matches_config as m
m.RESULTS_PATH = Path({str(results_path)!r})
sys.exit(m.main())
""",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(wrapper)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_missing_results_file_fails(tmp_path: Path) -> None:
    result = _run_with_results(tmp_path, None)
    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_missing_provenance_fields_fails(tmp_path: Path) -> None:
    result = _run_with_results(tmp_path, {"overall_score": 0.8})
    assert result.returncode == 1
    assert "no groq_model_small/groq_model_large" in result.stdout


def test_matching_models_passes(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from app.core.config import get_settings

    settings = get_settings()
    result = _run_with_results(
        tmp_path,
        {
            "groq_model_small": settings.groq_model_small,
            "groq_model_large": settings.groq_model_large,
        },
    )
    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_mismatched_small_model_fails(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from app.core.config import get_settings

    settings = get_settings()
    result = _run_with_results(
        tmp_path,
        {
            "groq_model_small": "some-deprecated-model-name",
            "groq_model_large": settings.groq_model_large,
        },
    )
    assert result.returncode == 1
    assert "groq_model_small" in result.stdout
    assert "some-deprecated-model-name" in result.stdout


def test_mismatched_large_model_fails(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from app.core.config import get_settings

    settings = get_settings()
    result = _run_with_results(
        tmp_path,
        {
            "groq_model_small": settings.groq_model_small,
            "groq_model_large": "some-other-deprecated-model",
        },
    )
    assert result.returncode == 1
    assert "groq_model_large" in result.stdout
