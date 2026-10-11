from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine.labelling import scale_plan as SP


def items(n: int = 10) -> list[dict]:
    return [
        {"id": f"i{k}", "split": "test" if k % 5 == 0 else "train", "text": "t"} for k in range(n)
    ]


def jrec(i: str, **kw: str) -> dict:
    t = {
        "needs_action": "no",
        "broad_intent": "praise",
        "sentiment": "positive",
        "urgency": "low",
        "aspects": {},
    }
    t.update(kw)
    return {"id": i, "judge": "x", "text": t}


def write(p: Path, recs: list[dict]) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")


def test_select_items_and_shards() -> None:
    rows = items()
    assert len(SP.select_items(rows, "A")) == 10
    assert [r["id"] for r in SP.select_items(rows, "S")] == ["i0", "i5"]
    with pytest.raises(ValueError):
        SP.select_items(rows, "B")
    assert sorted({SP.shard_of(k) for k in range(10)}) == [0, 1]


def test_pending_chunks_skip_done_and_respect_size() -> None:
    ch = SP.pending_chunks(items(10), {"i0", "i1"}, size=3)
    assert [[r["id"] for r in c] for c in ch] == [
        ["i2", "i3", "i4"],
        ["i5", "i6", "i7"],
        ["i8", "i9"],
    ]
    assert SP.pending_chunks(items(3), {"i0", "i1", "i2"}) == []


def test_stage_b_picks_disagreements_in_train_only_and_refuses_incomplete(tmp_path) -> None:  # noqa: ANN001
    rows = items(10)
    ids = [r["id"] for r in rows]
    write(tmp_path / "llama31_8b.jsonl", [jrec(i) for i in ids])
    write(
        tmp_path / "qwen3_8b.jsonl",
        [jrec(i, urgency="high") if i in ("i1", "i5") else jrec(i) for i in ids],
    )
    # i5 is sealed (test): mistral already runs there in stage S, so it is not in stage B
    assert SP.compute_stage_b(rows, tmp_path) == ["i1"]
    write(tmp_path / "qwen3_8b.jsonl", [jrec(i) for i in ids[:-1]])
    with pytest.raises(ValueError):
        SP.compute_stage_b(rows, tmp_path)  # fail closed on an incomplete stage A


def test_merge_shards_dedupes_and_renames_judge(tmp_path) -> None:  # noqa: ANN001
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    write(src / "llama31_8b__shard0.jsonl", [jrec("i0"), jrec("i2")])
    write(src / "llama31_8b__shard1.jsonl", [jrec("i1"), jrec("i2")])
    write(src / "mistral_7b.jsonl", [jrec("i0")])
    assert SP.merge_shards(src, dst) == {"llama31_8b": 3, "mistral_7b": 1}
    got = SP.read_jsonl(dst / "llama31_8b.jsonl")
    assert [r["id"] for r in got] == ["i0", "i1", "i2"] and {r["judge"] for r in got} == {
        "llama31_8b"
    }


def test_estimate_matches_hand_arithmetic() -> None:
    e = SP.estimate(5000, 750, 0.30)
    assert e["gpu_hours_by_stage"]["A"] == round(5000 * (3.292 + 2.783) / 3600, 2)
    assert e["gpu_hours_by_stage"]["S"] == round(750 * (4.392 + 3.939) / 3600, 2)
    assert e["gpu_hours_by_stage"]["B"] == round(4250 * 0.30 * 3.939 / 3600, 2)
    assert e["wall_hours_balanced_on_gpus"] == pytest.approx(e["gpu_hours_total"] / 2, abs=0.01)


def test_stage_plan_covers_the_amendment() -> None:
    judges = {j for gpus, _ in SP.STAGES.values() for js in gpus.values() for j in js}
    assert judges == set(SP.JUDGES)
    assert SP.STAGES["A"][0][0] == ["llama31_8b", "qwen3_8b"]
    assert (
        "gemma2_9b" not in SP.STAGES["A"][0][0] + SP.STAGES["B"][0][0]
    )  # gemma only on sealed items
