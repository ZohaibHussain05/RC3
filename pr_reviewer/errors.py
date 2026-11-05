"""Every reason to stop without merging has a code and a message that is posted on the PR."""
from __future__ import annotations


class SafeStop(Exception):
    """Stop the pipeline safely: never merge, explain on the PR, set status to failure."""

    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} from {url.split('?')[0]}")
        self.status = status
        self.url = url
        self.body = body[:500]


class BudgetExceeded(Exception):
    pass
