"""Thin read-only Jira Cloud REST API v3 client (basic auth, API token)."""
from __future__ import annotations

import base64
from typing import Any

import requests

FIELDS = [
    "summary",
    "status",
    "assignee",
    "reporter",
    "priority",
    "labels",
    "issuetype",
    "created",
    "updated",
    "resolutiondate",
]


class JiraError(RuntimeError):
    pass


class JiraClient:
    def __init__(self, site: str, email: str, api_token: str, verify_ssl: bool = True, timeout: int = 60, base_url: str | None = None):
        if not site or not email or not api_token:
            raise JiraError("jira config requires site, email and api_token (see .env)")
        self.base = base_url.rstrip("/") if base_url else (
            f"https://{site}/rest/api/3" if "." in site else f"https://{site}.atlassian.net/rest/api/3"
        )
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode()
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Basic {token}", "Accept": "application/json"}
        )
        self._verify_ssl = verify_ssl
        self._timeout = timeout

    def search(self, jql: str, max_results: int = 200) -> list[dict[str, Any]]:
        """Paginate `POST /search/jql` with changelog expansion for status transitions."""
        issues: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            body: dict[str, Any] = {
                "jql": jql,
                "fields": FIELDS,
                "expand": "changelog",
                "maxResults": max_results,
            }
            if next_token:
                body["nextPageToken"] = next_token
            try:
                resp = self._session.post(
                    f"{self.base}/search/jql", json=body, timeout=self._timeout, verify=self._verify_ssl
                )
            except requests.RequestException as exc:
                raise JiraError(f"Jira request failed: {exc}") from exc
            if resp.status_code in (401, 403):
                raise JiraError(
                    f"Jira auth failed ({resp.status_code}): check JIRA_EMAIL/JIRA_API_TOKEN and site"
                )
            if resp.status_code == 404:
                raise JiraError(f"Jira 404: check site and jql: {jql}")
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("issues") or []
            issues.extend(batch)
            if not batch or payload.get("isLast"):
                return issues
            next_token = payload.get("nextPageToken")
            if not next_token:
                return issues


def normalize(issue: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw Atlassian issue (fields + changelog) into our shape."""
    fields = issue.get("fields") or {}

    def person(field: str) -> str | None:
        value = fields.get(field)
        if not value:
            return None
        return value.get("displayName") or value.get("emailAddress") or None

    status = fields.get("status") or {}
    transitions = []
    for history in (issue.get("changelog") or {}).get("histories") or []:
        for item in history.get("items") or []:
            if item.get("field") == "status":
                transitions.append(
                    {
                        "at": history.get("created"),
                        "from": item.get("fromString"),
                        "to": item.get("toString"),
                    }
                )

    return {
        "key": issue.get("key"),
        "summary": fields.get("summary"),
        "type": (fields.get("issuetype") or {}).get("name") or "Issue",
        "status": status.get("name") or "Unknown",
        "status_category": (status.get("statusCategory") or {}).get("key") or "Unknown",
        "assignee": person("assignee"),
        "reporter": person("reporter"),
        "priority": (fields.get("priority") or {}).get("name") or "None",
        "labels": fields.get("labels") or [],
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "resolutiondate": fields.get("resolutiondate"),
        "transitions": transitions,
    }
