"""Render a weekly progress report as markdown."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _delta(cur: int, prev: int | None) -> str:
    if prev is None:
        return "—"
    diff = cur - prev
    return f"{'+' if diff > 0 else ''}{diff}"


def render_markdown(
    project: dict[str, Any],
    metrics: dict[str, Any],
    prev_metrics: dict[str, Any] | None,
    narrative: dict[str, Any] | None,
    start: datetime,
    end: datetime,
) -> str:
    name = project.get("name", project["key"])
    t = metrics["totals"]
    prev = prev_metrics["totals"] if prev_metrics else None
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"# {name} — Weekly Progress Report",
        f"**Week:** {start.date().isoformat()} → {end.date().isoformat()}  ·  "
        f"**Generated:** {generated}  ·  **Source:** Jira (source of truth)",
    ]

    if narrative:
        lines += ["", "## Executive summary", "", narrative.get("summary") or "_no summary_"]
        if narrative.get("highlights"):
            lines += ["", "## Highlights", ""] + [f"- {h}" for h in narrative["highlights"]]
        if narrative.get("risks"):
            lines += ["", "## Risks", ""] + [f"- {r}" for r in narrative["risks"]]
        if narrative.get("blockers"):
            lines += ["", "## Blockers", ""] + [f"- {b}" for b in narrative["blockers"]]
        if narrative.get("at_risk_items"):
            lines += ["", "## At-risk items", ""] + [
                f"- **{a['key']}** — {a.get('reason', '')}" for a in narrative["at_risk_items"]
            ]
    else:
        lines += ["", "> AI narration not configured — data-only report. Set DEEPINFRA_API_KEY in .env."]

    lines += [
        "",
        "## Delivery snapshot",
        "",
        "| Metric | This week | Last week | Δ |",
        "|---|---|---|---|",
        f"| Open | {t['open']} | {prev['open'] if prev else '—'} | {_delta(t['open'], prev['open']) if prev else '—'} |",
        f"| Created | {t['created_in_week']} | {prev['created_in_week'] if prev else '—'} | {_delta(t['created_in_week'], prev['created_in_week']) if prev else '—'} |",
        f"| Resolved | {t['resolved_in_week']} | {prev['resolved_in_week'] if prev else '—'} | {_delta(t['resolved_in_week'], prev['resolved_in_week']) if prev else '—'} |",
        f"| Net change | {t['net_change']:+d} | — | — |",
        f"| Blocked | {t['blocked']} | — | — |",
        f"| In progress | {t['in_progress']} | — | — |",
        f"| Unassigned (open) | {t['unassigned_open']} | — | — |",
        f"| Avg cycle time (days) | {metrics['avg_cycle_days'] if metrics['avg_cycle_days'] is not None else '—'} | — | — |",
    ]

    lines += [
        "",
        "## Status distribution",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status, count in sorted(metrics["by_status"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {status} | {count} |")

    aging = metrics["aging"]
    lines += [
        "",
        f"## Aging work (open, untouched > {aging['threshold_days']}d)",
        "",
    ]
    if aging["items"]:
        lines += [
            f"- **{a['key']}** ({a['days']}d, {a['status']}, {a['assignee'] or 'unassigned'}) — {a['summary']}"
            for a in aging["items"]
        ]
    else:
        lines.append("_None._")

    lines += ["", "## Resolved this week", ""]
    if metrics["resolved"]:
        lines += [
            f"- **{i['key']}** — {i['summary']}" + (f" (cycle {i['cycle_days']}d)" if i["cycle_days"] else "")
            for i in metrics["resolved"]
        ]
    else:
        lines.append("_None._")

    lines += ["", "## Created this week", ""]
    if metrics["created"]:
        lines += [f"- **{i['key']}** — {i['summary']} ({i['status']})" for i in metrics["created"]]
    else:
        lines.append("_None._")

    return "\n".join(lines) + "\n"
