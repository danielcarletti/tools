#!/usr/bin/env python3
"""Publish the local dc-review-pr skill body to PostHog.

Prefer the PostHog MCP `skill-update` tool when available (uses your connected
session; no extra API key scopes needed). This script is a CLI fallback.

Requires POSTHOG_API_KEY with llm_skill:write scope (in addition to task:write).
Uses the same .env as review.py.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SKILL_NAME = "dc-review-pr"
SKILL_BODY_PATH = Path(__file__).resolve().parent / "skill-body.md"
SKILL_DESCRIPTION = (
    "Review a GitHub PR against DC's review checklist — comments, naming, control flow, "
    "scope/side-effects, modularity, DRY, tests, dead code, and performance/cost. "
    'Use when asked to review a PR with "dc\'s review", "dc-review-pr", or to apply this '
    "checklist to a PR number. Supports report-only (default), single PR comment "
    "(delivery mode: github), or inline review comments (delivery mode: github-inline)."
)


def load_dotenv() -> None:
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


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("POSTHOG_API_KEY")
    project_id = os.environ.get("POSTHOG_PROJECT_ID")
    host = os.environ.get("POSTHOG_HOST", "https://us.posthog.com").rstrip("/")
    if not api_key or not project_id:
        sys.exit("error: POSTHOG_API_KEY and POSTHOG_PROJECT_ID are required.")

    if not SKILL_BODY_PATH.is_file():
        sys.exit(f"error: skill body not found at {SKILL_BODY_PATH}")

    body = SKILL_BODY_PATH.read_text()
    fetch_url = f"{host}/api/environments/{project_id}/llm_skills/name/{SKILL_NAME}/"
    req = urllib.request.Request(fetch_url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            current = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"error: could not fetch current skill: HTTP {exc.code}\n{exc.read().decode()}")

    payload = json.dumps({
        "body": body,
        "description": SKILL_DESCRIPTION,
        "base_version": current["version"],
    }).encode()
    publish_url = fetch_url
    req = urllib.request.Request(publish_url, data=payload, method="PATCH")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        if exc.code == 403 and "llm_skill:write" in detail:
            sys.exit(
                "error: POSTHOG_API_KEY needs llm_skill:write scope to publish.\n"
                "Create a personal API key with that scope, update .env, and re-run."
            )
        sys.exit(f"error: publish failed: HTTP {exc.code}\n{detail}")

    print(f"Published {SKILL_NAME} v{data['version']} (was v{current['version']})")


if __name__ == "__main__":
    main()
