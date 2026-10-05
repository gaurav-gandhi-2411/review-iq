from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import env_keys
from scripts import hook_guard_secrets as g

ROOT = Path(__file__).resolve().parents[2]


def _jwt(role: str) -> str:
    def seg(obj: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'HS256'})}.{seg({'role': role, 'ref': 'abcdefghij'})}.{'s' * 20}"


@pytest.mark.parametrize(
    "cmd",
    [
        "cat .env",
        "Get-Content .env",
        "Get-Content C:\\x\\review-iq\\.env.local | Select-Object -First 3",
        "type web\\.env.local",
        "grep SUPABASE .env",
        "Select-String -Path .env -Pattern KEY",
        "python -c \"print(open('.env').read())\"",
        "head -5 benchmark/vernacular_v2/.env.benchmark.local",
        "printenv",
        "Get-ChildItem Env:",
        "env",
        "echo $SUPABASE_SERVICE_ROLE_KEY",
        "Write-Host $env:GROQ_API_KEY",
        "gcloud secrets versions access latest --secret=supabase-service-role-key",
    ],
)
def test_pre_denies_secret_printing(cmd: str) -> None:
    assert g.pre_tool_violation("Bash", {"command": cmd}) is not None


@pytest.mark.parametrize(
    "cmd",
    [
        "cat .env.example",
        "git check-ignore -v .env",
        "Test-Path .env",
        "python scripts/env_keys.py .env",
        "git status --short",
        "$v = (gcloud secrets versions access latest --secret=x) -join ''",
        "gcloud secrets versions access latest --secret=x | gh secret set Y",
        "gcloud secrets versions access latest --secret=x > $env:TEMP/v.txt",
        "env_var=1 uv run pytest",
        "uv run pytest tests/unit -q",
    ],
)
def test_pre_allows_normal_work(cmd: str) -> None:
    assert g.pre_tool_violation("Bash", {"command": cmd}) is None


def test_pre_denies_read_and_grep_of_env_but_not_example() -> None:
    assert g.pre_tool_violation("Read", {"file_path": "C:\\r\\.env"}) is not None
    assert g.pre_tool_violation("Read", {"file_path": "/r/web/.env.local"}) is not None
    assert g.pre_tool_violation("Grep", {"path": "/r/.env", "pattern": "KEY"}) is not None
    assert g.pre_tool_violation("Read", {"file_path": "/r/.env.example"}) is None
    assert g.pre_tool_violation("Read", {"file_path": "/r/app/main.py"}) is None


def test_post_flags_service_role_jwt_but_not_anon() -> None:
    assert g.post_tool_findings(f"key={_jwt('service_role')}") == ["Supabase service-role JWT"]
    assert g.post_tool_findings(f"key={_jwt('anon')}") == []


def test_post_flags_pg_url_password_and_key_shapes_but_not_placeholders() -> None:
    real = "postgresql://" + "user:" + "Zx9qLmN2pQ7r" + "@db.example.supabase.co:5432/postgres"
    assert g.post_tool_findings(real) == ["database connection string with an embedded password"]
    assert g.post_tool_findings("postgresql://user:PASSWORD@host:5432/db") == []
    assert g.post_tool_findings("postgres://postgres:postgres@localhost:5432/postgres") == []
    assert g.post_tool_findings("gsk_" + "a" * 30) == ["Groq API key"]
    assert g.post_tool_findings("plain log line, no secrets") == []


def test_main_end_to_end_denies_and_fails_closed() -> None:
    def run(stdin: str) -> str:
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "hook_guard_secrets.py")],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        ).stdout

    out = json.loads(
        run(
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "cat .env"},
                }
            )
        )
    )
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        run(
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git status"},
                }
            )
        )
        == ""
    )
    # unparseable payload -> deny, not silent allow (rule 98a)
    assert json.loads(run("not json"))["hookSpecificOutput"]["permissionDecision"] == "deny"
    post = json.loads(
        run(
            json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_response": {"stdout": "k=" + _jwt("service_role")},
                }
            )
        )
    )
    assert post["decision"] == "block"
    assert _jwt("service_role") not in json.dumps(post)  # the hook never echoes the secret


def test_env_keys_never_returns_values() -> None:
    text = "# c\nA=secretvalue\nexport B=\nC = 'x'\nnot a line\n"
    assert env_keys.keys(text) == [("A", True), ("B", False), ("C", True)]
