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
                    {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 7}}
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
