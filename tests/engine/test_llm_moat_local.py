from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from engine.intent import llm_moat as M
from engine.intent import llm_moat_local as L

GOOD = {t: {"label": M.CLASSES[t][0], "confidence": 90} for t in M.TASKS}


class _Fake:
    """A fake OpenAI-compatible server; `script` maps a request counter to (status, content)."""

    def __init__(self, script):
        self.script, self.calls, self.bodies = script, 0, []
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.bodies.append(body)
                status, content = fake.script(fake.calls, body)
                fake.calls += 1
                payload = json.dumps(
                    {
                        "choices": [{"message": {"content": content}}],
                        "usage": {"total_tokens": 7, "prompt_tokens": 5},
                    }
                ).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def fake_ok():
    f = _Fake(lambda n, b: (200, json.dumps(GOOD)))
    yield f
    f.close()


def test_body_matches_groq_request(monkeypatch):
    captured = {}

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, json, **kw: captured.update(json) or R())
    M.ask("k", "openai/gpt-oss-20b", "hello world")
    local = L.build_body("openai/gpt-oss-20b", "hello world")
    assert local == captured


def test_parse_content():
    assert L.parse_content(json.dumps(GOOD)) == GOOD
    assert L.parse_content("Here:\n" + json.dumps(GOOD) + "\nDone") == GOOD
    assert L.parse_content("") is None
    assert L.parse_content(None) is None
    assert L.parse_content("not json at all") is None
    assert L.parse_content("[1, 2]") is None


def test_ask_local_ok_and_no_json_mode(fake_ok):
    ans, used = L.ask_local(fake_ok.url, "m", "x")
    assert ans == GOOD and used == 7
    assert fake_ok.bodies[0]["response_format"] == {"type": "json_object"}
    L.ask_local(fake_ok.url, "m", "x", json_mode=False)
    assert "response_format" not in fake_ok.bodies[1]


def test_unparseable_is_an_answer_and_scores_wrong_at_zero():
    f = _Fake(lambda n, b: (200, "oops"))
    try:
        ans, _ = L.ask_local(f.url, "m", "x")
    finally:
        f.close()
    assert ans is None
    c, k = M.llm_arrays([{"id": "a", "ans": None}], {"a": "yes"}, "needs_action")
    assert not c[0] and k[0] == 0.0


def test_5xx_retried_then_succeeds():
    f = _Fake(lambda n, b: (503, "") if n < 2 else (200, json.dumps(GOOD)))
    try:
        ans, _ = L.ask_local(f.url, "m", "x", sleep=0.0)
    finally:
        f.close()
    assert ans == GOOD and f.calls == 3


def test_persistent_error_raises_and_nothing_recorded(tmp_path):
    f = _Fake(lambda n, b: (500, ""))
    items = [{"id": "a", "text": "t"}]
    try:
        with pytest.raises(M.InfraError):
            L.ask_local(f.url, "m", "x", retries=2, sleep=0.0)
    finally:
        f.close()
    assert L.merge(tmp_path, ["a"]) == [] and items


def test_run_resume_order_and_interim_rule(tmp_path, fake_ok):
    items = [{"id": f"i{k}", "text": f"review {k}"} for k in range(8)]
    seen = {}

    def decide_fn(rows):
        seen["n"] = len(rows)
        return {t: {"decided": False} for t in M.TASKS}

    # a prior session already wrote i1 to shard 1: it must be skipped, not re-asked
    (tmp_path / "shards").mkdir()
    (tmp_path / "shards" / f"{L.STEM}__shard1.jsonl").write_text(
        json.dumps({"id": "i1", "ans": GOOD, "tokens": 5}) + "\n"
    )
    res = L.run_local(items, [fake_ok.url, fake_ok.url], tmp_path, decide_fn=decide_fn, n=4)
    assert res["complete"] and seen["n"] == 4
    assert fake_ok.calls == 7
    rows = L.merge(tmp_path / "shards", [i["id"] for i in items])
    assert [r["id"] for r in rows] == [i["id"] for i in items]
    assert set(rows[0]) == {"id", "ans", "tokens"}
    assert (tmp_path / f"{L.STEM}__interim4.json").exists()


def test_run_stops_when_all_tasks_decided(tmp_path, fake_ok):
    items = [{"id": f"i{k}", "text": "t"} for k in range(8)]
    res = L.run_local(
        items,
        [fake_ok.url],
        tmp_path,
        decide_fn=lambda r: {t: {"decided": True} for t in M.TASKS},
        n=3,
    )
    assert res["rule_fired_all"] and not res["complete"]
    assert fake_ok.calls == 3


def test_worker_failure_surfaces_and_records_nothing(tmp_path, monkeypatch):
    f = _Fake(lambda n, b: (500, ""))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    try:
        with pytest.raises(M.InfraError):
            L.run_local([{"id": "a", "text": "t"}], [f.url], tmp_path, n=1)
    finally:
        f.close()
    assert L.merge(tmp_path / "shards", ["a"]) == []


