"""S35: the SERVED R1+ router equals the EVALUATED R1+ (Amendments 17 and 18) bit for bit on the stored run-2 logits.

The fixture `reports/intent/router_parity/run2_served_logits.npz` holds the 14 served logit columns of the logits A17 (fresh17,
4,200 items) and A18 (fresh18, 8,100 items) were scored on, plus the R1 / R1+ bits from the evaluated function
`engine.intent.fresh17_eval.routes`. `scripts/build_router_parity_fixture.py` refuses to write it unless those bits reproduce the
committed result files. This test needs only numpy (no sklearn / onnxruntime), so it runs in CI and fails on any divergence:
a change to the thresholds file, the served tau, `runtime.decisions` or `route_r1_plus` that moves one decision is caught.
Pinned numerics: the logits are the stored ones, so no model runs here; live-service numerics are measured separately.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from app.core.classify import encoder_first as ef
from app.core.classify import runtime

FIX = Path("reports/intent/router_parity")
NPZ = FIX / "run2_served_logits.npz"
PROV = json.loads((FIX / "provenance.json").read_text(encoding="utf-8"))
THRESHOLDS = Path(PROV["thresholds"])


@pytest.fixture(scope="module")
def z() -> dict[str, np.ndarray]:
    with np.load(NPZ) as f:
        return {k: f[k] for k in f.files}


def _served(
    th: runtime.Thresholds, logits: np.ndarray, tau: float | None = None
) -> tuple[np.ndarray, np.ndarray]:
    sizes = {t: len(th.tasks[t].classes) for t in PROV["served_tasks"]}
    cols, i = {}, 0
    for t, n in sizes.items():
        cols[t] = logits[:, i : i + n]
        i += n
    assert i == logits.shape[1] == PROV["served_columns"]
    dec = runtime.decisions(th, cols)
    r1 = np.array([ef.route_r1(d).route for d in dec])
    rp = np.array([ef.route_r1_plus(d, tau).route for d in dec])
    return r1, rp


def test_fixture_is_the_recorded_one() -> None:
    assert hashlib.sha256(NPZ.read_bytes()).hexdigest() == PROV["fixture_sha256"]
    # runtime hashes RAW bytes, which depend on the checkout's line endings (CRLF on a Windows working tree, LF in git / CI).
    # Compare line-ending-independently: the LF bytes are the git blob; the same content with CRLF endings is the hash the
    # A17/A18 results and the live service report.
    lf = THRESHOLDS.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(lf).hexdigest() == PROV["thresholds_sha256_lf"]
    crlf = hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()
    assert crlf == PROV["thresholds_sha256_crlf_as_recorded"]
    for result in ("reports/intent/fresh17/result.json", "reports/intent/fresh18/result.json"):
        assert json.loads(Path(result).read_text(encoding="utf-8"))["thresholds_sha256"] == crlf
    # the served config names the same thresholds file (by its recorded hash) and the same tau
    served_cfg = json.loads(ef.R1_PLUS_CONFIG.read_text(encoding="utf-8"))
    assert served_cfg["thresholds_sha256"] == crlf
    assert ef.r1_plus_tau() == PROV["tau"]


@pytest.mark.parametrize("name", ["f17", "f18"])
def test_served_r1_plus_equals_evaluated_bit_for_bit(z: dict[str, np.ndarray], name: str) -> None:
    th = runtime.load_thresholds(THRESHOLDS)
    r1, rp = _served(th, z[f"{name}_logits"])
    assert len(rp) == PROV["sets"][name]["n_items"]
    diverged = np.flatnonzero(rp != z[f"{name}_r1_plus"])
    assert diverged.size == 0, (
        f"{name}: served R1+ differs on {[str(z[f'{name}_ids'][i]) for i in diverged[:10]]}"
    )
    assert np.array_equal(r1, z[f"{name}_r1"])
    assert int(rp.sum()) == PROV["sets"][name]["r1_plus_routed"]


def test_the_check_can_fail(z: dict[str, np.ndarray]) -> None:
    # control audit: a tau one grid step away must move decisions on fresh18, or the parity above proves nothing
    th = runtime.load_thresholds(THRESHOLDS)
    _, moved = _served(th, z["f18_logits"], tau=0.0705)
    assert int((moved != z["f18_r1_plus"]).sum()) > 0


def test_evaluated_function_agrees_on_the_fixture(z: dict[str, np.ndarray]) -> None:
    # local-only (eval extras): call the evaluation's own function on the same columns
    pytest.importorskip("sklearn")
    from engine.intent.fresh17_eval import routes

    th = runtime.load_thresholds(THRESHOLDS)
    for name in ("f17", "f18"):
        r1, rp = routes(th, z[f"{name}_logits"], PROV["tau"])
        assert np.array_equal(rp, _served(th, z[f"{name}_logits"])[1])
        assert np.array_equal(r1, z[f"{name}_r1"])
