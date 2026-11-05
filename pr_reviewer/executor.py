"""Component 5: Action Executor. Comment + failed status, or merge. DRY_RUN prints instead of writing."""
from __future__ import annotations

import logging

from .config import Settings
from .errors import SafeStop
from .git_github import GitPlatform
from .models import PREvent, RunRecord, Ticket, Verdict

log = logging.getLogger("pr_reviewer")
FOOTER = "\n\n---\n<sub>🤖 ai-pr-reviewer · reviewed commit `{sha}` · {mode}</sub>"


class Executor:
    def __init__(self, platform: GitPlatform, s: Settings):
        self.p, self.s = platform, s

    def _mode(self) -> str:
        return "DRY RUN (no changes made)" if self.s.dry_run else "live"

    def _write(self, rec: RunRecord, action: str, fn, *args):
        rec.step("action", action=action, dry_run=self.s.dry_run)
        if self.s.dry_run:
            log.info(json_line(rec, f"DRY RUN: would {action}"))
            print(f"[DRY RUN] would {action}")
            return None
        return fn(*args)

    def status(self, rec: RunRecord, ev: PREvent, state: str, description: str):
        self._write(rec, f"set commit status '{state}' on {ev.head_sha[:7]}: {description}",
                    self.p.set_status, ev.repo, ev.head_sha, state, description, self.s.status_context)

    def comment(self, rec: RunRecord, ev: PREvent, body: str):
        body += FOOTER.format(sha=ev.head_sha[:7], mode=self._mode())
        self._write(rec, f"comment on PR #{ev.number}:\n{body}", self.p.comment, ev.repo, ev.number, body)

    # ---------------------------------------------------------------- outcomes
    def reject(self, rec: RunRecord, ev: PREvent, ticket: Ticket, verdict: Verdict):
        body = (f"### ❌ Acceptance criteria not fulfilled ({ticket.id})\n\n"
                f"**Ticket:** [{ticket.id}]({ticket.url}) {ticket.title}\n\n{verdict.feedback}\n\n"
                "The PR was **not merged**. The `" + self.s.status_context + "` check is set to failed until a new "
                "commit fulfils the criteria.")
        self.comment(rec, ev, body)
        self.status(rec, ev, "failure", f"{ticket.id}: acceptance criteria not fulfilled")
        rec.outcome, rec.reason = "failed", "acceptance criteria not fulfilled"

    def safe_stop(self, rec: RunRecord, ev: PREvent, stop: SafeStop):
        body = (f"### ⚠️ Automated review stopped: `{stop.code}`\n\n{stop.message}\n\n"
                "The PR was **not merged**. Nothing was assumed; fix the issue above and push a new commit.")
        try:
            self.comment(rec, ev, body)
            self.status(rec, ev, "failure", f"{stop.code}: review stopped, not merged")
        except Exception as e:  # reporting must never turn into a merge or a crash loop
            log.error(json_line(rec, f"could not report safe stop: {e}"))
        rec.outcome, rec.reason = "stopped", stop.code

    def approve_and_merge(self, rec: RunRecord, ev: PREvent, ticket: Ticket, verdict: Verdict):
        # Re-check the live PR right before the irreversible step.
        pr = self.p.get_pr(ev.repo, ev.number)
        if pr.get("state") != "open" or pr.get("merged"):
            raise SafeStop("PR_NOT_OPEN", "The PR was closed or merged while it was being reviewed.")
        if pr.get("draft"):
            raise SafeStop("PR_IS_DRAFT", "The PR was converted to a draft during the review.")
        if pr.get("head", {}).get("sha") != ev.head_sha:
            raise SafeStop("PR_CHANGED", "New commits were pushed during the review; the reviewed commit is no "
                           "longer the PR head. The new commits will be reviewed by their own webhook.")
        if pr.get("mergeable") is not True:
            why = "has merge conflicts or is blocked" if pr.get("mergeable") is False else "mergeability is unknown"
            raise SafeStop("NOT_MERGEABLE", f"The acceptance criteria are fulfilled, but GitHub reports the PR {why} "
                           f"(mergeable_state: {pr.get('mergeable_state', 'unknown')}). It was not merged.")
        self.status(rec, ev, "success", f"{ticket.id}: acceptance criteria fulfilled")
        if not self.s.auto_merge_enabled:
            self.comment(rec, ev, f"### ✅ Acceptance criteria fulfilled ({ticket.id})\n\n{verdict.feedback}\n\n"
                                  "Auto-merge is disabled (`AUTO_MERGE_ENABLED=false`), so the PR was not merged.")
            rec.outcome, rec.reason = "approved_not_merged", "auto-merge disabled"
            return
        self.comment(rec, ev, f"### ✅ Acceptance criteria fulfilled ({ticket.id}), merging\n\n{verdict.feedback}")
        self._write(rec, f"merge PR #{ev.number} ({self.s.merge_method}) pinned to {ev.head_sha[:7]}",
                    self.p.merge, ev.repo, ev.number, ev.head_sha, self.s.merge_method,
                    f"{ev.title} (#{ev.number})")
        rec.outcome, rec.reason = "merged", "acceptance criteria fulfilled"


def json_line(rec: RunRecord, msg: str, **extra) -> str:
    import json
    return json.dumps({"delivery": rec.delivery_id, "repo": rec.repo, "pr": rec.pr, "msg": msg, **extra})
