#!/usr/bin/env python3
"""DC PR review driver.

Fires one PostHog Code cloud Task per PR. Each Task runs the published
`dc-review-pr` skill against the PR and produces a findings report as one of
its messages.

Delivery modes (local — where the driver prints the report once the Task finishes):
  (default)  poll until the report lands, then print it to the terminal.
  --slack    poll until the report lands, then post it to a Slack channel.
  --detach   daemonize: re-launches in the background, frees the terminal.
             Combine with --slack to get reports without watching the output.
             Logs go to review-<timestamp>.log next to this script.

GitHub delivery (the cloud Task posts to the PR; the driver still prints locally):
  --github         post the full report as a single PR comment.
  --github-inline  post each finding as an inline review comment on its line.

Completion detection:
  A cloud Task's status can stay `in_progress` for hours after the agent
  finishes, so we ignore status. Instead we poll the run's session log
  (which flushes to S3 incrementally) and wait for the agent's report message
  to appear. The report — not any earlier "let me look at the PR" chatter — is
  what signals completion (see select_report).

Report extraction:
  The session_logs endpoint applies its event_types filter BEFORE pagination,
  so ?event_types=agent_message returns every agent message of the run (a
  handful) in ascending time order. Early messages are preamble chatter; the
  report is the message carrying the skill's structure (Goal achieved + Verdict,
  or exactly "No comments"). We require BOTH the opening and closing markers so
  we never post a half-streamed report, and we keep the "## Review: PR #N …"
  title line, dropping only the chatty lead-in before it.

Pure standard library — no pip install. Python 3.11+.

Config (env vars; a .env file next to this script is auto-loaded):
  POSTHOG_API_KEY            required. Personal API key with task:write scope.
  POSTHOG_PROJECT_ID         required. The number in your PostHog URL /project/<id>.
  POSTHOG_HOST               optional. Default https://us.posthog.com
  DC_REVIEW_REPO             optional. Target repo org/repo. Default PostHog/posthog.
  DC_REVIEW_MODEL            optional. Claude model id for the cloud run.
                             Default claude-opus-5. Must pair with DC_REVIEW_RUNTIME_ADAPTER.
  DC_REVIEW_RUNTIME_ADAPTER  optional. Agent runtime. Default claude.
  DC_REVIEW_REASONING_EFFORT optional. Reasoning effort for the model (e.g. high).
                             Default high. Omit by setting to empty string.
  DC_REVIEW_CHANNEL          optional. Task channel (Space) UUID. Unset = leave unfiled.
  DC_REVIEW_SLACK_WEBHOOK    required for --slack. Slack incoming webhook URL.
  DC_REVIEW_GITHUB_INTEGRATION_ID  optional. Override the auto-resolved GitHub integration.

Usage:
  python3 review.py 62160 61795                         # poll + print to terminal
  python3 review.py --slack 62160 61795                 # poll + post to Slack
  python3 review.py --github 62831                      # PR comment + print locally
  python3 review.py --github-inline 62831               # inline comments + print locally
  python3 review.py --detach --slack 62160 61795        # background + post to Slack
  python3 review.py --detach 62160 61795                # background + print to log

After editing skill-body.md, publish with PostHog MCP `skill-update`, or:  python3 publish_skill.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

SKILL_NAME = "dc-review-pr"
DeliveryMode = Literal["report-only", "github", "github-inline"]
# Pin explicitly: omitting model on /run/ falls back to PostHog Code's harness
# default (currently claude-opus-4-8), which is why reviews were landing on 4.8.
DEFAULT_RUNTIME_ADAPTER = "claude"
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_REASONING_EFFORT = "high"
# Personal #me Space in PostHog Desktop — files reviews into that channel feed.
POLL_INTERVAL_SECONDS = 20
POLL_TIMEOUT_SECONDS = 45 * 60
# event_types filter is applied before pagination, so this caps agent messages,
# not raw events — a few hundred is far more than any single run produces.
SESSION_LOG_LIMIT = 200
SLACK_BLOCK_LIMIT = 2900  # Slack caps a section block at 3000 chars; leave headroom.
# A message is the finished report only if it has the opening marker AND the
# closing Verdict section (so a half-streamed flush never posts early), or it is
# exactly the clean-PR sentinel.
REPORT_OPEN_MARKER = "Goal achieved"
REPORT_CLOSE_MARKER = "Verdict"
CLEAN_PR_SENTINEL = "No comments"
# Where the report proper begins — used to trim the chatty lead-in while keeping
# the "## Review: PR #N — <title>" header. First match wins by position.
REPORT_START_MARKERS = ("## Review", "# Review", "**Review", "Review: PR", "**Goal achieved", "Goal achieved")


def load_dotenv() -> None:
    """Minimal .env loader. Real env vars win (we only setdefault)."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


