"""Render reports/serving/v2_classify_bench.md from reports/serving/v2_classify_bench.json (METRICS blocks, no hand-typed numbers).

python scripts/render_classify_bench.py            # rewrite the .md
python scripts/render_classify_bench.py --check    # exit 1 if the committed .md differs from the render
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "reports/serving/v2_classify_bench.json"
MD_PATH = ROOT / "reports/serving/v2_classify_bench.md"
MB = 1e6


def block(name: str, body: str) -> str:
    return f"<!-- METRICS:START:{name} -->\n{body.rstrip()}\n<!-- METRICS:END -->\n"


def render(d: dict) -> str:
    lat = [
        "| Config | Cold start s | of which model load s | Batch 1 p50 / p95 ms (n) | Batch 8 p50 / p95 ms (n) | Peak RSS MB | cgroup peak incl. page cache MB | Limit MB | OOM |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in d["container_runs"]:
        lim = c["memory_limit_bytes"]
        lat.append(
            f"| {c['docker_cpus']} cpu / {c['docker_memory']} | {c['cold_start_s']} | {c['model_load_s']} | "
            f"{c['batch_1']['p50_ms']} / {c['batch_1']['p95_ms']} ({c['batch_1']['n']}) | "
            f"{c['batch_8']['p50_ms']} / {c['batch_8']['p95_ms']} ({c['batch_8']['n']}) | "
            f"{round(c['peak_rss_bytes'] / MB)} | {round(c['cgroup_peak_incl_page_cache_bytes'] / MB)} | {round(lim / MB) if lim else 'n/a'} | {c['oom_killed']} |"
        )
    img = d["image"]
    parity = [
        "| Mode | Items | Task decisions | Mismatches | Abstain-vs-answer | Different class | Max abs prob diff |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, m in d["parity"]["modes"].items():
        parity.append(
            f"| {k} | {m['n_items']} | {m['n_task_decisions']} | {m['n_mismatches']} | {m['mismatch_kinds']['abstain_vs_answer']} | "
            f"{m['mismatch_kinds']['different_class']} | {m['max_abs_prob_diff']:.6f} |"
        )
    cov = [
        "| Task | Labelled | Offline (batch 128) coverage / precision | Served per item coverage / precision |",
        "|---|---|---|---|",
    ]
    for t, o in d["parity"]["offline_reference_vs_consensus_labels"].items():
        s = d["parity"]["modes"]["served_per_item"]["vs_consensus_labels"][t]
        cov.append(
            f"| {t} | {o['n_labelled']} | {o['coverage']} / {o['precision_answered']} | {s['coverage']} / {s['precision_answered']} |"
        )
    ip = d["container_runs"][0]["in_container_parity"]
    return (
        "# POST /v2/classify: local Docker benchmark and parity (S24 A6)\n\n"
        f"Provenance: commit `{d['commit']}`; artifacts `reports/serving/v2_classify_bench.json` (assembled from the bench and parity "
        f"runs named in it). **{d['label']}** Served model: {d['served_model']}.\n\n"
        "## Image\n\n"
        + block(
            "image",
            f"- image size on disk (docker images): {img['disk_usage']}\n- compressed content size (docker image inspect .Size): {round(img['content_bytes'] / MB)} MB\n"
            f"- of which model file: {round(img['model_bytes'] / MB)} MB; torch (CPU wheel) in the venv: {img['torch_venv_mb']} MB\n- build wall time (cold cache): {img['build_s']} s\n",
        )
        + "\n## Container latency, cold start and memory\n\n"
        + block("container", "\n".join(lat) + f"\n\nMachine: {d['machine']}\n")
        + "\n## Does it fit? Memory probes (1 vCPU container, no swap) against the existing service\n\n"
        + block(
            "memory",
            "| Probe | Memory limit | Result | Idle RSS MB | Peak RSS MB | cgroup peak incl. page cache MB |\n"
            "|---|---|---|---|---|---|\n"
            + "\n".join(
                f"| {p['probe']} | {p['limit']} | {p['result']} | {p.get('idle_rss_mb', 'n/a')} | "
                f"{p.get('peak_rss_mb', 'n/a')} | {p.get('cgroup_peak_mb', 'n/a')} |"
                for p in d["memory_probes"]
            )
            + f"\n\nExisting public service (ops/runbooks/cloud-run-deploy.md): {d['existing_service']}\n",
        )
        + "\n## Parity against the offline evaluation (sealed set)\n\n"
        + block(
            "parity",
            f"- thresholds file equals the offline recomputation bit-for-bit: {d['parity']['thresholds_bit_for_bit']['equal']} "
            f"(sha256 `{d['parity']['thresholds_bit_for_bit']['committed_sha256'][:16]}`)\n- sealed ids: {d['parity']['n_sealed_ids']}; pred-file test items: {d['parity']['n_pred_test_items']}\n\n"
            + "\n".join(parity)
            + f"\n\nIn the container (batch 1 over HTTP): {ip['n_items']} items, {ip['n_mismatches_vs_offline']} mismatches vs offline, "
            f"mismatch set equals the host served_per_item set: {ip['mismatch_set_equals_host_served_per_item']}.\n",
        )
        + "\n## Coverage and precision against panel-consensus labels (silver, sealed set)\n\n"
        + block("coverage", "\n".join(cov) + "\n")
        + "\n"
        + d["notes"]
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    out = render(json.loads(JSON_PATH.read_text(encoding="utf-8")))
    if a.check:
        sys.exit(0 if MD_PATH.read_text(encoding="utf-8") == out else 1)
    MD_PATH.write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
