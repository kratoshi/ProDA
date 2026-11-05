"""AI narration via an OpenAI-compatible chat completion.

Default target is deepinfra serving the DeepSeek model configured in omp. The
model only narrates deterministic facts we computed; it never changes numbers.
If no API key is set, narration is skipped and reports render data-only.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

import requests

_SCHEMA = """{
  "summary": "2-3 sentence plain summary",
  "highlights": ["up to 5 concrete facts with numbers"],
  "risks": ["up to 5 concerns"],
  "blockers": ["specific blocked items"],
  "at_risk_items": [{"key": "ABC-1", "reason": "one-line reason"}]
}"""

_SYSTEM = (
    "You are a delivery analyst writing a concise weekly progress report from Jira data. "
    "Respond ONLY with valid JSON matching this schema: " + _SCHEMA + " "
    "Rules: use only facts present in the data; never invent numbers, names, or statuses; "
    "empty sections become empty lists."
)


def build_packet(
    project: dict[str, Any],
    metrics: dict[str, Any],
    start: datetime,
    end: datetime,
    prev_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compact, model-safe view of the week's data (counts, not full issue text)."""
    prev = None
    if prev_metrics:
        p = prev_metrics["totals"]
        prev = {
            "resolved": p["resolved_in_week"],
            "created": p["created_in_week"],
            "open": p["open"],
        }
    return {
        "project": project.get("name", project["key"]),
        "week": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "totals": metrics["totals"],
        "transitions": metrics["transitions"],
        "by_status": metrics["by_status"],
        "aging": {
            "count": metrics["aging"]["count"],
            "items": [
                {"key": a["key"], "days": a["days"], "status": a["status"], "assignee": a["assignee"]}
                for a in metrics["aging"]["items"]
            ],
        },
        "avg_cycle_days": metrics["avg_cycle_days"],
        "previous_week": prev,
        "activity": {"created": metrics["created"][:30], "resolved": metrics["resolved"][:30]},
    }


def narrate(
    base_url: str,
    model: str,
    api_key: str,
    packet: dict[str, Any],
    post: Callable[..., requests.Response] = requests.post,
    timeout: int = 120,
) -> dict[str, Any] | None:
    if not api_key:
        return None

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "Weekly data (JSON):\n" + json.dumps(packet, ensure_ascii=False)},
    ]
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload: dict[str, Any] = {"model": model, "temperature": 0.3, "messages": messages}

    try:
        payload["response_format"] = {"type": "json_object"}
        resp = post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"AI request failed: {exc}") from exc
    if resp.status_code == 400 and resp.text and "response_format" in resp.text:
        # Provider rejects JSON mode — retry without it and parse the plain answer.
        payload.pop("response_format")
        resp = post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError(f"AI response is not a JSON object: {content[:200]}")
    return parsed
