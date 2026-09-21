"""Static wiring check for the S15d injection controls (an AST walk over app/).

A control wired on only one path is decorative. This fails if any function in app/ that calls the
extraction model path (`extract_with_llm` / `route_extraction`) does not also go through
app.core.injection_controls, unless it is on an allowlist WITH a written reason. A second
inventory pins every raw LLM transport call (`.complete(`) so a brand-new model call site cannot
appear without someone deciding whether it needs the controls.

Surface of this control (per rule 85a): direct calls, `from ... import x as y` aliases and
`module.x(...)` attribute calls to the two names below, in app/**/*.py. NOT covered: a call made
through `getattr`/string dispatch, or a new function that re-implements provider calls without
`.complete(` (the second inventory catches the common shapes: `.complete(`, `chat.completions`,
`generate_content`).
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

EXTRACTION_CALLS = {"extract_with_llm", "route_extraction"}

# (relative path, function) -> reason it may call the extraction path without the controls.
EXTRACTION_ALLOWLIST: dict[tuple[str, str], str] = {
    ("app/core/llm.py", "extract_with_llm"): (
        "the model-call layer itself: it receives an already-built prompt from its callers, "
        "which apply the controls before building it"
    ),
}

# (relative path, function) -> reason the OUTPUT step is not applied (the input step still is).
OUTPUT_EXEMPT: dict[tuple[str, str], str] = {
    ("app/core/reply/engine.py", "draft_reply"): (
        "only cons/topics are consumed from this grounding extraction; buy_again/stars_inferred "
        "never leave it, so the output check has nothing to protect"
    ),
}

# Raw LLM transport call sites: path -> why it does not need the extraction controls.
TRANSPORT_ALLOWLIST: dict[str, str] = {
    "app/core/llm.py": "extraction model-call layer (controls applied by its callers)",
    "app/core/router.py": "tiered extraction routing, called only via extract_with_llm",
    "app/core/providers/groq.py": "provider implementation (`complete` definition)",
    "app/core/providers/secondary.py": "provider implementation (`complete` definition)",
    "app/core/providers/cassette.py": "record/replay wrapper around a provider",
    "app/core/providers/base.py": "provider Protocol definition",
    "app/core/reply/engine.py": (
        "reply drafting: a different task with no extraction schema fields to steer; its "
        "extraction sub-call is wired (see EXTRACTION_ALLOWLIST/OUTPUT_EXEMPT)"
    ),
    "app/core/authenticity/engine.py": (
        "authenticity scoring: emits its own score/flags schema, not buy_again/stars_inferred/"
        "topics; it has its own eval (SECURITY.md section 10)"
    ),
    "app/core/injection_guard.py": "the Layer 2 classifier itself (Prompt Guard), not extraction",
    "app/api/ops.py": "health probe of the provider, sends no review text",
}
TRANSPORT_MARKERS = {"complete", "generate_content"}


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _aliases(tree: ast.AST) -> set[str]:
    names = set(EXTRACTION_CALLS)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name in EXTRACTION_CALLS and a.asname:
                    names.add(a.asname)
    return names


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _calls_in(fn: ast.AST) -> list[tuple[str, int]]:
    return [
        (nm, c.lineno) for c in ast.walk(fn) if isinstance(c, ast.Call) and (nm := _call_name(c))
    ]


def find_violations(source: str, rel: str) -> list[str]:
    """Return human-readable violations for one module's source (empty = wired or allowlisted)."""
    tree = ast.parse(source)
    targets = _aliases(tree)
    out: list[str] = []
    for fn in _functions(tree):
        calls = _calls_in(fn)
        names = {n for n, _ in calls}
        if not (names & targets):
            continue
        key = (rel, fn.name)
        if key in EXTRACTION_ALLOWLIST:
            continue
        if "controlled_input" not in names:
            out.append(f"{rel}:{fn.name} calls the extraction path without controlled_input()")
            continue
        if "apply_output_controls" not in names and key not in OUTPUT_EXEMPT:
            out.append(
                f"{rel}:{fn.name} has no apply_output_controls() and no OUTPUT_EXEMPT reason"
            )
        first_ctl = min(ln for n, ln in calls if n == "controlled_input")
        sanitizers = [ln for n, ln in calls if n == "sanitize"]
        if sanitizers and min(sanitizers) < first_ctl and key not in OUTPUT_EXEMPT:
            out.append(f"{rel}:{fn.name} calls sanitize() before controlled_input()")
        if (
            names & {"get_by_hash", "get_by_hash_pg"}
            and "apply_output_controls_to_cached" not in names
        ):
            out.append(f"{rel}:{fn.name} serves cached extractions without the output re-check")
    return out


