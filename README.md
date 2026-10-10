# tools

Small personal tools. Each one lives in its own folder with its own README and has no shared code or dependencies with the others.

| Tool | What it does |
| --- | --- |
| [dc-review-pr](dc-review-pr/) | Fires one PostHog Code cloud Task per GitHub PR to review it against a checklist, then prints the report, posts it to Slack, or lets the Task comment on the PR. |

## Conventions

- One folder per tool, with setup and usage in that folder's `README.md`.
- Secrets go in a per-tool `.env` (gitignored), with a committed `.env.example` listing the variables.
