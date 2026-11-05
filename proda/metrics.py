"""Derive delivery metrics from a fetched project snapshot."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .fetcher import _status_is_blocked, parse_iso


def _done_set(project: dict[str, Any]) -> set[str]:
    return set(project.get("done_statuses") or ["Done", "Closed"])


def _mini(issue: dict[str, Any], max_summary: int = 120) -> dict[str, Any]:
    summary = issue.get("summary") or ""
    return {
        "key": issue["key"],
        "summary": summary[:max_summary],
        "status": issue["status"],
        "priority": issue["priority"],
        "assignee": issue.get("assignee"),
    }


def compute_metrics(
    issues: list[dict[str, Any]],
    project: dict[str, Any],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    aging_days = int(project.get("aging_days", 14))
    done_set = _done_set(project)

    open_issues = [i for i in issues if i["status_category"] != "Done"]
    created = [i for i in issues if i["activity"]["created_in_week"]]
    resolved = [i for i in issues if i["activity"]["resolved_in_week"]]
    in_progress = [i for i in issues if i["status_category"] == "In Progress"]
    blocked = [i for i in issues if i["activity"]["blocked_now"]]
    unassigned = [i for i in open_issues if not i.get("assignee")]

    by_status: dict[str, int] = {}
    for i in issues:
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1

    now = datetime.now().astimezone()
    aging = []
    for i in open_issues:
        updated = i.get("updated")
        if not updated:
            continue
        days = (now - parse_iso(updated)).days
        if days > aging_days:
            aging.append(
                {
                    "key": i["key"],
                    "days": days,
                    "status": i["status"],
                    "assignee": i.get("assignee"),
                    "summary": (i.get("summary") or "")[:120],
                }
            )
    aging.sort(key=lambda a: a["days"], reverse=True)

    def _done_at(issue: dict[str, Any]) -> datetime | None:
        for t in issue["activity"]["week_transitions"]:
            if t["to"] in done_set and t.get("at"):
                return parse_iso(t["at"])
        return parse_iso(issue["resolutiondate"]) if issue.get("resolutiondate") else None

    cycle_days: list[float] = []
    resolved_mini = []
    for i in resolved:
        done_dt = _done_at(i)
        cycle = None
        if done_dt and i.get("created"):
            cycle = round((done_dt - parse_iso(i["created"])).total_seconds() / 86400.0, 1)
            cycle_days.append(cycle)
        resolved_mini.append({**_mini(i), "cycle_days": cycle})

    return {
        "totals": {
            "open": len(open_issues),
            "in_progress": len(in_progress),
            "blocked": len(blocked),
            "created_in_week": len(created),
            "resolved_in_week": len(resolved),
            "unassigned_open": len(unassigned),
            "net_change": len(created) - len(resolved),
        },
        "by_status": by_status,
        "aging": {"threshold_days": aging_days, "count": len(aging), "items": aging[:10]},
        "avg_cycle_days": round(sum(cycle_days) / len(cycle_days), 1) if cycle_days else None,
        "transitions": {
            "to_done": sum(1 for i in issues if any(t["to"] in done_set for t in i["activity"]["week_transitions"])),
            "to_active": sum(1 for i in issues if any(t["to"] not in done_set for t in i["activity"]["week_transitions"])),
            "to_blocked": sum(1 for i in issues if any(_status_is_blocked(t["to"]) for t in i["activity"]["week_transitions"])),
        },
        "created": [_mini(i) for i in created],
        "resolved": resolved_mini,
    }
