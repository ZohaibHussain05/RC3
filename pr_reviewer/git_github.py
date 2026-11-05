"""Component 3 + 5 (platform side): GitHub adapter for PR data, diff, comments, commit status and merge."""
from __future__ import annotations

import time
from typing import Protocol

from .errors import HttpError, SafeStop
from .http import Http

API = "https://api.github.com"


class GitPlatform(Protocol):
    def get_pr(self, repo: str, number: int) -> dict: ...
    def get_diff(self, repo: str, number: int) -> str: ...
    def comment(self, repo: str, number: int, body: str) -> None: ...
    def set_status(self, repo: str, sha: str, state: str, description: str, context: str) -> None: ...
    def merge(self, repo: str, number: int, sha: str, method: str, title: str) -> dict: ...


class GitHubPlatform:
    def __init__(self, http: Http, token: str, sleep=time.sleep):
        self.http, self.sleep = http, sleep
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}

    def get_pr(self, repo: str, number: int) -> dict:
        """Returns the PR. GitHub computes 'mergeable' lazily: if it is null, ask once more after a pause."""
        url = f"{API}/repos/{repo}/pulls/{number}"
        pr = self.http.json("GET", url, self.h)
        if pr.get("mergeable") is None and pr.get("state") == "open":
            self.sleep(2)
            pr = self.http.json("GET", url, self.h)
        return pr

    def get_diff(self, repo: str, number: int) -> str:
        try:
            _, _, data = self.http.request("GET", f"{API}/repos/{repo}/pulls/{number}",
                                           {**self.h, "Accept": "application/vnd.github.diff"})
        except HttpError as e:
            if e.status == 406 or "too_large" in e.body or "diff is taking too long" in e.body.lower():
                raise SafeStop("DIFF_TOO_LARGE", "GitHub could not produce the diff because it is too large. "
                               "This PR needs a human review.") from None
            raise SafeStop("DIFF_FETCH_FAILED", f"The PR diff could not be fetched ({e}).") from None
        return data.decode("utf-8", "replace")

    def comment(self, repo: str, number: int, body: str) -> None:
        self.http.json("POST", f"{API}/repos/{repo}/issues/{number}/comments", self.h, {"body": body})

    def set_status(self, repo: str, sha: str, state: str, description: str, context: str) -> None:
        self.http.json("POST", f"{API}/repos/{repo}/statuses/{sha}", self.h,
                       {"state": state, "description": description[:140], "context": context})

    def merge(self, repo: str, number: int, sha: str, method: str, title: str) -> dict:
        """'sha' pins the merge to the reviewed commit: GitHub refuses (409) if new commits arrived."""
        try:
            return self.http.json("PUT", f"{API}/repos/{repo}/pulls/{number}/merge", self.h,
                                  {"sha": sha, "merge_method": method, "commit_title": title})
        except HttpError as e:
            if e.status == 409:
                raise SafeStop("PR_CHANGED", "New commits were pushed while the PR was being reviewed, so it was "
                               "not merged. The new commits will be reviewed separately.") from None
            if e.status == 405:
                raise SafeStop("NOT_MERGEABLE", f"GitHub refused the merge (405): {e.body[:200]}") from None
            raise SafeStop("MERGE_FAILED", f"The merge request failed ({e}).") from None