class Config:
    def __init__(self, *, need_slack: bool) -> None:
        self.api_key = self._require("POSTHOG_API_KEY")
        self.project_id = self._require("POSTHOG_PROJECT_ID")
        self.host = os.environ.get("POSTHOG_HOST", "https://us.posthog.com").rstrip("/")
        self.repo = os.environ.get("DC_REVIEW_REPO", "PostHog/posthog")
        self.runtime_adapter = os.environ.get("DC_REVIEW_RUNTIME_ADAPTER", DEFAULT_RUNTIME_ADAPTER)
        self.model = os.environ.get("DC_REVIEW_MODEL", DEFAULT_MODEL)
        # Empty string disables the effort pin (model default). Unset → DEFAULT_REASONING_EFFORT.
        if "DC_REVIEW_REASONING_EFFORT" in os.environ:
            self.reasoning_effort = os.environ["DC_REVIEW_REASONING_EFFORT"].strip() or None
        else:
            self.reasoning_effort = DEFAULT_REASONING_EFFORT
        self.channel = os.environ.get("DC_REVIEW_CHANNEL", "").strip() or None
        self.github_integration_id = os.environ.get("DC_REVIEW_GITHUB_INTEGRATION_ID")
        self.slack_webhook = os.environ.get("DC_REVIEW_SLACK_WEBHOOK")
        if need_slack and not self.slack_webhook:
            sys.exit("error: --slack requires DC_REVIEW_SLACK_WEBHOOK to be set (see .env).")
        if bool(self.runtime_adapter) ^ bool(self.model):
            sys.exit(
                "error: DC_REVIEW_RUNTIME_ADAPTER and DC_REVIEW_MODEL must both be set "
                "(the Tasks /run/ API requires them as a pair)."
            )

    @staticmethod
    def _require(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            sys.exit(f"error: {name} is required (set it in .env or the environment).")
        return value

    @property
    def api_base(self) -> str:
        return f"{self.host}/api/projects/{self.project_id}"

    def task_url(self, task_id: str) -> str:
        return f"{self.host}/project/{self.project_id}/tasks/{task_id}"


def api_request(cfg: Config, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{cfg.api_base}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {cfg.api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"error: {method} {path} -> HTTP {exc.code}\n{detail}") from exc


def fetch_agent_messages(cfg: Config, task_id: str, run_id: str) -> list[str]:
    """Return all agent message texts for a run, in the order the API gives them."""
    url = (
        f"{cfg.api_base}/tasks/{task_id}/runs/{run_id}/session_logs/"
        f"?event_types=agent_message&limit={SESSION_LOG_LIMIT}"
    )
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {cfg.api_key}")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"  ! could not read session log: HTTP {exc.code}", file=sys.stderr)
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    entries: list = data if isinstance(data, list) else data.get("results", [])
    return [text for entry in entries if (text := _extract_text(entry))]


def _extract_text(entry: dict) -> str | None:
    update = entry.get("notification", {}).get("params", {}).get("update", {})
    content = update.get("content")
    if isinstance(content, dict) and content.get("type") == "text":
        text = content.get("text")
        if isinstance(text, str) and text.strip():
            return text
    message = update.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return None


def _is_report(message: str) -> bool:
    """True only for a complete findings report (or the clean-PR sentinel)."""
    if message.strip() == CLEAN_PR_SENTINEL:
        return True
    return REPORT_OPEN_MARKER in message and REPORT_CLOSE_MARKER in message


def _strip_preamble(report: str) -> str:
    """Drop the chatty lead-in before the report, keeping the Review/title line."""
    positions = [pos for marker in REPORT_START_MARKERS if (pos := report.find(marker)) >= 0]
    if not positions:
        return report
    cut = min(positions)
    return report[cut:].strip() if cut > 0 else report


def select_report(messages: list[str]) -> str:
    """Return the finished report, or '' if it hasn't been produced yet.

    Crucially this is also the completion signal for the poll loop: it returns
    '' (keep waiting) until a message satisfies _is_report, so preamble chatter
    is never mistaken for the review. Among report messages (normally one) the
    longest wins.
    """
    candidates = [m for m in messages if _is_report(m)]
    if not candidates:
        return ""
    return _strip_preamble(max(candidates, key=len).strip())


def build_prompt(cfg: Config, pr: int, *, delivery: DeliveryMode) -> str:
    repo = cfg.repo
    delivery_instructions = {
        "report-only": "Delivery mode: report-only. Do not post to GitHub, comment, approve, or push.",
        "github": (
            "Delivery mode: github. After building the report, post it as a single PR comment with "
            f"`gh pr comment {pr} --repo {repo} --body-file -` (pipe the report markdown on stdin). "
            "If there are no actionable findings, post the literal text `No comments`. "
            "Do not approve, request changes, or push."
        ),
        "github-inline": (
            "Delivery mode: github-inline. After building the report, post each finding as an inline "
            "review comment on its cited line. "
            f"Get the head commit with `gh pr view {pr} --repo {repo} --json headRefOid`. "
            f"Submit one review via `gh api repos/{repo}/pulls/{pr}/reviews` with "
            "`commit_id=<headRefOid>`, `event=COMMENT`, a short Verdict summary in `body`, and a "
            "`comments` JSON array — one entry per finding (`path`, `line`, `side=RIGHT`, `body`). "
            "Confirm each line exists on the PR head; unanchorable findings go in the review body "
            "instead. If there are no actionable findings, skip the review API call. "
            "Do not approve, request changes, or push."
        ),
    }[delivery]
    return (
        f"Load the `{SKILL_NAME}` skill from the PostHog skills store "
        f"(call the skill-get MCP tool with skill_name=\"{SKILL_NAME}\"), then "
        f"follow it exactly to review PR #{pr} in the {cfg.repo} repository. "
        f"{delivery_instructions} "
        f"Output the findings report as your final message."
    )


def create_and_run(cfg: Config, pr: int, *, delivery: DeliveryMode) -> str:
    """Create a Task for the PR, start it, and return the task id."""
    create_body: dict = {
        "title": f"dc-review-pr #{pr}",
        "description": build_prompt(cfg, pr, delivery=delivery),
        "repository": cfg.repo,
    }
    if cfg.channel:
        create_body["channel"] = cfg.channel
    if cfg.github_integration_id:
        create_body["github_integration"] = cfg.github_integration_id
    task = api_request(cfg, "POST", "/tasks/", create_body)
    task_id = task["id"]
    run_body: dict = {"mode": "background"}
    if cfg.runtime_adapter and cfg.model:
        run_body["runtime_adapter"] = cfg.runtime_adapter
        run_body["model"] = cfg.model
        if cfg.reasoning_effort:
            run_body["reasoning_effort"] = cfg.reasoning_effort
    api_request(cfg, "POST", f"/tasks/{task_id}/run/", run_body)
    return task_id


def latest_run_id(cfg: Config, task_id: str) -> str | None:
    task = api_request(cfg, "GET", f"/tasks/{task_id}/")
    run = task.get("latest_run")
    return run.get("id") if run else None


def to_slack_mrkdwn(text: str) -> str:
    """Light GitHub-markdown -> Slack-mrkdwn touch-up."""
    out_lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            stripped = line.lstrip("#").strip()
            line = f"*{stripped}*" if stripped else ""
        out_lines.append(line)
    return "\n".join(out_lines).replace("**", "*")


def chunk_text(text: str, limit: int = SLACK_BLOCK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        chunks.append(current)
    return chunks


def post_to_slack(cfg: Config, *, pr: int, task_id: str, report: str, note: str | None = None) -> None:
    pr_url = f"https://github.com/{cfg.repo}/pull/{pr}"
    header = f":mag: *Review: <{pr_url}|{cfg.repo}#{pr}>*  ·  <{cfg.task_url(task_id)}|PostHog Task>"
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": header}}]
    if report:
        blocks.append({"type": "divider"})
        for chunk in chunk_text(to_slack_mrkdwn(report)):
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": note or "_No report found — open the Task to check._"},
        })
    payload = {"text": f"Review: {cfg.repo}#{pr}", "blocks": blocks[:50]}
    req = urllib.request.Request(
        cfg.slack_webhook,  # type: ignore[arg-type]
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"  ! slack post failed for #{pr}: HTTP {exc.code} {body}", file=sys.stderr)


def _done_message(cfg: Config, *, pr: int, delivery: DeliveryMode, slack: bool) -> str:
    if slack:
        return "posted to Slack"
    if delivery == "github":
        return "posted to GitHub (single comment)"
    if delivery == "github-inline":
        return "posted to GitHub (inline comments)"
    return "done"


def run_poll(cfg: Config, prs: list[int], *, slack: bool, delivery: DeliveryMode) -> None:
    tasks: dict[int, str] = {}
    run_ids: dict[int, str] = {}

    for pr in prs:
        task_id = create_and_run(cfg, pr, delivery=delivery)
        tasks[pr] = task_id
        print(f"#{pr}: started — {cfg.task_url(task_id)}", flush=True)

    pending = set(prs)
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS

    while pending and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        print(f"  polling {len(pending)} task(s)…", flush=True)

        for pr in list(pending):
            task_id = tasks[pr]
            if pr not in run_ids:
                rid = latest_run_id(cfg, task_id)
                if not rid:
                    print(f"    #{pr}: no run yet", flush=True)
                    continue
                run_ids[pr] = rid

            report = select_report(fetch_agent_messages(cfg, task_id, run_ids[pr]))
            if not report:
                print(f"    #{pr}: waiting for report…", flush=True)
                continue

            pending.discard(pr)
            if slack:
                post_to_slack(cfg, pr=pr, task_id=task_id, report=report)
            else:
                pr_url = f"https://github.com/{cfg.repo}/pull/{pr}"
                print(f"\n--- #{pr}: {pr_url} ---\n{report}\n", flush=True)
            print(f"#{pr}: {_done_message(cfg, pr=pr, delivery=delivery, slack=slack)}", flush=True)

    for pr in pending:
        task_id = tasks[pr]
        msg = f"#{pr}: timed out — {cfg.task_url(task_id)}"
        if slack:
            post_to_slack(
                cfg, pr=pr, task_id=task_id, report="",
                note=f"_Timed out waiting for the review. <{cfg.task_url(task_id)}|Open the Task> to check._",
            )
            msg += " (posted timeout note to Slack)"
        print(msg, file=sys.stderr, flush=True)


def daemonize(prs: list[int], *, slack: bool, delivery: DeliveryMode) -> None:
    """Re-exec this script without --detach as a detached background process."""
    script = Path(__file__).resolve()
    log_path = script.parent / f"review-{int(time.time())}.log"

    cmd = [sys.executable, str(script)]
    if slack:
        cmd.append("--slack")
    elif delivery == "github":
        cmd.append("--github")
    elif delivery == "github-inline":
        cmd.append("--github-inline")
    cmd.extend(str(pr) for pr in prs)

    log_file = open(log_path, "w")  # noqa: SIM115 — handed to the child via fd inheritance
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # detach from the controlling terminal
        close_fds=True,
    )
    log_file.close()

    print(f"Running in background (pid {proc.pid})")
    print(f"Log: {log_path}")
    print(f"Follow with:  tail -f {log_path}")


