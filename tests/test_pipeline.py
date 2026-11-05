"""End-to-end pipeline test: fake Jira client -> fetcher -> metrics -> snapshot -> report -> AI mock.

No network: Jira responses are constructed locally, the AI completion is mocked.
The reference week is Mon 2026-11-02 00:00 .. Mon 2026-11-09 00:00 (local).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import json as jsonmod

from proda import ai, cli, fetcher, metrics as metrics_mod, report as report_mod
from proda.config import Config
from proda.jira_client import normalize
from proda.snapshot import SnapshotStore

PROJECT = {"key": "ABC", "name": "Acme Backend", "done_statuses": ["Done", "Closed"], "aging_days": 14}


def _raw(
    key: str,
    status: str,
    category: str,
    created: str,
    updated: str,
    resolutiondate: str | None = None,
    assignee: str | None = "alice",
    labels: list[str] | None = None,
    transitions: list[tuple[str, str, str]] | None = None,
) -> dict:
    histories = []
    for at, frm, to in transitions or []:
        histories.append({"created": at, "items": [{"field": "status", "fromString": frm, "toString": to}]})
    return {
        "id": key,
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "status": {"name": status, "statusCategory": {"key": category}},
            "assignee": {"displayName": assignee} if assignee else None,
            "reporter": {"displayName": "bob"},
            "priority": {"name": "High"},
            "labels": labels or [],
            "issuetype": {"name": "Task"},
            "created": created,
            "updated": updated,
            "resolutiondate": resolutiondate,
        },
        "changelog": {"histories": histories},
    }


class FakeJira:
    def __init__(self, issues: list[dict]):
        self._issues = issues
        self.last_jql = None

    def search(self, jql: str, max_results: int = 200) -> list[dict]:
        self.last_jql = jql
        return list(self._issues)


def _fake_issues() -> list[dict]:
    return [
        _raw("ABC-1", "Done", "Done", "2026-10-20T09:00:00+00:00", "2026-11-05T15:00:00+00:00",
             resolutiondate="2026-11-05T15:00:00+00:00",
             transitions=[("2026-11-03T10:00:00+00:00", "To Do", "In Progress"),
                          ("2026-11-05T15:00:00+00:00", "In Progress", "Done")]),
        _raw("ABC-2", "In Progress", "In Progress", "2026-11-03T08:00:00+00:00", "2026-11-04T09:00:00+00:00",
             transitions=[("2026-11-03T08:30:00+00:00", "To Do", "In Progress")]),
        _raw("ABC-3", "To Do", "To Do", "2026-10-15T09:00:00+00:00", "2026-10-15T09:00:00+00:00"),
        _raw("ABC-4", "Blocked", "In Progress", "2026-10-25T09:00:00+00:00", "2026-10-25T09:00:00+00:00",
             labels=["blocked"]),
        _raw("ABC-5", "To Do", "To Do", "2026-10-28T09:00:00+00:00", "2026-10-28T09:00:00+00:00",
             assignee=None),
    ]


@pytest.fixture
def window() -> tuple[datetime, datetime]:
    start = datetime(2026, 11, 2).astimezone()
    return start, start + timedelta(days=7)


def test_week_bounds(window):
    anchor = datetime(2026, 11, 5, 14, 0).astimezone()  # Thursday
    start, end = fetcher.week_bounds(anchor)
    assert (start, end) == window
    jql = fetcher.project_jql(PROJECT, start, end)
    assert 'project = "ABC"' in jql
    assert "updated >= 2026-11-02 AND updated < 2026-11-09" in jql


def test_fetch_and_classify(window):
    issues = fetcher.fetch_project(FakeJira(_fake_issues()), PROJECT, *window)
    assert [i["key"] for i in issues] == ["ABC-1", "ABC-2", "ABC-3", "ABC-4", "ABC-5"]
    by_key = {i["key"]: i for i in issues}
    assert by_key["ABC-1"]["activity"]["resolved_in_week"] is True
    assert by_key["ABC-1"]["activity"]["created_in_week"] is False
    assert by_key["ABC-2"]["activity"]["created_in_week"] is True
    assert by_key["ABC-4"]["activity"]["blocked_now"] is True
    assert len(by_key["ABC-1"]["activity"]["week_transitions"]) == 2


def test_normalize_strips_changelog_but_keeps_transitions():
    issue = normalize(_fake_issues()[0])
    assert issue["key"] == "ABC-1"
    assert issue["status"] == "Done"
    assert issue["assignee"] == "alice"
    assert issue["transitions"][0]["to"] == "In Progress"


def test_metrics(monkeypatch, window):
    class FakeNow:
        @staticmethod
        def now():
            return datetime(2026, 11, 5, 12, 0).astimezone()

    monkeypatch.setattr(metrics_mod, "datetime", FakeNow)
    issues = fetcher.fetch_project(FakeJira(_fake_issues()), PROJECT, *window)
    m = metrics_mod.compute_metrics(issues, PROJECT, *window)
    t = m["totals"]
    assert t["open"] == 4            # ABC-1 done, others open
    assert t["created_in_week"] == 1  # ABC-2
    assert t["resolved_in_week"] == 1  # ABC-1
    assert t["blocked"] == 1         # ABC-4
    assert t["unassigned_open"] == 1  # ABC-5
    assert t["net_change"] == 0
    assert m["avg_cycle_days"] == 16.2  # ABC-1: Oct 20 09:00 -> Nov 5 15:00 = 16.25d
    assert m["by_status"] == {"Done": 1, "In Progress": 1, "To Do": 2, "Blocked": 1}
    assert [a["key"] for a in m["aging"]["items"]] == ["ABC-3"]  # untouched 21 days
    assert m["aging"]["items"][0]["days"] == 21
    assert m["transitions"]["to_done"] == 1


def test_snapshot_roundtrip_and_deltas(window, tmp_path):
    store = SnapshotStore(tmp_path / "snapshots.db")
    start, end = window
    issues = fetcher.fetch_project(FakeJira(_fake_issues()), PROJECT, *window)
    week_start = start.date().isoformat()
    store.save("ABC", week_start, issues, "2026-11-05T12:00:00+00:00")
    assert store.load("ABC", week_start) == issues
    assert SnapshotStore.previous_week_start(week_start) == "2026-10-26"

    prev = store.load("ABC", store.previous_week_start(week_start))
    assert prev is None  # first week -> no delta source


def test_build_packet_shape(window):
    start, end = window
    issues = fetcher.fetch_project(FakeJira(_fake_issues()), PROJECT, *window)
    m = metrics_mod.compute_metrics(issues, PROJECT, *window)
    packet = ai.build_packet(PROJECT, m, start, end, None)
    assert packet["project"] == "Acme Backend"
    assert packet["totals"]["resolved_in_week"] == 1
    assert packet["previous_week"] is None
    assert len(packet["activity"]["created"]) == 1


def _fake_post(ok=True):
    def post(url, json, headers, timeout):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        assert json["response_format"] == {"type": "json_object"}
        content = jsonmod.dumps({
            "summary": "Solid week.",
            "highlights": ["Resolved 1 issue."],
            "risks": [],
            "blockers": [],
            "at_risk_items": [{"key": "ABC-4", "reason": "blocked"}],
        })
        if not ok:
            return SimpleNamespace(status_code=400, text='{"error": "response_format unsupported"}',
                                   raise_for_status=lambda: None, json=lambda: {"error": 1})
        return SimpleNamespace(status_code=200, text="", raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": content}}]})

    return post


def test_narrate_mock_and_parse():
    packet = {"project": "Acme", "totals": {"resolved_in_week": 1}, "week": {}}
    out = ai.narrate("https://api.deepinfra.com/v1", "m", "key", packet, post=_fake_post())
    assert out["summary"] == "Solid week."
    assert out["at_risk_items"][0]["key"] == "ABC-4"


def test_narrate_retries_without_json_mode():
    def post(url, json, headers, timeout):
        if "response_format" in json:
            return SimpleNamespace(status_code=400, text='{"error": "response_format unsupported"}',
                                   raise_for_status=lambda: None, json=lambda: {"error": 1})
        return SimpleNamespace(status_code=200, text="", raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": '{"summary":"ok"}'}}]})

    assert ai.narrate("https://x/v1", "m", "key", {}, post=post) == {"summary": "ok"}


def test_report_markdown(monkeypatch, window, tmp_path):
    class FakeNow:
        @staticmethod
        def now():
            return datetime(2026, 11, 5, 12, 0).astimezone()

    monkeypatch.setattr(report_mod, "datetime", FakeNow)
    monkeypatch.setattr(metrics_mod, "datetime", FakeNow)
    start, end = window
    issues = fetcher.fetch_project(FakeJira(_fake_issues()), PROJECT, *window)
    m = metrics_mod.compute_metrics(issues, PROJECT, *window)
    narrative = {"summary": "Good pace.", "highlights": ["Resolved 1."], "risks": [],
                 "blockers": ["ABC-4 blocked"], "at_risk_items": [{"key": "ABC-4", "reason": "blocked"}]}
    md = report_mod.render_markdown(PROJECT, m, None, narrative, start, end)
    assert "# Acme Backend — Weekly Progress Report" in md
    assert "2026-11-02 → 2026-11-09" in md
    assert "| Resolved | 1 | — | — |" in md
    assert "| Open | 4 | — | — |" in md
    assert "ABC-4 blocked" in md
    assert "**ABC-3** (21d, To Do, alice)" in md
    assert "**ABC-1** — Summary of ABC-1 (cycle 16.2d)" in md


def test_cli_report_e2e(monkeypatch, window, tmp_path):
    """Drive the real cli._generate with a fake client; verify files + delta next run."""
    import proda.cli as cli_mod

    cfg_data = {
        "jira": {"site": "fake", "email": "e", "api_token": "t"},
        "ai": {"enabled": False},
        "projects": [PROJECT],
        "reports_dir": str(tmp_path / "reports"),
        "data_dir": str(tmp_path / "data"),
    }
    cfg = Config(cfg_data)
    store = SnapshotStore(tmp_path / "data" / "snapshots.db")

    class FakeNow:
        @staticmethod
        def now():
            return datetime(2026, 11, 5, 12, 0).astimezone()

    monkeypatch.setattr(cli_mod, "week_bounds", lambda anchor=None: window)
    monkeypatch.setattr(cli_mod, "datetime", FakeNow)
    monkeypatch.setattr(cli_mod, "JiraClient", lambda **kwargs: FakeJira(_fake_issues()))
    args = SimpleNamespace(no_ai=True, since=None, until=None, week=None)
    first = cli_mod._generate(cfg, PROJECT, store, args)
    assert first.name == "2026-11-02.md"
    assert (tmp_path / "reports" / "ABC" / "latest.md").exists()
    assert "| Resolved | 1 | — | — |" in first.read_text()
    assert "AI narration not configured" in first.read_text()
