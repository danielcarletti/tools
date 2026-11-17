# dc-review-pr

Fires one PostHog Code cloud Task per GitHub PR. Each Task loads the `dc-review-pr` skill from the PostHog skills store, reviews the PR against the checklist in [skill-body.md](skill-body.md), and produces a findings report. The driver polls until the report lands, then prints it, posts it to Slack, or lets the Task post it to the PR.

Pure standard library, no `pip install`. Python 3.11+.

## Setup

```bash
cp .env.example .env
```

Fill in `.env` (gitignored, auto-loaded; real env vars win):

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `POSTHOG_API_KEY` | yes | | Personal API key with `task:write` (plus `llm_skill:write` to publish the skill). |
| `POSTHOG_PROJECT_ID` | yes | | The number in your PostHog URL, `/project/<id>`. |
| `POSTHOG_HOST` | no | `https://us.posthog.com` | API host. |
| `DC_REVIEW_REPO` | no | `PostHog/posthog` | Target repo as `org/repo`. |
| `DC_REVIEW_SLACK_WEBHOOK` | for `--slack` | | Slack incoming webhook URL. |
| `DC_REVIEW_CHANNEL` | no | unfiled | Task channel (Space) UUID. |
| `DC_REVIEW_MODEL` | no | `claude-opus-5` | Model for the cloud run. Must pair with the runtime adapter. |
| `DC_REVIEW_RUNTIME_ADAPTER` | no | `claude` | Agent runtime. |
| `DC_REVIEW_REASONING_EFFORT` | no | `high` | Empty string uses the model default. |
| `DC_REVIEW_GITHUB_INTEGRATION_ID` | no | auto-resolved | Override the GitHub integration. |

The skill must exist in your project's skills store under the name `dc-review-pr` (see [Publishing the skill](#publishing-the-skill)).

## Usage

```bash
python3 review.py 62160 61795                   # poll, print reports to the terminal
python3 review.py --slack 62160 61795           # poll, post reports to Slack
python3 review.py --github 62831                # Task posts one PR comment; also prints locally
python3 review.py --github-inline 62831         # Task posts inline review comments; also prints locally
python3 review.py --detach --slack 62160 61795  # run in the background, post to Slack
```

`--detach` re-launches the driver as a background process and logs to `review-<timestamp>.log` next to the script.

`--github` and `--github-inline` are mutually exclusive. Without either, the Task runs report-only and never touches the PR. In no mode does it approve, request changes, or push.

Polling runs every 20 seconds and gives up after 45 minutes per batch.

## How completion is detected

A cloud Task's status can stay `in_progress` long after the agent finishes, so the driver ignores status. It polls the run's session log for agent messages and waits for one that carries the report structure: both `Goal achieved` and `Verdict`, or exactly `No comments` for a clean PR. Requiring both markers avoids posting a half-streamed report.

## Publishing the skill

After editing `skill-body.md`, publish it with the PostHog MCP `skill-update` tool, or:

```bash
python3 publish_skill.py
```

This needs `llm_skill:write` on the API key and updates the existing skill, so the skill has to be created once beforehand.
