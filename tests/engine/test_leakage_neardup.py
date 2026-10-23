from __future__ import annotations

from engine.labelling import leakage_neardup as nd

ITEM = "the fabric pilled after two washes and the stitching came apart on the left sleeve"


def _fp(text: str) -> dict:
    n = nd.norm(text)
    return {
        "items": {
            "h1": {"n": len(n), "gj": sorted(nd.shingles(n, 4)), "g12": sorted(nd.shingles(n, 12))}
        }
    }


def _scan(tmp_path, body: str) -> list:
    (tmp_path / "f.txt").write_text(body, encoding="utf-8")
    return nd.scan(_fp(ITEM), files=["f.txt"], root=tmp_path)


def test_exact_copy_with_different_case_and_punctuation(tmp_path) -> None:
    assert _scan(tmp_path, "x\n" + ITEM.upper().replace(" ", ",  ") + "\n")


def test_one_word_edit_is_a_near_duplicate(tmp_path) -> None:
    edited = ITEM.replace("pilled", "piled")  # one-character typo
    hits = _scan(tmp_path, edited + "\n")
    assert any(k == "near-duplicate" for _, _, k in hits)


def test_quote_inside_longer_text(tmp_path) -> None:
    longer = "A customer wrote: " + ITEM + ". We reply to every review within two days and more."
    hits = _scan(tmp_path, longer + "\n")
    assert any(k == "quoted-substring" for _, _, k in hits)


def test_the_candidate_filters_lose_nothing_against_a_brute_force_scan(tmp_path) -> None:
    # S31: the prefix and 12-gram filters must find exactly what comparing every sealed item with every candidate finds
    import random

    rnd = random.Random(42)
    words = [
        "fabric",
        "zip",
        "sleeve",
        "refund",
        "broke",
        "late",
        "size",
        "colour",
        "smell",
        "taste",
        "packet",
        "cream",
        "pump",
        "lid",
    ]
    sealed = [" ".join(rnd.choice(words) for _ in range(rnd.randint(4, 30))) for _ in range(60)]
    lines = []
    for s in sealed[:20]:
        lines.append(
            s.replace(" ", " the ", 1)
        )  # light edit: near-duplicate or not, decided by the rule
        lines.append("A customer wrote: " + s + ". We answered.")  # quote
    lines += [" ".join(rnd.choice(words) for _ in range(rnd.randint(4, 30))) for _ in range(40)]
    (tmp_path / "f.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    fp = {"items": {}}
    for k, s in enumerate(sealed):
        n = nd.norm(s)
        fp["items"][f"h{k}"] = {
            "n": len(n),
            "gj": sorted(nd.shingles(n, 4)),
            "g12": sorted(nd.shingles(n, 12)),
        }
    import zlib

    all12 = {g for v in fp["items"].values() for g in v["g12"]}
    brute = set()
    for line in lines:
        n = nd.norm(line)
        if len(n) < nd.MIN_CAND_CHARS:
            continue
        # the scan's documented sampled-12-gram prefilter, applied identically; everything after it is brute force
        if not any(
            zlib.crc32(n[i : i + 12].encode("utf-8")) in all12 for i in range(0, len(n) - 11, 2)
        ):
            continue
        gj, g12 = nd.shingles(n, 4), nd.shingles(n, 12)
        for h, v in fp["items"].items():
            sj, s12 = set(v["gj"]), set(v["g12"])
            if len(sj & gj) / max(1, len(sj | gj)) >= nd.JACCARD:
                brute.add((h, "near-duplicate"))
            elif v["n"] >= nd.MIN_QUOTE_CHARS and s12 and len(s12 & g12) / len(s12) >= nd.CONTAIN:
                brute.add((h, "quoted-substring"))
    got = {(h, kind) for _, h, kind in nd.scan(fp, files=["f.txt"], root=tmp_path)}
    assert got == brute and len(brute) >= 20


def test_unrelated_text_is_clean(tmp_path) -> None:
    assert (
        _scan(tmp_path, "this sentence is about something else entirely, nothing in common\n") == []
    )
