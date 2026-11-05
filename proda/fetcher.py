"""Fetch a project's issue set for a week and classify per-issue activity."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .jira_client import JiraClient, normalize

DONE_CATEGORY = "Done"
DAY = timedelta(days=1)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def week_bounds(anchor: datetime | None = None) -> tuple[datetime, datetime]:
    """Monday 00:00 .. next Monday 00:00 (local time) for the week containing anchor."""
    anchor = anchor or datetime.now().astimezone()
    start = (anchor - timedelta(days=anchor.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, start + timedelta(days=7)


def project_jql(project: dict[str, Any], start: datetime, end: datetime) -> str:
    """Open issues plus anything updated inside the week window."""
    since = start.strftime("%Y-%m-%d")
    until = end.strftime("%Y-%m-%d")
    clauses = [
        f'project = "{project["key"]}"',
        f"(updated >= {since} AND updated < {until}) OR statusCategory != Done",
    ]
    extra = (project.get("jql") or "").strip()
    if extra:
        clauses.append(extra)
    return " AND ".join(f"({c})" for c in clauses)


def _status_is_blocked(name: str) -> bool:
    return "block" in (name or "").lower()


def _is_blocked(issue: dict[str, Any]) -> bool:
    if _status_is_blocked(issue.get("status") or ""):
        return True
    return any("block" in (label or "").lower() for label in issue.get("labels") or [])


def classify(
    issue: dict[str, Any],
    start: datetime,
    end: datetime,
    done_statuses: list[str],
) -> dict[str, Any]:
    created = parse_iso(issue["created"]) if issue.get("created") else None
    done_set = set(done_statuses)
    transitions = [
        t
        for t in issue.get("transitions") or []
        if t.get("at") and start <= parse_iso(t["at"]) < end
    ]
    resolved = issue.get("resolutiondate")
    resolved_dt = parse_iso(resolved) if resolved else None
    return {
        "created_in_week": bool(created and start <= created < end),
        "resolved_in_week": any(t["to"] in done_set for t in transitions)
        or bool(resolved_dt and start <= resolved_dt < end),
        "entered_active_in_week": any(t["to"] not in done_set for t in transitions),
        "entered_blocked_in_week": any(_status_is_blocked(t["to"]) for t in transitions),
        "blocked_now": _is_blocked(issue),
        "week_transitions": transitions,
    }


def fetch_project(
    client: JiraClient, project: dict[str, Any], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    done_statuses = project.get("done_statuses") or ["Done", "Closed"]
    raw = client.search(project_jql(project, start, end))
    issues = []
    for r in raw:
        issue = normalize(r)
        issue["activity"] = classify(issue, start, end, done_statuses)
        issues.append(issue)
    return issues
