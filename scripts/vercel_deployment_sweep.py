"""Vercel deployment sweep: report first, delete only what a human approved (S18 V1d).

Why: the team hit Functions Storage 10.93 GB / 10 GB on the Hobby plan. Old deployments are only
removed by retention (30 days default) with exceptions that keep recent ones, so this tool lists
every deployment per project, sizes it, and proposes a KEEP / DELETE plan.

Two separate invocations, per CLAUDE rule 55d (the check and the delete never share a command):

  1. dry run (default)  -> prints the report, writes reports/vercel-sweep-plan.json, deletes nothing
  2. --apply --approved-list <plan.json>  -> deletes only IDs present in BOTH the approved plan and
     the freshly recomputed DELETE set, so a stale approval cannot delete something that has since
     become current.

Auth: VERCEL_TOKEN from the environment only (never an argument, never printed, redacted in
errors). Team: --team or VERCEL_TEAM_ID. All API shapes below are from the Vercel REST docs
(vercel.com/docs/rest-api); the lambdas[].size field is NOT in the documented schema (items are
documented as having only id and output), so a missing size fails closed: that deployment is
reported UNKNOWN and is never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API = "https://api.vercel.com"
TIMEOUT_S = 30
MAX_ATTEMPTS = 5
PAGE_LIMIT = 100
KEEP_NEWEST = 2
# Serves app.samidhareviews.xyz. Hard-coded so a bug in alias or production detection can never
# nominate it for deletion.
PROTECTED_IDS = frozenset({"dpl_Bbe7PvBVd2rcSmLAQAmUc3jyk3LA"})
IN_FLIGHT = frozenset({"BUILDING", "QUEUED", "INITIALIZING"})
DEFAULT_PLAN_PATH = Path("reports/vercel-sweep-plan.json")
MB = 1024 * 1024
GB = 1024 * MB

# (method, url, headers, timeout) -> (status, body bytes). Injected in tests; no network there.
Transport = Callable[[str, str, dict[str, str], float], tuple[int, bytes]]


class SweepError(RuntimeError):
    """Any condition under which the sweep must stop rather than guess."""


def redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def urllib_transport(
    method: str, url: str, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, headers=headers)  # noqa: S310 -- https only
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Client:
    def __init__(
        self,
        token: str,
        team: str,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise SweepError("VERCEL_TOKEN is not set in the environment")
        self._token = token
        self._team = team
        self._transport = transport
        self._sleep = sleep
        self._rng = random.Random(42)  # deterministic jitter

    def request(
        self, method: str, path: str, params: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        query = dict(params or {})
        if self._team:
            query["teamId"] = self._team
        url = f"{API}{path}?{urllib.parse.urlencode(query)}"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        for attempt in range(MAX_ATTEMPTS):
            try:
                status, body = self._transport(method, url, headers, TIMEOUT_S)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SweepError(
                        redact(f"{method} {path} failed: {type(exc).__name__}: {exc}", self._token)
                    ) from None
                self._backoff(attempt)
                continue
            if status == 429 or status >= 500:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SweepError(f"{method} {path} still {status} after {MAX_ATTEMPTS} tries")
                self._backoff(attempt)
                continue
            try:
                data = json.loads(body) if body else None
            except ValueError:
                raise SweepError(f"{method} {path}: non-JSON body (status {status})") from None
            return status, data
        raise SweepError(f"{method} {path}: retries exhausted")  # pragma: no cover

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(30.0, 2.0**attempt) * (0.5 + self._rng.random()))

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        status, data = self.request("GET", path, params)
        if status != 200:
            raise SweepError(f"GET {path} returned {status}")
        return data


@dataclass
class Row:
    project: str
    id: str
    created: str
    created_ms: int
    target: str
    state: str
    branch: str
    sha: str
    size_bytes: int | None
    aliases: list[str] = field(default_factory=list)
    action: str = "KEEP"
    reason: str = ""

    @property
    def size_mb(self) -> float | None:
        return None if self.size_bytes is None else round(self.size_bytes / MB, 1)


def functions_size(detail: dict[str, Any]) -> int | None:
    """Sum of lambdas[].size, or None if the field is absent anywhere (fail closed)."""
    lambdas = detail.get("lambdas")
    if not isinstance(lambdas, list):
        return None
    total = 0
    for item in lambdas:
        size = item.get("size") if isinstance(item, dict) else None
        if not isinstance(size, int | float) or isinstance(size, bool):
            return None
        total += int(size)
    return total


def _paged(client: Client, path: str, params: dict[str, str], key: str, cursor: str) -> list[Any]:
    out: list[Any] = []
    params = dict(params, limit=str(PAGE_LIMIT))
    for _ in range(1000):
        data = client.get(path, params)
        if isinstance(data, list):  # projects endpoint may answer with a bare array
            if len(data) >= PAGE_LIMIT:
                raise SweepError(f"{path}: bare list at page limit, cannot paginate safely")
            return out + data
        if not isinstance(data, dict) or key not in data:
            raise SweepError(f"{path}: unexpected response shape")
        out += data[key]
        nxt = (data.get("pagination") or {}).get("next")
        if nxt is None:
            return out
        params[cursor] = str(nxt)
    raise SweepError(f"{path}: pagination did not terminate")


def list_projects(client: Client, only: list[str]) -> list[dict[str, str]]:
    projects = _paged(client, "/v10/projects", {}, "projects", "from")
    found = [{"id": p["id"], "name": p["name"]} for p in projects]
    if only:
        found = [p for p in found if p["name"] in only or p["id"] in only]
        missing = set(only) - {p["name"] for p in found} - {p["id"] for p in found}
        if missing:
            raise SweepError(f"requested projects not found: {sorted(missing)}")
    return found


def collect_rows(client: Client, project: dict[str, str]) -> list[Row]:
    items = _paged(client, "/v7/deployments", {"projectId": project["id"]}, "deployments", "until")
    rows: list[Row] = []
    for item in items:
        did = item["uid"]
        detail = client.get(f"/v13/deployments/{did}")
        aliases_resp = client.get(f"/v2/deployments/{did}/aliases")
        if not isinstance(aliases_resp, dict) or "aliases" not in aliases_resp:
            raise SweepError(f"alias lookup for {did}: unexpected response shape")
        meta = item.get("meta") or {}
        created_ms = int(item["created"])
        rows.append(
            Row(
                project=project["name"],
                id=did,
                created=datetime.fromtimestamp(created_ms / 1000, UTC).strftime("%Y-%m-%d %H:%M"),
                created_ms=created_ms,
                target=str(item.get("target") or "preview"),
                state=str(item.get("state") or item.get("readyState") or "UNKNOWN"),
                branch=str(meta.get("githubCommitRef", "")),
                sha=str(meta.get("githubCommitSha", ""))[:7],
                size_bytes=functions_size(detail),
                aliases=[a["alias"] for a in aliases_resp["aliases"]],
            )
        )
    return rows


def apply_policy(rows: list[Row], relax_branch_aliases: bool = False) -> None:
    """Mark each row KEEP or DELETE in place. Everything not provably safe to delete is KEPT."""
    ordered = sorted(rows, key=lambda r: r.created_ms, reverse=True)
    prod_ready = [r for r in ordered if r.target == "production" and r.state == "READY"]
    current_prod = prod_ready[0].id if prod_ready else None
    newest = {r.id for r in ordered[:KEEP_NEWEST]}
    for r in ordered:
        reason = _keep_reason(r, current_prod, newest, relax_branch_aliases)
        r.action, r.reason = ("KEEP", reason) if reason else ("DELETE", "older than newest 2")


def _keep_reason(
    r: Row, current_prod: str | None, newest: set[str], relax_branch_aliases: bool
) -> str:
    if r.id in PROTECTED_IDS:
        return "protected id (serves app.samidhareviews.xyz)"
    if r.id == current_prod:
        return "current production deployment"
    if r.id in newest:
        return f"one of the {KEEP_NEWEST} newest"
    if r.state in IN_FLIGHT:
        return f"in flight ({r.state})"
    if r.size_bytes is None:
        return "size unknown (fail closed)"
    aliases = r.aliases
    if relax_branch_aliases and r.target != "production":
        aliases = [a for a in aliases if not a.endswith(".vercel.app")]
    if aliases:
        return f"active alias: {aliases[0]}"
    return ""


def summarize(rows: list[Row]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        s = out.setdefault(r.project, {"recoverable_bytes": 0, "total_bytes": 0, "unknown": 0})
        if r.size_bytes is None:
            s["unknown"] += 1
            continue
        s["total_bytes"] += r.size_bytes
        if r.action == "DELETE":
            s["recoverable_bytes"] += r.size_bytes
    return out


def print_report(rows: list[Row], out: Callable[[str], None] = print) -> None:
    for project in sorted({r.project for r in rows}):
        out(f"\n== {project} ==")
        for r in sorted((x for x in rows if x.project == project), key=lambda x: -x.created_ms):
            size = "?" if r.size_mb is None else f"{r.size_mb:.1f}"
            out(
                f"{r.action:6} {r.id} {r.created} {r.target:10} {r.state:8} "
                f"{r.branch or '-'}@{r.sha or '-'} {size:>8} MB  {r.reason}"
            )
    out("\n== recoverable by project ==")
    totals = summarize(rows)
    for project, s in sorted(totals.items()):
        out(
            f"{project}: {s['recoverable_bytes'] / GB:.2f} GB recoverable of "
            f"{s['total_bytes'] / GB:.2f} GB measured ({int(s['unknown'])} unknown-size)"
        )
    out(f"TOTAL recoverable: {sum(s['recoverable_bytes'] for s in totals.values()) / GB:.2f} GB")


def write_plan(rows: list[Row], path: Path, team: str) -> None:
    plan = {
        "generated_at": datetime.now(UTC).isoformat(),
        "team": team,
        "delete_ids": [r.id for r in rows if r.action == "DELETE"],
        "rows": [asdict(r) for r in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def load_approved(path: Path) -> set[str]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        ids = plan["delete_ids"]
    except (OSError, ValueError, KeyError, TypeError):
        raise SweepError(f"approved list {path} is unreadable or has no delete_ids") from None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise SweepError("approved list delete_ids must be a list of strings")
    return set(ids)


def apply_deletes(
    client: Client, rows: list[Row], approved: set[str], out: Callable[[str], None] = print
) -> int:
    """Delete rows that are DELETE now AND approved. Stops on the first unexpected response."""
    fresh = {r.id: r for r in rows if r.action == "DELETE"}
    todo = [fresh[i] for i in fresh if i in approved]
    for skipped in sorted(approved - set(fresh)):
        out(f"SKIP {skipped}: no longer in the recomputed DELETE set")
    freed = 0
    for r in todo:
        if r.id in PROTECTED_IDS:  # belt and braces; policy already excludes it
            raise SweepError(f"refusing to delete protected {r.id}")
        status, data = client.request("DELETE", f"/v13/deployments/{r.id}")
        ok = status == 200 and isinstance(data, dict)
        if not ok or data.get("uid") != r.id or data.get("state") != "DELETED":
            raise SweepError(f"unexpected response deleting {r.id}: status {status}; stopping")
        freed += r.size_bytes or 0
        out(f"DELETED {r.id} ({r.project}) freed {(r.size_bytes or 0) / MB:.1f} MB")
    out(f"deleted {len(todo)} deployments, freed {freed / GB:.2f} GB")
    return len(todo)


def run(argv: list[str], env: dict[str, str], client: Client | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--team", default=env.get("VERCEL_TEAM_ID", ""))
    ap.add_argument("--project", action="append", default=[], help="name or id; repeatable")
    ap.add_argument("--plan-out", type=Path, default=DEFAULT_PLAN_PATH)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--approved-list", type=Path)
    ap.add_argument(
        "--relax-branch-aliases",
        action="store_true",
        help="non-production deployments holding only *.vercel.app aliases may be deleted",
    )
    args = ap.parse_args(argv)
    if args.apply and not args.approved_list:
        ap.error("--apply requires --approved-list")
    if args.approved_list and not args.apply:
        ap.error("--approved-list is only meaningful with --apply")
    token = env.get("VERCEL_TOKEN", "")
    try:
        client = client or Client(token, args.team)
        approved = load_approved(args.approved_list) if args.apply else set()
        rows: list[Row] = []
        for project in list_projects(client, args.project):
            rows += collect_rows(client, project)
        for project in {r.project for r in rows}:
            apply_policy([r for r in rows if r.project == project], args.relax_branch_aliases)
        print_report(rows)
        if not args.apply:
            write_plan(rows, args.plan_out, args.team)
            print(f"\nDRY RUN: plan written to {args.plan_out}. Nothing deleted.")
            return 0
        apply_deletes(client, rows, approved)
        return 0
    except SweepError as exc:
        print(f"ERROR: {redact(str(exc), token)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], dict(os.environ)))
