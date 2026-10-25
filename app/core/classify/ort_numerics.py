"""The ONNX Runtime numerics pin shared by serving, evaluation and the Kaggle kernels (S30 decision, GG).

Why. The static-int8 encoder's MatMuls run through ONNX Runtime's u8s8 kernels. On x64 CPUs without the VNNI instructions (the
laptop that produced every offline artifact) those kernels can saturate; on the Cloud Run CPU they do not. The same model file then gives
different probabilities on different hosts: S29 found 81 of 3028 sealed decisions differing between the laptop reference and the
deployed service (median per-review difference 0.008, p95 0.143). `session.x64quantprecision=1` makes ONNX Runtime take the
exact-precision path on every x64 host, so offline and served arithmetic agree by construction (S29 measured: with it the laptop
matches Cloud Run, 1 label mismatch of 800 task decisions against 21 without).

Rule: every `InferenceSession` in this repository is built from options that went through `apply_numerics_pin`
(`tests/unit/test_ort_numerics_pin.py` fails otherwise). Stdlib only, so the scripts and the Kaggle kernels can import it.
"""

from __future__ import annotations

from typing import Any

CONFIG_KEY = "session.x64quantprecision"
CONFIG_VALUE = "1"


def apply_numerics_pin(session_options: Any) -> Any:
    """Add the pin to an `onnxruntime.SessionOptions` and return it."""
    session_options.add_session_config_entry(CONFIG_KEY, CONFIG_VALUE)
    return session_options


def is_pinned(session_options: Any) -> bool:
    """True if the options carry the pin (what the tests and the equivalence report assert)."""
    try:
        return bool(session_options.get_session_config_entry(CONFIG_KEY) == CONFIG_VALUE)
    except RuntimeError:  # onnxruntime raises when the entry was never set
        return False
