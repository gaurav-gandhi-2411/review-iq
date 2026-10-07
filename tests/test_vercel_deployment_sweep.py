"""Tests for scripts/vercel_deployment_sweep.py. Fake HTTP layer only: no network."""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import vercel_deployment_sweep as mod  # noqa: E402 -- must follow the sys.path insert

TOKEN = "tok_SECRET_value_123"
PROTECTED = "dpl_Bbe7PvBVd2rcSmLAQAmUc3jyk3LA"
MB = 1024 * 1024


class FakeVercel:
    """Serves projects/deployments/aliases/details; records every call."""

    def __init__(self) -> None:
        self.projects = [{"id": "prj_1", "name": "samidha-reviews-web"}]
        self.deployments: dict[str, list[dict[str, Any]]] = {"prj_1": []}
        self.sizes: dict[str, int | None] = {}
        self.aliases: dict[str, list[str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.delete_response: tuple[int, dict[str, Any]] | None = None
        self.flaky: dict[str, int] = {}

    def add(
        self,
        did: str,
        created: int,
        target: str | None = None,
        state: str = "READY",
        size_mb: int | None = 10,
        aliases: list[str] | None = None,
        project: str = "prj_1",
    ) -> None:
        self.deployments[project].append(
            {
                "uid": did,
                "created": created,
                "target": target,
                "state": state,
                "meta": {"githubCommitRef": "main", "githubCommitSha": "abcdef123456"},
            }
        )
        self.sizes[did] = None if size_mb is None else size_mb * MB
        self.aliases[did] = aliases or []

    def __call__(self, method: str, url: str, headers: dict[str, str], timeout: float):
        parsed = urllib.parse.urlparse(url)
        path, q = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((method, path))
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        if self.flaky.get(path, 0) > 0:
            self.flaky[path] -= 1
            return 503, b"{}"
        if path == "/v10/projects":
            return 200, json.dumps(
                {"projects": self.projects, "pagination": {"next": None}}
            ).encode()
        if path == "/v7/deployments":
            allrows = sorted(self.deployments[q["projectId"]], key=lambda d: -d["created"])
            until = int(q["until"]) if "until" in q else 10**18
            page = [d for d in allrows if d["created"] < until][:2]  # tiny page: forces paging
            more = len([d for d in allrows if d["created"] < until]) > 2
            nxt = page[-1]["created"] if more else None
            return 200, json.dumps({"deployments": page, "pagination": {"next": nxt}}).encode()
        if method == "GET" and path.startswith("/v13/deployments/"):
            did = path.rsplit("/", 1)[1]
            size = self.sizes[did]
            lam = [{"id": "l", "output": "o"}] if size is None else [{"id": "l", "size": size}]
            return 200, json.dumps({"lambdas": lam}).encode()
        if path.endswith("/aliases"):
            did = path.split("/")[3]
            return 200, json.dumps({"aliases": [{"alias": a} for a in self.aliases[did]]}).encode()
        if method == "DELETE":
            if self.delete_response:
                s, body = self.delete_response
                return s, json.dumps(body).encode()
            did = path.rsplit("/", 1)[1]
            return 200, json.dumps({"state": "DELETED", "uid": did}).encode()
        return 404, b"{}"


def make(fake: FakeVercel) -> mod.Client:
    return mod.Client(TOKEN, "team_x", transport=fake, sleep=lambda _s: None)


def plan(fake: FakeVercel, **kw: Any) -> dict[str, mod.Row]:
    client = make(fake)
    rows: list[mod.Row] = []
    for p in mod.list_projects(client, []):
        rows += mod.collect_rows(client, p)
    mod.apply_policy(rows, **kw)
    return {r.id: r for r in rows}


def seeded() -> FakeVercel:
    f = FakeVercel()
    # newest first: d6 d5 | d4 (prod, older) | d3 aliased | d2 old | d1 old | PROTECTED oldest
    f.add("d6", 600)
    f.add("d5", 500)
    f.add("d4", 400, target="production")
    f.add("d3", 300, aliases=["feature-x.vercel.app"])
    f.add("d2", 200)
    f.add("d1", 100)
    f.add(PROTECTED, 50, target="production")
    return f


def test_keeps_prod_two_newest_protected_and_aliased() -> None:
    rows = plan(seeded())
    keeps = {i for i, r in rows.items() if r.action == "KEEP"}
    assert keeps == {"d6", "d5", "d4", "d3", PROTECTED}
    assert {i for i, r in rows.items() if r.action == "DELETE"} == {"d2", "d1"}
    assert rows["d4"].reason == "current production deployment"
    assert rows[PROTECTED].reason.startswith("protected id")


def test_protected_id_kept_even_when_it_looks_deletable() -> None:
    f = FakeVercel()
    for n in range(5):
        f.add(f"n{n}", 1000 + n)
    f.add(PROTECTED, 10, target=None, state="ERROR")
    assert plan(f)[PROTECTED].action == "KEEP"


def test_strict_alias_protection_and_relaxed_mode() -> None:
    f = seeded()
    f.aliases["d2"] = ["samidhareviews.xyz"]
    assert plan(f)["d2"].action == "KEEP"
    f.aliases["d2"] = []
    f.aliases["d1"] = ["proj-git-x.vercel.app"]
    assert plan(f)["d1"].action == "KEEP"
    relaxed = plan(f, relax_branch_aliases=True)
    assert relaxed["d1"].action == "DELETE"
    f.aliases["d1"] = ["samidhareviews.xyz"]  # custom domain still protects when relaxed
    assert plan(f, relax_branch_aliases=True)["d1"].action == "KEEP"


def test_missing_size_fails_closed() -> None:
    f = seeded()
    f.sizes["d2"] = None
    rows = plan(f)
    assert rows["d2"].size_bytes is None
    assert rows["d2"].action == "KEEP"
    assert "unknown" in rows["d2"].reason


def test_functions_size_variants() -> None:
    assert mod.functions_size({"lambdas": []}) == 0
    assert mod.functions_size({}) is None
    assert mod.functions_size({"lambdas": [{"size": 5}, {"id": "x"}]}) is None
    assert mod.functions_size({"lambdas": [{"size": 5}, {"size": 7}]}) == 12
    assert mod.functions_size({"lambdas": [{"size": True}]}) is None


def test_in_flight_deployment_is_kept() -> None:
    f = seeded()
    f.deployments["prj_1"][4]["state"] = "BUILDING"  # d2
    assert plan(f)["d2"].action == "KEEP"


def test_paging_collects_every_deployment() -> None:
    assert len(plan(seeded())) == 7


def test_retries_on_5xx_then_succeeds() -> None:
    f = seeded()
    f.flaky["/v10/projects"] = 2
    assert len(plan(f)) == 7
    assert [c for c in f.calls if c[1] == "/v10/projects"].__len__() == 3


def test_gives_up_after_max_attempts_without_leaking_token() -> None:
    f = seeded()
    f.flaky["/v10/projects"] = 99
    with pytest.raises(mod.SweepError) as exc:
        mod.list_projects(make(f), [])
    assert TOKEN not in str(exc.value)


def test_dry_run_writes_plan_and_never_deletes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = seeded()
    out = tmp_path / "plan.json"
    rc = mod.run(["--team", "team_x", "--plan-out", str(out)], {"VERCEL_TOKEN": TOKEN}, make(f))
    assert rc == 0
    assert not any(m == "DELETE" for m, _ in f.calls)
    saved = json.loads(out.read_text())
    assert set(saved["delete_ids"]) == {"d2", "d1"}
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err + out.read_text()
    assert "GB recoverable" in captured.out


def test_apply_deletes_only_intersection(tmp_path: Path) -> None:
    f = seeded()
    approved = tmp_path / "approved.json"
    # d2 approved; d4 (current prod) and d3 approved by a stale plan; d1 not approved.
    approved.write_text(json.dumps({"delete_ids": ["d2", "d4", "d3", PROTECTED]}))
    rc = mod.run(["--apply", "--approved-list", str(approved)], {"VERCEL_TOKEN": TOKEN}, make(f))
    assert rc == 0
    deleted = [p.rsplit("/", 1)[1] for m, p in f.calls if m == "DELETE"]
    assert deleted == ["d2"]


def test_apply_stops_on_first_unexpected_response(tmp_path: Path) -> None:
    f = seeded()
    f.delete_response = (200, {"state": "DELETED", "uid": "somebody_else"})
    approved = tmp_path / "a.json"
    approved.write_text(json.dumps({"delete_ids": ["d2", "d1"]}))
    rc = mod.run(["--apply", "--approved-list", str(approved)], {"VERCEL_TOKEN": TOKEN}, make(f))
    assert rc == 2
    assert [m for m, _ in f.calls].count("DELETE") == 1


def test_apply_stops_on_http_error(tmp_path: Path) -> None:
    f = seeded()
    f.delete_response = (403, {"error": {}})
    approved = tmp_path / "a.json"
    approved.write_text(json.dumps({"delete_ids": ["d2", "d1"]}))
    rc = mod.run(["--apply", "--approved-list", str(approved)], {"VERCEL_TOKEN": TOKEN}, make(f))
    assert rc == 2
    assert [m for m, _ in f.calls].count("DELETE") == 1


def test_apply_requires_approved_list_and_valid_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        mod.run(["--apply"], {"VERCEL_TOKEN": TOKEN}, make(seeded()))
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert (
        mod.run(["--apply", "--approved-list", str(bad)], {"VERCEL_TOKEN": TOKEN}, make(seeded()))
        == 2
    )


def test_missing_token_is_an_error_without_network(capsys: pytest.CaptureFixture[str]) -> None:
    assert mod.run([], {}) == 2
    assert "VERCEL_TOKEN" in capsys.readouterr().err


def test_redact() -> None:
    assert mod.redact(f"boom {TOKEN} boom", TOKEN) == "boom *** boom"


# --- --run: one command, one typed confirmation ---

PHRASE = "DELETE 2 DEPLOYMENTS 0.02 GB"  # seeded(): d2 + d1, 10 MB each = 20 MB = 0.0195 GB


def run_single(
    tmp_path: Path, f: FakeVercel, typed: str | None, extra: list[str] | None = None
) -> int:
    import io

    argv = [
        "--run",
        "--plan-out",
        str(tmp_path / "plan.json"),
        "--report-out",
        str(tmp_path / "report.json"),
        *(extra or []),
    ]
    stdin = io.StringIO("" if typed is None else typed + "\n")  # None = EOF with no input
    return mod.run(argv, {"VERCEL_TOKEN": TOKEN}, make(f), stdin)


def deleted_ids(f: FakeVercel) -> list[str]:
    return [p.rsplit("/", 1)[1] for m, p in f.calls if m == "DELETE"]


def test_run_exact_confirmation_deletes_only_the_plan_and_writes_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = seeded()
    assert run_single(tmp_path, f, PHRASE) == 0
    assert sorted(deleted_ids(f)) == ["d1", "d2"]
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["confirmed"] is True
    assert sorted(report["deleted"]) == ["d1", "d2"]
    assert report["planned_delete_count"] == 2
    assert report["freed_bytes"] == 20 * MB
    assert json.loads((tmp_path / "plan.json").read_text())["delete_ids"]
    out = capsys.readouterr()
    assert PHRASE in out.out
    assert TOKEN not in out.out + out.err + (tmp_path / "report.json").read_text()


@pytest.mark.parametrize(
    "typed",
    [
        "",
        "yes",
        "delete 2 deployments 0.02 gb",
        "DELETE 2 DEPLOYMENTS 0.02 GB.",
        "DELETE 3 DEPLOYMENTS 0.02 GB",
        "DELETE 2 DEPLOYMENTS 0.03 GB",
        " DELETE 2 DEPLOYMENTS 0.02 GB",
    ],
)
def test_run_confirmation_mismatch_deletes_nothing(tmp_path: Path, typed: str) -> None:
    f = seeded()
    assert run_single(tmp_path, f, typed) == 1
    assert deleted_ids(f) == []
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["confirmed"] is False
    assert report["deleted"] == []


def test_run_eof_on_stdin_deletes_nothing(tmp_path: Path) -> None:
    f = seeded()
    assert run_single(tmp_path, f, None) == 1
    assert deleted_ids(f) == []


def test_run_defaults_to_the_real_stdin_so_an_unattended_run_deletes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setattr(mod.sys, "stdin", io.StringIO(""))
    f = seeded()
    argv = [
        "--run",
        "--plan-out",
        str(tmp_path / "p.json"),
        "--report-out",
        str(tmp_path / "r.json"),
    ]
    assert mod.run(argv, {"VERCEL_TOKEN": TOKEN}, make(f)) == 1
    assert deleted_ids(f) == []


def test_run_keeps_every_protection_after_confirmation(tmp_path: Path) -> None:
    f = seeded()
    assert run_single(tmp_path, f, PHRASE) == 0
    assert PROTECTED not in deleted_ids(f)
    assert "d4" not in deleted_ids(f)  # current production
    assert "d3" not in deleted_ids(f)  # aliased


def test_run_deletes_only_the_intersection_when_state_changes_after_the_plan(
    tmp_path: Path,
) -> None:
    f = seeded()
    real_input = f.__call__
    state = {"plans": 0}

    def drifting(method: str, url: str, headers: dict[str, str], timeout: float):
        # After the first full listing (the plan), d2 gains an alias and a newer deploy appears.
        if method == "GET" and url.split("?")[0].endswith("/v10/projects"):
            state["plans"] += 1
            if state["plans"] == 2:
                f.aliases["d2"] = ["samidhareviews.xyz"]
        return real_input(method, url, headers, timeout)

    import io

    argv = [
        "--run",
        "--plan-out",
        str(tmp_path / "p.json"),
        "--report-out",
        str(tmp_path / "r.json"),
    ]
    client = mod.Client(TOKEN, "team_x", transport=drifting, sleep=lambda _s: None)
    rc = mod.run(argv, {"VERCEL_TOKEN": TOKEN}, client, io.StringIO(PHRASE + "\n"))
    assert rc == 0
    assert deleted_ids(f) == ["d1"]
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["skipped"] == ["d2"]


def test_run_stops_and_still_reports_on_unexpected_delete_response(tmp_path: Path) -> None:
    f = seeded()
    f.delete_response = (403, {"error": {}})
    assert run_single(tmp_path, f, PHRASE) == 2
    assert len(deleted_ids(f)) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["confirmed"] is True
    assert report["deleted"] == []
    assert "unexpected response" in report["error"]


def test_run_with_nothing_to_delete_asks_nothing(tmp_path: Path) -> None:
    f = FakeVercel()
    f.add("only", 100)
    assert run_single(tmp_path, f, None) == 0
    assert deleted_ids(f) == []
    assert json.loads((tmp_path / "report.json").read_text())["planned_delete_count"] == 0


def test_run_cannot_be_combined_with_apply(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        mod.run(["--run", "--apply", "--approved-list", "x.json"], {"VERCEL_TOKEN": TOKEN}, None)


def test_run_missing_token_is_an_error_before_any_prompt(tmp_path: Path) -> None:
    import io

    assert mod.run(["--run"], {}, None, io.StringIO(PHRASE + "\n")) == 2
