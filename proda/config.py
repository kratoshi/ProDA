"""Configuration loading for ProDA.

Loads `config.yaml` (override via PRODA_CONFIG or --config), expands `${ENV_VAR}`
references, and loads a local `.env` file if present.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _expand(value: Any, env: dict[str, str], strict: bool = True) -> Any:
    if isinstance(value, dict):
        return {k: _expand(v, env, strict) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, env, strict) for v in value]
    if isinstance(value, str):

        def repl(m: re.Match) -> str:
            name = m.group(1)
            if name not in env or not env[name]:
                if strict:
                    raise ValueError(
                        f"environment variable '{name}' referenced in config is not set "
                        "(see .env.example)"
                    )
                return m.group(0)
            return env[name]

        return _ENV_REF.sub(repl, value)
    return value


class Config:
    def __init__(self, data: dict[str, Any]):
        self.raw = data
        self.jira = data.get("jira") or {}
        self.ai = data.get("ai") or {}
        self.projects = data.get("projects") or []
        self.reports_dir = data.get("reports_dir", "reports")
        self.data_dir = data.get("data_dir", "data")

    def project(self, key: str) -> dict[str, Any] | None:
        for p in self.projects:
            if p.get("key") == key:
                return p
        return None


def load_config(path: str | None = None, strict: bool = True) -> Config:
    path = path or os.environ.get("PRODA_CONFIG") or "config.yaml"
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"config not found: {p} — copy config.example.yaml to config.yaml and fill in credentials"
        )
    load_dotenv(Path(".env"))
    data = _expand(yaml.safe_load(p.read_text()) or {}, os.environ, strict=strict)
    return Config(data)
