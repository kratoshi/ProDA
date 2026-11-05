"""ProDA command-line interface.

Commands:
  proda list                       — show configured projects
  proda report [--project KEY] [--all] [--week YYYY-MM-DD]
                                   — generate weekly progress report(s)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .ai import build_packet, narrate
from .config import Config, load_config
from .fetcher import fetch_project, week_bounds
from .jira_client import JiraClient
from .metrics import compute_metrics
from .report import render_markdown
from .snapshot import SnapshotStore


def _parse_day(value: str) -> datetime:
    d = date.fromisoformat(value)
    return datetime(d.year, d.month, d.day).astimezone()


def _generate(cfg: Config, project: dict[str, Any], store: SnapshotStore, args: argparse.Namespace) -> Path:
    key = project["key"]
    if args.since and args.until:
        start, end = _parse_day(args.since), _parse_day(args.until)
    else:
        anchor = _parse_day(args.week) if args.week else None
        start, end = week_bounds(anchor)

    client = JiraClient(
        site=cfg.jira["site"],
        email=cfg.jira["email"],
        api_token=cfg.jira["api_token"],
        verify_ssl=bool(cfg.jira.get("verify_ssl", True)),
        base_url=cfg.jira.get("base_url"),
    )
    print(f"[{key}] fetching {start.date().isoformat()} → {end.date().isoformat()}", file=sys.stderr)
    issues = fetch_project(client, project, start, end)
    metrics = compute_metrics(issues, project, start, end)

    week_start = start.date().isoformat()
    store.save(key, week_start, issues, datetime.now().astimezone().isoformat())
    prev = store.load(key, store.previous_week_start(week_start))
    prev_metrics = compute_metrics(prev, project, start - timedelta(days=7), end - timedelta(days=7)) if prev else None

    narrative = None
    ai = cfg.ai
    if ai.get("enabled", True) and not args.no_ai:
        packet = build_packet(project, metrics, start, end, prev_metrics)
        narrative = narrate(
            base_url=ai.get("base_url") or "https://api.deepinfra.com/v1",
            model=ai.get("model") or "deepseek-ai/DeepSeek-V4-Flash-0731",
            api_key=ai.get("api_key") or "",
            packet=packet,
        )

    markdown = render_markdown(project, metrics, prev_metrics, narrative, start, end)
    out_dir = Path(cfg.reports_dir) / key
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{week_start}.md"
    report_path.write_text(markdown)
    (out_dir / "latest.md").write_text(markdown)
    print(f"[{key}] wrote {report_path} ({len(issues)} issues)", file=sys.stderr)
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proda", description="Project Delivery Assistant")
    parser.add_argument("--config", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show configured projects").add_argument("--config", help="path to config.yaml")

    rep = sub.add_parser("report", help="generate weekly progress report(s)")
    rep.add_argument("--config", help="path to config.yaml")
    rep.add_argument("--project", help="Jira project key (must be in config)")
    rep.add_argument("--all", action="store_true", help="run for every configured project")
    rep.add_argument("--week", help="anchor date YYYY-MM-DD (default: today)")
    rep.add_argument("--since", help="report start date YYYY-MM-DD")
    rep.add_argument("--until", help="report end date YYYY-MM-DD (exclusive)")
    rep.add_argument("--no-ai", action="store_true", help="skip AI narration")

    args = parser.parse_args(argv)
    cfg = load_config(args.config, strict=args.command != "list")

    if args.command == "list":
        for p in cfg.projects:
            print(f"{p['key']:<12} {p.get('name', p['key'])}")
        return 0

    if args.all:
        keys = [p["key"] for p in cfg.projects]
    elif args.project:
        keys = [args.project]
    else:
        print("specify --project KEY or --all", file=sys.stderr)
        return 2
    if not keys:
        print("no projects configured in config.yaml", file=sys.stderr)
        return 2

    projects = [cfg.project(k) for k in keys]
    missing = [k for k, p in zip(keys, projects) if p is None]
    if missing:
        print(f"project(s) not in config: {', '.join(missing)}", file=sys.stderr)
        return 2

    store = SnapshotStore(Path(cfg.data_dir) / "snapshots.db")
    for project in projects:
        _generate(cfg, project, store, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
