from __future__ import annotations

import json

import pandas as pd
import pytest
from engine.labelling import scale_sample as SS


def _frames(n: int) -> dict:
    def mk(p: str, k: int) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "text": [
                    f"{p} review number {i} is fine and long enough to pass the length filter"
                    for i in range(k)
                ],
                "stars": [4.0] * k,
            }
        )

    return {
        "apparel": mk("a", n),
        "food": mk("f", n),
        "beauty": mk("b", n),
        "vernacular": mk("v", 30),
    }


def test_split_is_deterministic_and_roughly_15_percent_test() -> None:
    shas = [f"{i:016x}" for i in range(20000)]
    sp = [SS.split_of(s) for s in shas]
    assert sp == [SS.split_of(s) for s in shas]
    assert abs(sp.count("test") / len(sp) - 0.15) < 0.01
    assert abs(sp.count("val") / len(sp) - 0.085) < 0.01


def test_allocation_caps_small_pool_and_splits_rest_equally() -> None:
    a = SS.allocate({"apparel": 9000, "food": 9000, "beauty": 9000, "vernacular": 150}, 5000)
    assert a["vernacular"] == 150 and sum(a.values()) == 5000
    eng = [a["apparel"], a["food"], a["beauty"]]
    assert max(eng) - min(eng) <= 1


def test_draw_excludes_banned_and_manifest_has_no_text() -> None:
    frames = _frames(400)
    first, _ = SS.build_pools(frames, set())
    banned = {s for _, _, s in first["apparel"][:100]} | {s for _, _, s in first["food"][:50]}
    pools, stats = SS.build_pools(frames, banned)
    assert stats["apparel"]["excluded_as_seen_before"] == 100
    rows = SS.draw(pools, 300)
    assert rows == SS.draw(pools, 300)  # seeded
    assert not ({r["sha"] for r in rows} & banned)
    assert len({r["sha"] for r in rows}) == len(rows)
    man = SS.manifest_of(rows, {"manifests": {}, "ledger": None}, stats, 300)
    blob = json.dumps(man)
    assert all(r["text"] not in blob for r in rows)
    assert set(man["items"][0]) == {"id", "stratum", "split", "sha"}
    assert sum(sum(c.values()) for c in man["counts"].values()) == len(rows)


def test_split_is_a_function_of_content_not_of_draw_order() -> None:
    pools, _ = SS.build_pools(_frames(400), set())
    a = {r["sha"]: r["split"] for r in SS.draw(pools, 300, seed=44)}
    b = {r["sha"]: r["split"] for r in SS.draw(pools, 300, seed=7)}
    assert a.keys() & b.keys()
    assert all(a[s] == b[s] for s in a.keys() & b.keys())


def test_load_banned_reads_manifests_and_ledger_and_rejects_bad_ledger(tmp_path) -> None:  # noqa: ANN001
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"items": [{"sha": "aa"}, {"sha": "bb"}]}))
    lg = tmp_path / "l.json"
    lg.write_text(json.dumps({"hashes": ["cc"]}))
    banned, info = SS.load_banned([str(m)], str(lg))
    assert banned == {"aa", "bb", "cc"} and info["ledger"]["hashes"] == 1
    lg.write_text(json.dumps([{"nope": 1}]))
    with pytest.raises(ValueError):
        SS.load_banned([], str(lg))
