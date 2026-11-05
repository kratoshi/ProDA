"""Local SQLite snapshot store: one row per project per week, keyed by ISO week start.

Snapshots give us last week's data so reports can show week-over-week deltas
(velocity, created vs resolved, backlog growth). Jira stays the source of truth;
this store only caches what we already read.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  project     TEXT NOT NULL,
  week_start  TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  issues_json TEXT NOT NULL,
  PRIMARY KEY (project, week_start)
);
"""


class SnapshotStore:
    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def save(self, project: str, week_start: str, issues: list[dict[str, Any]], captured_at: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots(project, week_start, captured_at, issues_json)"
            " VALUES (?, ?, ?, ?)",
            (project, week_start, captured_at, json.dumps(issues)),
        )
        self._conn.commit()

    def load(self, project: str, week_start: str) -> list[dict[str, Any]] | None:
        row = self._conn.execute(
            "SELECT issues_json FROM snapshots WHERE project = ? AND week_start = ?",
            (project, week_start),
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def previous_week_start(week_start: str) -> str:
        dt = datetime.fromisoformat(week_start) - timedelta(days=7)
        return dt.date().isoformat()
