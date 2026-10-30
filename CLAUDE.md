# review-iq: project rules for Claude Code sessions

Global defaults live in `~/.claude/CLAUDE.md`; this file adds only what is specific to this repo.

- **Never `cd` into a subdirectory in a shell tool call.** Use `npm --prefix web ...`, `git -C <path> ...`, absolute paths, or a
  subshell `(cd web && ...)`. A plain `cd` persists into later tool calls. If a project hook runs a script by a relative path, it then
  cannot find the script, and every tool call is refused. Incident S33 (2026-10-30): `cd web && npm ci` locked the session out until
  GG ran `! cd` at the prompt. The secrets hook now uses an absolute path (#631); the rule stands for any other hook.
