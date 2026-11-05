# ProDA — Project Delivery Assistant

ProDA is a read-only Python CLI that pulls a week of Jira data per project, computes
delivery metrics, and generates a markdown progress report — with an optional AI
narration layer that explains the numbers in plain language.

**Jira is the source of truth.** ProDA only reads from Jira Cloud via the REST API;
it never writes back. Local state (SQLite snapshots) exists only to compute
week-over-week deltas.

## What it does

- Fetches each configured project's issues for the current week (or a custom range),
  including status-change history (changelog).
- Computes delivery metrics from that data: open / created / resolved, net change,
  blocked, in progress, unassigned, average cycle time, aging, and status distribution.
- Stores a snapshot per project per week in SQLite so the next run can show
  **last week vs this week** deltas.
- Renders a markdown report per project (`reports/<KEY>/<week>.md` plus `latest.md`).
- Optionally asks the configured model (OpenAI-compatible, e.g. deepinfra's DeepSeek)
  to write an executive summary, highlights, and risks. The AI only narrates the
  deterministic facts we computed — it never changes the numbers.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

1. Copy the templates and fill in real values:

   ```bash
   cp .env.example .env
   cp config.example.yaml config.yaml
   ```

   `.env` (gitignored):

   ```dotenv
   JIRA_EMAIL=you@example.com
   JIRA_API_TOKEN=your-atlassian-api-token
   JIRA_SITE=your-company.atlassian.net
   DEEPINFRA_API_KEY=            # leave blank for data-only reports
   ```

   - Create a Jira API token at
     <https://id.atlassian.com/manage-profile/security/api-tokens>.
   - `JIRA_SITE` is the full hostname of your Jira Cloud site.
   - `DEEPINFRA_API_KEY` (or any OpenAI-compatible key) enables AI narration; blank
     produces data-only reports.

2. `config.yaml` — Jira connection + the projects to track:

   ```yaml
   jira:
     site: "${JIRA_SITE}"
     email: "${JIRA_EMAIL}"
     api_token: "${JIRA_API_TOKEN}"

   ai:
     enabled: true
     base_url: "https://api.deepinfra.com/v1"
     model: "deepseek-ai/DeepSeek-V4-Flash-0731"
     api_key: "${DEEPINFRA_API_KEY}"

   reports_dir: "reports"
   data_dir: "data"

   projects:
     - key: "KAN"
       name: "The Incredibles API Development"
       done_statuses: ["Done", "Closed"]
       aging_days: 14
       # jql: "component = \"api\""   # optional extra JQL AND-clause
   ```

   `config.yaml` references env vars with `${...}`; values are expanded at load time
   from your environment or `.env`.

## Usage

```bash
proda list                          # show configured projects
proda report --project KAN          # report for one project (with AI narration)
proda report --all                  # report for every configured project
proda report --all --no-ai          # data only, skip the AI layer
proda report --week 2026-11-02      # report for the week containing this date
proda report --since 2026-11-02 --until 2026-11-09   # custom date range
```

Reports land in `reports/<KEY>/<week-start>.md`, with `latest.md` pointing at the
most recent one.

## Report contents

- **Executive summary / Highlights / Risks** — AI narration (when enabled).
- **Delivery snapshot** — open, created, resolved, net change, blocked, in progress,
  unassigned, avg cycle time, each with a Δ vs the previous week.
- **Status distribution** — issue count per current status.
- **Aging work** — open issues untouched longer than `aging_days`.
- **Resolved / Created this week** — per-issue lists with cycle times.

## How it works

```
Jira Cloud REST API (read-only)
        │  POST /rest/api/3/search/jql  (+ changelog)
        ▼
proda/fetcher.py    weekly JQL → normalized issues + per-issue activity
proda/metrics.py    delivery metrics from the snapshot
proda/snapshot.py   SQLite store (one row per project per week) → Δ vs last week
proda/ai.py         narrate the metrics via an OpenAI-compatible endpoint
proda/report.py     render markdown
proda/cli.py        command-line entry point
```

| Module | Responsibility |
|---|---|
| `proda/cli.py` | argument parsing, command dispatch |
| `proda/config.py` | `config.yaml` + `${ENV}` expansion + `.env` loader |
| `proda/jira_client.py` | Jira Cloud REST v3, basic auth, paginated search with changelog |
| `proda/fetcher.py` | weekly JQL, issue normalization, per-issue activity classification |
| `proda/metrics.py` | open/created/resolved/blocked/aging/cycle-time metrics |
| `proda/snapshot.py` | SQLite week-over-week snapshot store |
| `proda/ai.py` | AI narration (JSON-mode chat completion) |
| `proda/report.py` | markdown renderer |

## Development

```bash
pytest            # run the test suite
```

## Design notes

- **Jira stays the source of truth** — no writes back to Jira, ever.
- **AI never alters data** — the model gets compact, computed metrics and is
  instructed to only narrate facts present in them; treat its prose as a draft.
- **Secrets stay local** — `.env` and `config.yaml` are gitignored; commit only the
  `.example` templates.
