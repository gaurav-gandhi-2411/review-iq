"""Build the R1+ served-vs-evaluated parity fixture from the run-2 logits A17 and A18 were scored on (S35).

    PYTHONPATH=. python scripts/build_router_parity_fixture.py \
        --f17 D:/ml-cache/s31/run2_f17_out/out/logits.npz --f18 D:/ml-cache/s33/fresh18_run2_pinned.npz \
        --out reports/intent/router_parity

Writes `run2_served_logits.npz` (ids, the 14 SERVED logit columns as float32, and the R1 / R1+ bits computed by the EVALUATED
function `engine.intent.fresh17_eval.routes`) and `provenance.json`. Refuses to write unless the evaluated bits reproduce the
committed results (A17 block A routed share R1+ 534 / 1800 and R1 481 / 1800; A18 R1+ routed share 1206 / 8100), so the fixture
is provably the logits that were scored. Ids and numbers only: no review text. Needs the eval extras (sklearn); the CI test that
reads the fixture does not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from app.core.classify import runtime
from engine.intent.fresh17_eval import THRESHOLDS, routes
from engine.intent.quantize_m1 import SERVED

TAU_FILE = Path("reports/intent/a17/tau.json")
MANIFESTS = {
    "f17": Path("reports/labelling/fresh17/manifest.json"),
    "f18": Path("reports/labelling/fresh18/manifest.json"),
}
# (key in the source npz, anchors from the committed result files)
SOURCES = {"f17": "pinned", "f18": "logits"}
ANCHORS = {
    "f17": {
        "result": "reports/intent/fresh17/result.json",
        "block_A_r1_plus": 534,
        "block_A_r1": 481,
        "n_A": 1800,
    },
    "f18": {"result": "reports/intent/fresh18/result.json", "all_r1_plus": 1206, "n": 8100},
}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_lf(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def sha256_lf_as_crlf(p: Path) -> str:
    return hashlib.sha256(
        p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    ).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--f17", type=Path, required=True)
    ap.add_argument("--f18", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    th = runtime.load_thresholds(THRESHOLDS)
    tau = float(json.loads(TAU_FILE.read_text(encoding="utf-8"))["tau"])
    n_cols = sum(len(th.tasks[t].classes) for t in SERVED)
    arrays: dict[str, np.ndarray] = {}
    prov: dict = {
        "thresholds": Path(THRESHOLDS).as_posix(),
        # text hashes are of LF bytes (the git blob), so they match on any checkout; the A17/A18 results and the live service
        # report the CRLF bytes' hash (files read from a Windows working tree), recorded here as well
        "thresholds_sha256_lf": sha256_lf(Path(THRESHOLDS)),
        "thresholds_sha256_crlf_as_recorded": sha256_lf_as_crlf(Path(THRESHOLDS)),
        "tau": tau,
        "tau_file_sha256_lf": sha256_lf(TAU_FILE),
        "served_tasks": list(SERVED),
        "served_columns": n_cols,
        "evaluated_function": "engine.intent.fresh17_eval.routes",
        "sets": {},
    }
    for name, src in (("f17", a.f17), ("f18", a.f18)):
        z = np.load(src)
        ids = [str(x) for x in z["ids"]]
        items = json.loads(MANIFESTS[name].read_text(encoding="utf-8"))["items"]
        if ids != [r["id"] for r in items]:
            raise SystemExit(f"{name}: logits are for different items than {MANIFESTS[name]}")
        full = z[SOURCES[name]]
        r1, rp = routes(th, full, tau)
        anc = ANCHORS[name]
        if name == "f17":
            a_idx = [i for i, r in enumerate(items) if r["block"] == "A"]
            got = (int(rp[a_idx].sum()), int(r1[a_idx].sum()), len(a_idx))
            want = (anc["block_A_r1_plus"], anc["block_A_r1"], anc["n_A"])
        else:
            got, want = (int(rp.sum()), len(ids)), (anc["all_r1_plus"], anc["n"])
        if got != want:
            raise SystemExit(
                f"{name}: evaluated routes {got} do not reproduce {anc['result']} {want}"
            )
        arrays[f"{name}_ids"] = np.array(ids)
        arrays[f"{name}_logits"] = np.ascontiguousarray(full[:, :n_cols], dtype=np.float32)
        arrays[f"{name}_r1"] = r1.astype(bool)
        arrays[f"{name}_r1_plus"] = rp.astype(bool)
        prov["sets"][name] = {
            "source_file": src.name,
            "source_key": SOURCES[name],
            "source_sha256": sha256(src),
            "manifest": MANIFESTS[name].as_posix(),
            "manifest_sha256_lf": sha256_lf(MANIFESTS[name]),
            "n_items": len(ids),
            "r1_routed": int(r1.sum()),
            "r1_plus_routed": int(rp.sum()),
            "anchor": {"result": anc["result"], "reproduced": list(got)},
        }
    a.out.mkdir(parents=True, exist_ok=True)
    npz = a.out / "run2_served_logits.npz"
    np.savez_compressed(npz, **arrays)
    prov["fixture_sha256"] = sha256(npz)
    (a.out / "provenance.json").write_text(json.dumps(prov, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in prov.items() if k != "sets"} | {"sets": prov["sets"]}, indent=1
        )
    )


if __name__ == "__main__":
    main()
