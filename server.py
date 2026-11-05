"""Webhook listener. Run: python server.py   (POST /webhook, GET /healthz)

Replies to GitHub within its 10-second limit (202), then reviews in one background worker,
so reviews never run in parallel on the same repository.
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pr_reviewer.config import Settings
from pr_reviewer.factory import build
from pr_reviewer.webhook import parse_github

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pr_reviewer")


class Dedupe:
    """Idempotency: the same delivery, or the same PR head commit, is processed only once."""

    def __init__(self, size: int = 1000):
        self.seen: dict[str, None] = {}
        self.size = size
        self.lock = threading.Lock()

    def first_time(self, *keys: str) -> bool:
        with self.lock:
            if any(k and k in self.seen for k in keys):
                return False
            for k in keys:
                if k:
                    self.seen[k] = None
            while len(self.seen) > self.size:
                self.seen.pop(next(iter(self.seen)))
            return True


def make_handler(s: Settings, jobs: queue.Queue, dedupe: Dedupe):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, msg: str):
            data = json.dumps({"status": code, "message": msg}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # route http.server logs through logging
            log.info("http " + fmt % args)

        def do_GET(self):
            if self.path == "/healthz":
                return self._reply(200, f"ok (dry_run={s.dry_run}, auto_merge={s.auto_merge_enabled}, queue={jobs.qsize()})")
            self._reply(404, "not found")

        def do_POST(self):
            if self.path != "/webhook":
                return self._reply(404, "not found")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411, "Content-Length required")
            if length > s.max_body_bytes:
                return self._reply(413, "payload too large")
            body = self.rfile.read(length)
            res = parse_github(dict(self.headers), body, s.webhook_secret, s.allow_unsigned_webhooks,
                               s.allowed_repos, s.max_body_bytes)
            if res.event is None:
                return self._reply(res.status, res.message)
            ev = res.event
            # The review depends on the commit AND the title/branch (the ticket key can be added by editing
            # the title), so a title edit on the same commit is a new review, while a description edit is not.
            if not dedupe.first_time(ev.delivery_id, f"{ev.repo}#{ev.number}@{ev.head_sha}|{ev.title}|{ev.branch}"):
                return self._reply(200, "duplicate delivery or commit already reviewed")
            jobs.put(ev)
            self._reply(202, f"queued review of {ev.repo}#{ev.number} @ {ev.head_sha[:7]}")
    return Handler


def worker(s: Settings, jobs: queue.Queue):
    while True:
        ev = jobs.get()
        try:
            rec = build(s).review(ev)   # fresh adapters + request budget per PR
            log.info(f"review finished {ev.repo}#{ev.number}: {rec.outcome} ({rec.reason})")
        except Exception:
            log.exception("worker error (no merge was performed)")
        finally:
            jobs.task_done()


def main() -> int:
    s = Settings.from_env()
    problems = s.problems()
    if problems:
        print("Configuration errors:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1
    jobs: queue.Queue = queue.Queue(maxsize=100)
    threading.Thread(target=worker, args=(s, jobs), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", s.port), make_handler(s, jobs, Dedupe()))
    print(f"Listening on :{s.port}/webhook  (DRY_RUN={s.dry_run}, AUTO_MERGE_ENABLED={s.auto_merge_enabled})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Stopping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
