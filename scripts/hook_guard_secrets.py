#!/usr/bin/env python
"""Claude Code hook: keep secret VALUES out of the session transcript (S17 W1e).

Why this exists: the Supabase service-role key and the postgres password reached a transcript
because a command printed a `.env` file (S16). The disk guards (secret-scan.yml, gitleaks) cannot
see a transcript; the only places a transcript leak can be stopped are the tool call going in and
the tool output coming out. The same class happened on 2026-08-15 (a `gcloud secrets versions
access` printed a live connection string). Two layers, one script, wired in `.claude/settings.json`:

PreToolUse  (deny BEFORE the command runs -- the only layer that prevents the leak):
  * Read/Grep/Glob whose path is a secret-bearing file (.env, .env.local, *.env.*local, keys).
  * Bash/PowerShell commands that name such a file together with a content-emitting verb
    (cat/type/Get-Content/grep/Select-String/...), or that dump the environment, or that run
    `gcloud secrets versions access` without capturing the value (variable/pipe/redirect).
    `.env.example` is allowed (it holds placeholders). To list which variables a file sets
    WITHOUT their values, run `python scripts/env_keys.py <file>`.
PostToolUse (detective -- cannot unsend, but forces the session to stop and report):
  * Tool output containing a service-role JWT, a connection string with an embedded password, or
    a known API-key shape is blocked with an instruction to treat the secret as compromised.

Fail-closed (rule 98a): an unparseable payload or an internal error denies the call rather than
letting it through. Limits, stated plainly: it matches command TEXT, so `python -c "print(open(
'.env').read())"` is caught only because the path appears; a script that reads the file via a
variable built at runtime is not. It is a tripwire for the accidental print, not a sandbox.
"""

from __future__ import annotations

import base64
import json
import re
import sys

# A path is "secret-bearing" when its final component matches. `.env.example` is excluded.
_SECRET_FILE = re.compile(
    r"""(?ix)
    (?:^|[\\/\s'"=:])                       # start of a path component or token
    (?:
        \.env(?:\.(?!example\b)[\w.-]+)?    # .env, .env.local, .env.production (not .env.example)
      | [\w.-]*\.env\.[\w.-]*local          # benchmark/.../.env.benchmark.local
      | [\w.-]*(?:service[-_]?account|credentials?)[\w.-]*\.json
      | [\w.-]+\.(?:pem|p12|pfx|key)
      | id_(?:rsa|ed25519|ecdsa)
      | \.netrc
    )
    (?=$|[\s'"|;&)<>])                      # end of the token
    """
)

# Verbs that put file CONTENT on stdout. Presence of one + a secret path in one command = deny.
_CONTENT_VERB = re.compile(
    r"""(?ix)\b(?:cat|type|more|less|head|tail|bat|gc|get-content|select-string|sls|grep|egrep|
    rg|findstr|awk|sed|cut|od|xxd|strings|out-string|read_text|readlines|open|import-csv|
    convertfrom-stringdata|dotenv_values|load_dotenv|source|cp|copy|copy-item)\b|(?:^|\s)\.\s+\S"""
)

# Dumping the whole environment prints every secret exported to the shell.
_ENV_DUMP = re.compile(
    r"""(?ix)(?:^|[;&|]\s*|\()\s*(?:printenv|env|export\s+-p|declare\s+-x|set)\s*(?:$|[;&|)])
    |\b(?:get-childitem|gci|ls|dir)\s+env:(?!\w)|\bget-item\s+env:(?!\w)
    |\[(?:system\.)?environment\]::getenvironmentvariables"""
)

# Echoing a variable whose NAME says it is a secret.
_ECHO_SECRET_VAR = re.compile(
    r"""(?ix)\b(?:echo|write-host|write-output|print|printf)\b[^|;&\n]*
    \$\{?(?:env:)?\w*(?:secret|token|password|passwd|api_?key|service_?role|database_url|
    direct_url|private_?key)\w*"""
)

_GCLOUD_SECRET_READ = re.compile(r"(?i)\bgcloud\s+secrets\s+versions\s+access\b")
# A secret read is "captured" if its value is assigned, piped on, or redirected to a file.
_CAPTURED = re.compile(
    r"""(?x)(?:\$\w+\s*=|\$env:\w+\s*=|\w+=\$\(|\w+="?\$\(|\||>\s*\S|--out-file)"""
)

