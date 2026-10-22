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


def test_a_quote_with_its_last_letter_changed_is_still_contained(tmp_path) -> None:
    # S31 control audit: raising the containment threshold to 0.999 survived; one edited 12-gram of ~55 must still count
    longer = (
        "A customer wrote: " + ITEM[:-1] + "x. We reply to every review within two days and more."
    )
    assert any(k == "quoted-substring" for _, _, k in _scan(tmp_path, longer + "\n"))


def test_a_review_wrapped_across_two_lines_of_a_paragraph(tmp_path) -> None:
    # S31: neither half alone is a near-duplicate or a quote; only the joined paragraph candidate catches it
    half = len(ITEM) // 2
    assert _scan(tmp_path, "intro\n\n" + ITEM[:half] + "\n" + ITEM[half:] + "\n\noutro\n")


def test_a_review_stored_as_an_escaped_json_string(tmp_path) -> None:
    # S31: json.dumps escapes non-ASCII as \\uXXXX, so the raw line never matches; only the decoded JSON string does
    import json

    hindi = "कपड़े की सिलाई दो बार धोने के बाद ही खुल गई और बाईं आस्तीन भी फट गई"
    (tmp_path / "f.json").write_text(json.dumps({"review": hindi}), encoding="utf-8")
    assert nd.scan(_fp(hindi), files=["f.json"], root=tmp_path)


def test_unrelated_text_is_clean(tmp_path) -> None:
    assert (
        _scan(tmp_path, "this sentence is about something else entirely, nothing in common\n") == []
    )
