"""Unit tests for scripts/check_scheduled_workflows_alert.py.

Behavioural: each test writes real workflow files into a tmp dir and asserts on the guard's
verdict (exit code / failure messages), not on its internals. One test runs the guard over
this repo's real workflows so a future scheduled workflow with no alert path fails CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_scheduled_workflows_alert as guard

SCHEDULED_WITH_COMPOSITE = """\
name: Nightly thing
on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: ./probe.sh
      - name: Alert
        if: always() && !cancelled() && github.event_name == 'schedule'
        uses: ./.github/actions/schedule-failure-alert
        with:
          status: ${{ job.status == 'success' && 'success' || 'failure' }}
          title: "Nightly thing is failing"
"""

SCHEDULED_WITH_INLINE_GH = """\
name: Nightly inline
on:
  schedule:
    - cron: "0 3 * * *"
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: ./probe.sh
      - if: failure()
        run: gh issue create --title "probe failing" --body "see run"
"""

SCHEDULED_WITH_GITHUB_SCRIPT = """\
name: Nightly script
on:
  schedule:
    - cron: "0 3 * * *"
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            await github.rest.issues.create({owner, repo, title: "x"});
"""

SCHEDULED_NO_ALERT = """\
name: Silent nightly
on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: ./probe.sh
"""

NOT_SCHEDULED = """\
name: CI
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")


def test_passes_scheduled_workflow_using_composite_action(tmp_path: Path) -> None:
    _write(tmp_path, "a.yml", SCHEDULED_WITH_COMPOSITE)
    assert guard.check_directory(tmp_path) == []
    assert guard.main([str(tmp_path)]) == 0


def test_passes_scheduled_workflow_with_inline_gh_issue_create(tmp_path: Path) -> None:
    _write(tmp_path, "a.yml", SCHEDULED_WITH_INLINE_GH)
    assert guard.main([str(tmp_path)]) == 0


def test_passes_scheduled_workflow_with_github_script_issues_create(tmp_path: Path) -> None:
    _write(tmp_path, "a.yml", SCHEDULED_WITH_GITHUB_SCRIPT)
    assert guard.main([str(tmp_path)]) == 0


def test_fails_scheduled_workflow_with_no_alerting_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "silent-nightly.yml", SCHEDULED_NO_ALERT)
    assert guard.main([str(tmp_path)]) == 1
    assert "silent-nightly.yml" in capsys.readouterr().out


def test_ignores_workflows_without_a_schedule_trigger(tmp_path: Path) -> None:
    _write(tmp_path, "ci.yml", NOT_SCHEDULED)
    assert guard.main([str(tmp_path)]) == 0


def test_only_the_offending_workflow_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "good.yml", SCHEDULED_WITH_COMPOSITE)
    _write(tmp_path, "bad.yml", SCHEDULED_NO_ALERT)
    _write(tmp_path, "ci.yml", NOT_SCHEDULED)
    assert guard.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "bad.yml" in out
    assert "good.yml" not in out
    assert "ci.yml" not in out


def test_inline_flow_schedule_trigger_is_detected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "flow.yml",
        "name: Flow\non: [push, schedule]\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo hi\n",
    )
    assert guard.main([str(tmp_path)]) == 1


def test_alert_mentioned_only_in_a_comment_does_not_count(tmp_path: Path) -> None:
    commented = SCHEDULED_NO_ALERT.replace(
        "      - run: ./probe.sh\n",
        "      - run: ./probe.sh\n      # uses: ./.github/actions/schedule-failure-alert\n"
        "      # run: gh issue create --title x\n",
    )
    _write(tmp_path, "commented.yml", commented)
    assert guard.main([str(tmp_path)]) == 1


def test_reusable_workflow_delegation_is_not_recognised(tmp_path: Path) -> None:
    delegated = (
        "name: Delegated\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  alert:\n"
        "    uses: ./.github/workflows/some-reusable-alert.yml\n"
    )
    _write(tmp_path, "delegated.yml", delegated)
    assert guard.main([str(tmp_path)]) == 1


@pytest.mark.parametrize(
    "content",
    [
        "",  # empty
        "just some prose, not a workflow\n",  # no on:/jobs:
        "name: NoJobs\non:\n  schedule:\n    - cron: '0 3 * * *'\n",  # no jobs key
        "name: NoOn\njobs:\n  j:\n    runs-on: ubuntu-latest\n",  # no on key
        "name: Tabs\non:\n\tschedule:\n\t\t- cron: '0 3 * * *'\njobs:\n  j: {}\n",  # tab indent
    ],
)
def test_fails_closed_on_unparseable_workflow(tmp_path: Path, content: str) -> None:
    _write(tmp_path, "broken.yml", content)
    failures = guard.check_directory(tmp_path)
    assert len(failures) == 1
    assert "broken.yml" in failures[0]
    assert "failing closed" in failures[0]
    assert guard.main([str(tmp_path)]) == 1


def test_fails_closed_on_non_utf8_file(tmp_path: Path) -> None:
    (tmp_path / "bin.yml").write_bytes(b"\xff\xfe\x00not utf8 \x80\x81")
    assert guard.main([str(tmp_path)]) == 1


def test_fails_closed_when_directory_has_no_workflows(tmp_path: Path) -> None:
    assert guard.main([str(tmp_path)]) == 1


def test_allowlist_starts_empty() -> None:
    assert guard.ALLOWLIST == {}


def test_allowlist_entry_exempts_only_with_a_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "silent.yml", SCHEDULED_NO_ALERT)
    monkeypatch.setattr(guard, "ALLOWLIST", {"silent.yml": "manual-only in practice"})
    assert guard.main([str(tmp_path)]) == 0
    monkeypatch.setattr(guard, "ALLOWLIST", {"silent.yml": "  "})
    assert guard.main([str(tmp_path)]) == 1


def test_real_repo_workflows_all_have_an_alert_path() -> None:
    assert guard.main([]) == 0