# Secret VALUE shapes for the output scan. Placeholders (all-caps PASSWORD, <...>) are skipped.
_PG_URL = re.compile(r"postgres(?:ql)?://[^\s:@/]+:([^\s@/]{4,})@[^\s/]+")
_PLACEHOLDER_PW = re.compile(r"^(?:[A-Z_]+|\*+|<[^>]*>|\$\{?\w+\}?|xxx+|password|changeme)$")
_KEY_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Groq API key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("Supabase secret key", re.compile(r"\bsb_secret_[A-Za-z0-9_-]{16,}")),
    ("Resend API key", re.compile(r"\bre_[A-Za-z0-9]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.(eyJ[A-Za-z0-9_-]{8,})\.[A-Za-z0-9_-]{8,}")


def _deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def _paths_in(tool_input: dict[str, object]) -> list[str]:
    return [
        str(tool_input[k]) for k in ("file_path", "path", "pattern", "glob") if tool_input.get(k)
    ]


def pre_tool_violation(tool_name: str, tool_input: dict[str, object]) -> str | None:
    """Reason to deny this call, or None. Pure, so it is unit-testable."""
    if tool_name in {"Read", "Grep", "Glob", "NotebookEdit", "Edit", "Write"}:
        for p in _paths_in(tool_input):
            if _SECRET_FILE.search(" " + p):
                return (
                    f"`{p}` looks like a secret-bearing file. Its contents must never enter the "
                    "transcript. To see which variables it sets (names only, never values) run "
                    "`python scripts/env_keys.py <file>`."
                )
        return None
    if tool_name not in {"Bash", "PowerShell"}:
        return None
    cmd = str(tool_input.get("command", ""))
    if _SECRET_FILE.search(" " + cmd) and _CONTENT_VERB.search(cmd):
        return (
            "This command names a secret-bearing file (.env / key / credentials) and a verb that "
            "prints file content. Do not print it. To list which variables a file sets (names "
            "only) run `python scripts/env_keys.py <file>`; to use a value, load it inside the "
            "program that needs it (settings/pydantic) and print nothing."
        )
    if _ENV_DUMP.search(cmd):
        return (
            "Dumping the whole environment prints every exported secret. Read one non-secret "
            "variable by name instead."
        )
    if _ECHO_SECRET_VAR.search(cmd):
        return "Echoing a variable whose name marks it as a secret would print its value."
    if _GCLOUD_SECRET_READ.search(cmd) and not _CAPTURED.search(cmd):
        return (
            "`gcloud secrets versions access` writes the secret to stdout. Capture it "
            "($v = (gcloud ... ) -join '') or pipe it into the consumer so it is never printed."
        )
    return None


def _jwt_role(payload_b64: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        role = json.loads(raw).get("role")
        return str(role) if role is not None else None
    except (ValueError, json.JSONDecodeError):
        return None


def post_tool_findings(text: str) -> list[str]:
    """Kinds of secret found in tool output (never the values). Pure, unit-testable."""
    found: list[str] = []
    for m in _JWT.finditer(text):
        if _jwt_role(m.group(1)) == "service_role":
            found.append("Supabase service-role JWT")
            break
    for m in _PG_URL.finditer(text):
        pw = m.group(1)
        if not _PLACEHOLDER_PW.match(pw) and "localhost" not in m.group(0):
            found.append("database connection string with an embedded password")
            break
    for label, pat in _KEY_SHAPES:
        if pat.search(text):
            found.append(label)
    return found


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "")
        if event == "PostToolUse":
            text = json.dumps(payload.get("tool_response", ""), ensure_ascii=False)
            kinds = post_tool_findings(text)
            if kinds:
                reason = (
                    f"SECRET EXPOSED in this tool output ({', '.join(kinds)}). It is now in the "
                    "transcript: treat it as compromised. Stop, do not repeat or paste the value, "
                    "tell GG, and follow ops/runbooks/secret-rotation.md for the rotation order."
                )
                print(
                    json.dumps(
                        {
                            "decision": "block",
                            "reason": reason,
                            "hookSpecificOutput": {
                                "hookEventName": "PostToolUse",
                                "additionalContext": reason,
                            },
                        }
                    )
                )
            sys.exit(0)
        violation = pre_tool_violation(
            str(payload.get("tool_name", "")), dict(payload.get("tool_input", {}))
        )
        if violation:
            _deny(violation)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 -- fail closed on anything unexpected (rule 98a)
        _deny(f"hook_guard_secrets could not evaluate this call ({type(exc).__name__}); denied.")


if __name__ == "__main__":
    main()
