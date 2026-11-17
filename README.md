# tools

Small personal tools and agent skills. Each one lives in its own folder with its own README and has no shared code or dependencies with the others.

## Skills

Agent skills live under [skills/](skills/), one folder per skill.

| Skill | What it does |
| --- | --- |
| [dc-review-pr](skills/dc-review-pr/) | Fires one PostHog Code cloud Task per GitHub PR to review it against a checklist, then prints the report, posts it to Slack, or lets the Task comment on the PR. |
| [grilling](skills/grilling/) | Interviews you in rounds about a plan or design until every decision is settled. From [mattpocock/skills](https://github.com/mattpocock/skills) (MIT). |
| [grill-me](skills/grill-me/) | User-invoked `/grill-me` entry point that hands off to `grilling`. From [mattpocock/skills](https://github.com/mattpocock/skills) (MIT). |

To install a `SKILL.md`-based skill, copy its folder into `~/.claude/skills/`.

## Conventions

- One folder per tool or skill, with setup and usage in that folder's `README.md` (or `SKILL.md`).
- Secrets go in a per-tool `.env` (gitignored), with a committed `.env.example` listing the variables.
- Third-party skills are vendored verbatim with their upstream `LICENSE`.
