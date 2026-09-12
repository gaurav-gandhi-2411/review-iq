"""Wave 1 Section H: corpus_pipeline.adversarial_pairs -- mocked-client tests only.

No live Groq calls. Verifies: (1) prompt builders produce sane text, (2) every
generated record is correctly tagged held_out/synthetic_fake, (3) the generator
family list excludes Meta Llama (the detector + teacher family), (4) write_holdout
creates the DO-NOT-TRAIN README next to its output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmark.vernacular_v2.corpus_pipeline.adversarial_pairs import (
    GENERATOR_MODELS,
    GENERIC_FAKE_TEMPLATES,
    build_fabrication_prompt,
    build_paraphrase_prompt,
    build_template_shift_prompt,
    generate_sample,
    make_record,
    write_holdout,
)


def test_generator_models_exclude_meta_llama() -> None:
    families = {m["family"] for m in GENERATOR_MODELS}
    assert "Meta Llama" not in families
    assert families == {"OpenAI GPT-OSS", "Alibaba Qwen"}


def test_build_fabrication_prompt_includes_product_and_stars() -> None:
    prompt = build_fabrication_prompt("Wireless Mouse", 5, language="en")
    assert "Wireless Mouse" in prompt
    assert "5/5" in prompt


def test_build_fabrication_prompt_hien_language_instruction() -> None:
    prompt = build_fabrication_prompt("Headset", 1, language="hi-en")
    assert "Hinglish" in prompt


def test_build_paraphrase_prompt_wraps_original_text() -> None:
    prompt = build_paraphrase_prompt("Battery life is amazing")
    assert "Battery life is amazing" in prompt
    assert "<review>" in prompt


def test_build_template_shift_prompt_includes_template_and_variant_number() -> None:
    prompt = build_template_shift_prompt(GENERIC_FAKE_TEMPLATES[0], 3)
    assert GENERIC_FAKE_TEMPLATES[0] in prompt
    assert "variant #3" in prompt


def test_make_record_tags_held_out_and_synthetic_fake() -> None:
    rec = make_record(
        text="Great product!",
        attack_type="fabrication",
        generator_model="openai/gpt-oss-120b",
        generator_family="OpenAI GPT-OSS",
        source_note="test",
    )
    assert rec["held_out"] is True
    assert rec["label"] == "synthetic_fake"
    assert rec["excluded_families"] == ["Meta Llama"]
    assert rec["attack_type"] == "fabrication"


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    async def create(self, *, model: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(f"[fake generated review for {model}]")


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeGroqClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


async def test_generate_sample_produces_all_attack_types() -> None:
    client = _FakeGroqClient()
    real_reviews = [
        {"text": "Great battery life", "product_name": "Headset"},
        {"text": "Sound is tinny", "product_name": "Speaker"},
    ]
    results = await generate_sample(client, real_reviews, n_per_type_per_model=2, delay_seconds=0.0)
    attack_types = {r["attack_type"] for r in results}
    assert attack_types == {"fabrication", "paraphrase", "template_shift"}
    assert all(r["held_out"] is True for r in results)
    assert all(r["label"] == "synthetic_fake" for r in results)
    # 2 models * (2 fabrication + 2 paraphrase + 2 template_shift) = 12
    assert len(results) == 12


async def test_generate_sample_records_generator_error_without_crashing() -> None:
    class _FailingCompletions:
        async def create(self, *, model: str, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError("simulated outage")

    client = _FakeGroqClient()
    client.chat.completions = _FailingCompletions()  # type: ignore[assignment]

    real_reviews = [{"text": "Fine product", "product_name": "Widget"}]
    results = await generate_sample(client, real_reviews, n_per_type_per_model=1, delay_seconds=0.0)
    assert all(r["error"] is not None for r in results)
    assert all(r["text"] == "" for r in results)


def test_write_holdout_creates_readme_and_file(tmp_path: Path) -> None:
    out_path = tmp_path / "adversarial_holdout" / "sample.jsonl"
    records = [
        make_record(
            text="fake review",
            attack_type="fabrication",
            generator_model="openai/gpt-oss-120b",
            generator_family="OpenAI GPT-OSS",
            source_note="test",
        )
    ]
    write_holdout(records, out_path)

    assert out_path.exists()
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    written = json.loads(lines[0])
    assert written["held_out"] is True

    readme = out_path.parent / "README.md"
    assert readme.exists()
    assert "DO NOT TRAIN" in readme.read_text(encoding="utf-8")


def test_write_holdout_does_not_overwrite_existing_readme(tmp_path: Path) -> None:
    out_dir = tmp_path / "adversarial_holdout"
    out_dir.mkdir()
    readme = out_dir / "README.md"
    readme.write_text("custom content", encoding="utf-8")

    write_holdout([], out_dir / "sample.jsonl")
    assert readme.read_text(encoding="utf-8") == "custom content"