def test_smoke_limit_runs_only_limit(tmp_path, fake_ok):
    items = [{"id": f"i{k}", "text": "t"} for k in range(6)]
    res = L.run_local(items, [fake_ok.url], tmp_path, limit=2)
    assert fake_ok.calls == 2 and res["interim"] is None


SHOTS = M.shots_text(
    [
        ("s1", "great {phone}", {t: M.CLASSES[t][0] for t in M.TASKS}),
        ("s2", "broke on day 2", {t: M.CLASSES[t][-1] for t in M.TASKS}),
    ]
)


def test_render_prompt_zero_shot_is_byte_identical():
    for text in ("hello", "has {braces} and {0}", "x" * 3000):
        assert L.render_prompt(text) == M.PROMPT.format(text=text[:1500])


def test_render_prompt_inserts_shots_before_review_and_tolerates_braces():
    out = L.render_prompt("the review {x}", SHOTS)
    assert SHOTS in out and out.index(SHOTS) < out.index("Review: the review {x}")
    assert out.startswith(M.PROMPT.split("Review: {text}")[0].format())
    assert out.endswith("Review: the review {x}\nJSON:")
    assert out.count("Review:") == 3  # two shots + the item
    # the review is still truncated to 1500 characters
    assert "y" * 1501 not in L.render_prompt("y" * 2000, SHOTS)


def test_few_shot_run_records_prompt_tokens_but_merge_strips_them(tmp_path, fake_ok):
    items = [{"id": f"i{k}", "text": "t"} for k in range(4)]
    res = L.run_local(items, [fake_ok.url], tmp_path, stem=L.STEM_FEWSHOT, shots=SHOTS, n=2)
    assert res["complete"] and res["stats"][0]["max_prompt_tokens"] == 5
    assert all(SHOTS in b["messages"][0]["content"] for b in fake_ok.bodies)
    shard = tmp_path / "shards" / f"{L.STEM_FEWSHOT}__shard0.jsonl"
    assert all(json.loads(ln)["ptoks"] == 5 for ln in shard.read_text().splitlines())
    rows = L.merge(tmp_path / "shards", [i["id"] for i in items], L.STEM_FEWSHOT)
    assert [set(r) for r in rows] == [{"id", "ans", "tokens"}] * 4
    assert (tmp_path / f"{L.STEM_FEWSHOT}__interim2.json").exists() is False  # no decide_fn given


def test_merge_stem_keeps_arms_apart(tmp_path):
    d = tmp_path
    for stem, ans in ((L.STEM, "zs"), (L.STEM_FEWSHOT, "fs")):
        (d / f"{stem}__shard0.jsonl").write_text(
            json.dumps({"id": "a", "ans": ans, "tokens": 1}) + "\n"
        )
    assert L.merge(d, ["a"], L.STEM)[0]["ans"] == "zs"
    assert L.merge(d, ["a"], L.STEM_FEWSHOT)[0]["ans"] == "fs"


def test_zero_shot_rows_have_no_extra_fields(tmp_path, fake_ok):
    L.run_local([{"id": "a", "text": "t"}], [fake_ok.url], tmp_path, limit=1)
    row = json.loads((tmp_path / "shards" / f"{L.STEM}__shard0.jsonl").read_text())
    assert set(row) == {"id", "ans", "tokens"}


def test_read_shots_and_probe(tmp_path, fake_ok):
    f = tmp_path / "shots.jsonl"
    f.write_text(
        json.dumps({"id": "s1", "text": "great", "gold": {t: M.CLASSES[t][0] for t in M.TASKS}})
        + "\n"
    )
    shots = L.read_shots(f)
    assert shots.startswith(M.FEWSHOT_HEADER) and "Review: great\nJSON: " in shots
    items = [{"id": "a", "text": "short"}, {"id": "b", "text": "a much longer reviewé"}]
    res = L.probe_prompt_tokens(fake_ok.url, "m", items, shots, k=1)
    assert res["max_prompt_tokens"] == 5 and res["probed"][0]["id"] == "b"
    assert all(b["max_tokens"] == 1 for b in fake_ok.bodies)
    assert all(b["messages"][0]["content"].startswith("x\n") for b in fake_ok.bodies)


def test_push_build_fills_every_placeholder_and_compiles():
    from engine.kaggle import push_moat20b as P

    for arm in ("zeroshot", "fewshot"):
        src = P.build("some-branch", "smoke", 1.5, arm)
        assert "__" not in "".join(
            f for f in ("__BRANCH__", "__MODE__", "__BUDGET_H__", "__ARM__") if f in src
        )
        assert f'ARM = "{arm}"' in src and 'BRANCH = "some-branch"' in src
        compile(src, "kernel.py", "exec")