def parse_delivery(args: argparse.Namespace) -> DeliveryMode:
    if args.github:
        return "github"
    if args.github_inline:
        return "github-inline"
    return "report-only"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fire dc-review-pr cloud Tasks for one or more PRs.")
    parser.add_argument("prs", nargs="+", type=int, help="PR numbers to review.")
    parser.add_argument("--slack", action="store_true",
                        help="Post each report to Slack (DC_REVIEW_SLACK_WEBHOOK) instead of the terminal.")
    parser.add_argument("--detach", action="store_true",
                        help="Run in the background (daemon). Logs to review-<timestamp>.log.")
    github_group = parser.add_mutually_exclusive_group()
    github_group.add_argument("--github", action="store_true",
                              help="Have the Task post the full report as a single PR comment.")
    github_group.add_argument("--github-inline", action="store_true",
                              help="Have the Task post each finding as an inline review comment.")
    args = parser.parse_args()

    load_dotenv()
    delivery = parse_delivery(args)

    if args.detach:
        Config(need_slack=args.slack)  # validate config before forking
        daemonize(args.prs, slack=args.slack, delivery=delivery)
        return

    cfg = Config(need_slack=args.slack)
    run_poll(cfg, args.prs, slack=args.slack, delivery=delivery)


if __name__ == "__main__":
    main()

