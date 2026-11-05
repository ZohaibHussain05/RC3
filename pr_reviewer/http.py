"""One HTTP helper for every external API: timeouts, retries with backoff, per-PR request budget."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable

from .errors import BudgetExceeded, HttpError

RETRY_STATUS = {429, 500, 502, 503, 504}
Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, dict, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""


class Http:
    def __init__(self, transport: Transport = urllib_transport, max_requests: int = 30,
                 retries: int = 3, backoff: tuple = (1.0, 2.0, 4.0), timeout: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.transport, self.max_requests, self.retries = transport, max_requests, retries
        self.backoff, self.timeout, self.sleep = backoff, timeout, sleep
        self.count = 0

    def request(self, method: str, url: str, headers: dict | None = None, json_body=None,
                ok: tuple = (200, 201, 202, 204)) -> tuple[int, dict, bytes]:
        body = json.dumps(json_body).encode() if json_body is not None else None
        h = {"User-Agent": "ai-pr-reviewer", **(headers or {})}
        if body is not None:
            h.setdefault("Content-Type", "application/json")
        last = None
        for attempt in range(self.retries + 1):
            if self.count >= self.max_requests:
                raise BudgetExceeded(f"API request budget reached ({self.max_requests} per PR)")
            self.count += 1
            try:
                status, rh, data = self.transport(method, url, h, body, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = HttpError(0, url, f"network error: {getattr(e, 'reason', e)}")
                status = None
            if status is not None:
                if status in ok:
                    return status, rh, data
                last = HttpError(status, url, data.decode("utf-8", "replace"))
                if status not in RETRY_STATUS:
                    raise last
            if attempt < self.retries:
                self.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        raise last

    def json(self, method: str, url: str, headers: dict | None = None, json_body=None, ok=(200, 201)):
        _, _, data = self.request(method, url, headers, json_body, ok)
        try:
            return json.loads(data.decode("utf-8") or "null")
        except ValueError:
            raise HttpError(-1, url, "response was not valid JSON") from None
