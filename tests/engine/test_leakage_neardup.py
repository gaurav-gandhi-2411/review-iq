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


def test_unrelated_text_is_clean(tmp_path) -> None:
    assert (
        _scan(tmp_path, "this sentence is about something else entirely, nothing in common\n") == []
    )
