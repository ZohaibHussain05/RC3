"""Review one PR directly (same pipeline as the webhook), useful for demos and testing.

    python review_pr.py owner/repo 12
"""
from __future__ import annotations

import logging
import sys

from pr_reviewer.config import Settings
from pr_reviewer.errors import HttpError
from pr_reviewer.factory import build
from pr_reviewer.http import Http
from pr_reviewer.git_github import GitHubPlatform
from pr_reviewer.models import PREvent

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit() or "/" not in argv[0]:
        print("usage: python review_pr.py owner/repo PR_NUMBER", file=sys.stderr)
        return 2
    s = Settings.from_env()
    problems = [p for p in s.problems() if "WEBHOOK_SECRET" not in p]
    if problems:
        print("Configuration errors:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1
    try:
        pr = GitHubPlatform(Http(), s.github_token).get_pr(argv[0], int(argv[1]))
    except HttpError as e:
        print(f"Could not load the PR: {e}", file=sys.stderr)
        return 1
    if pr.get("state") != "open":
        print("The PR is not open; nothing to review.", file=sys.stderr)
        return 1
    ev = PREvent("github", argv[0], int(argv[1]), pr.get("title", ""), pr["head"]["ref"], pr["base"]["ref"],
                 pr["head"]["sha"], "manual", bool(pr.get("draft")), "manual-run", (pr.get("user") or {}).get("login", ""))
    rec = build(s).review(ev)
    print(f"\nOutcome: {rec.outcome} ({rec.reason})  dry_run={rec.dry_run}")
    return 0 if rec.outcome in ("merged", "approved_not_merged", "failed") else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
