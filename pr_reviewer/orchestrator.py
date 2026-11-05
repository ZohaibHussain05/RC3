"""The code orchestrator: a fixed sequence of components. Any SafeStop or error means: no merge."""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
from dataclasses import asdict

from .config import Settings
from .diff_guard import check_diff
from .errors import BudgetExceeded, HttpError, SafeStop
from .evaluator import Evaluator
from .executor import Executor, json_line
from .models import PREvent, RunRecord
from .ticket_id import extract_ticket_id
from .tickets import TicketProvider

log = logging.getLogger("pr_reviewer")


class Orchestrator:
    def __init__(self, s: Settings, platform, tickets: TicketProvider, evaluator: Evaluator,
                 audit_dir: str | None = "runs"):
        self.s, self.platform, self.tickets, self.evaluator = s, platform, tickets, evaluator
        self.executor = Executor(platform, s)
        self.audit_dir = audit_dir

    def review(self, ev: PREvent) -> RunRecord:
        rec = RunRecord(ev.delivery_id, ev.repo, ev.number, ev.head_sha, dry_run=self.s.dry_run)
        t0 = time.time()
        try:
            self.executor.status(rec, ev, "pending", "AI review in progress")
            tid = extract_ticket_id(ev.title, ev.branch, self.s.ticket_pattern)
            rec.step("ticket_id", id=tid)
            ticket = self.tickets.fetch(tid)
            rec.step("ticket", id=ticket.id, criteria=len(ticket.acceptance_criteria))
            diff = check_diff(self.platform.get_diff(ev.repo, ev.number), self.s.max_diff_chars)
            rec.step("diff", chars=len(diff))
            verdict = self.evaluator.evaluate(ticket, diff)
            rec.step("verdict", fulfilled=verdict.fulfilled, llm_calls=self.evaluator.calls)
            if verdict.fulfilled:
                self.executor.approve_and_merge(rec, ev, ticket, verdict)
            else:
                self.executor.reject(rec, ev, ticket, verdict)
        except SafeStop as stop:
            rec.step("safe_stop", code=stop.code, detail=stop.detail)
            self.executor.safe_stop(rec, ev, stop)
        except (HttpError, BudgetExceeded) as e:
            self.executor.safe_stop(rec, ev, SafeStop("API_ERROR", f"An external API call failed ({e})."))
        except Exception as e:  # never let an unexpected error lead anywhere near a merge
            log.error(json_line(rec, "unexpected error", trace=traceback.format_exc()))
            self.executor.safe_stop(rec, ev, SafeStop("INTERNAL_ERROR",
                                                      f"The reviewer hit an unexpected error ({type(e).__name__})."))
        rec.step("done", seconds=round(time.time() - t0, 2))
        log.info(json_line(rec, f"outcome={rec.outcome}", reason=rec.reason))
        self._audit(rec)
        return rec

    def _audit(self, rec: RunRecord):
        if not self.audit_dir:
            return
        os.makedirs(self.audit_dir, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}_{rec.repo.replace('/', '_')}_pr{rec.pr}.json"
        with open(os.path.join(self.audit_dir, name), "w", encoding="utf-8") as f:
            json.dump(asdict(rec), f, indent=2)