def _app_sources() -> dict[str, str]:
    return {
        p.relative_to(APP.parent).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(APP.rglob("*.py"))
    }


def test_every_extraction_call_site_goes_through_the_controls() -> None:
    violations: list[str] = []
    seen = 0
    for rel, src in _app_sources().items():
        tree = ast.parse(src)
        seen += sum(1 for fn in _functions(tree) if {n for n, _ in _calls_in(fn)} & _aliases(tree))
        violations += find_violations(src, rel)
    assert violations == []
    # Not vacuous: /v2/extract, /demo/extract, v1 /extract, reply fallback, the llm layer.
    assert seen >= 5, seen


def test_allowlists_have_reasons_and_point_at_real_code() -> None:
    sources = _app_sources()
    for (rel, fn), reason in {**EXTRACTION_ALLOWLIST, **OUTPUT_EXEMPT}.items():
        assert reason.strip(), (rel, fn)
        assert rel in sources, rel
        assert any(f.name == fn for f in _functions(ast.parse(sources[rel]))), (rel, fn)
    for rel, reason in TRANSPORT_ALLOWLIST.items():
        assert reason.strip() and rel in sources, rel


def test_no_unlisted_llm_transport_call_site() -> None:
    """Any new module that talks to a model must be classified (needs controls, or a reason)."""
    unlisted: list[str] = []
    for rel, src in _app_sources().items():
        tree = ast.parse(src)
        uses = (
            any(
                isinstance(n, ast.Call) and _call_name(n) in TRANSPORT_MARKERS
                for n in ast.walk(tree)
            )
            or "chat.completions" in src
        )
        if uses and rel not in TRANSPORT_ALLOWLIST:
            unlisted.append(rel)
    assert unlisted == []


# --- the walker itself must be able to fail -------------------------------------------------
_BAD = """
from app.core.llm import extract_with_llm as run_llm

async def handler(text):
    clean, _ = sanitize(text)
    return await run_llm(clean)
"""

_GOOD = """
from app.core.llm import extract_with_llm
from app.core.injection_controls import controlled_input, apply_output_controls

async def handler(text):
    ctl = controlled_input(text)
    clean, _ = sanitize(ctl.text)
    out = await extract_with_llm(clean)
    apply_output_controls(out, ctl)
    return out
"""

_WRONG_ORDER = _GOOD.replace(
    "    ctl = controlled_input(text)\n    clean, _ = sanitize(ctl.text)\n",
    "    clean, _ = sanitize(text)\n    ctl = controlled_input(text)\n",
)

_CACHED_NO_RECHECK = _GOOD.replace("    ctl =", "    get_by_hash(text)\n    ctl =")


def test_walker_flags_an_uncontrolled_alias_call() -> None:
    v = find_violations(_BAD, "app/api/new.py")
    assert v and "without controlled_input" in v[0]


def test_walker_accepts_a_wired_call_and_flags_bad_ordering_and_cache() -> None:
    assert find_violations(_GOOD, "app/api/new.py") == []
    assert any("before controlled_input" in v for v in find_violations(_WRONG_ORDER, "x.py"))
    assert any("cached extractions" in v for v in find_violations(_CACHED_NO_RECHECK, "x.py"))
