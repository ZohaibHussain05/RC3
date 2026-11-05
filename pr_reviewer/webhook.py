"""Component 1: webhook verification and parsing. Pure functions; the HTTP server is in server.py."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from .models import PREvent

HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review", "edited"}


@dataclass
class WebhookResult:
    status: int            # HTTP status to return to GitHub
    message: str
    event: PREvent | None = None


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """GitHub sends X-Hub-Signature-256: sha256=<hmac>. Constant-time comparison."""
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def parse_github(headers: dict, body: bytes, secret: str, allow_unsigned: bool,
                 allowed_repos: set[str], max_bytes: int) -> WebhookResult:
    h = {k.lower(): v for k, v in headers.items()}
    if len(body) > max_bytes:
        return WebhookResult(413, f"payload larger than {max_bytes} bytes")
    if secret:
        if not verify_signature(secret, body, h.get("x-hub-signature-256")):
            return WebhookResult(401, "invalid or missing webhook signature")
    elif not allow_unsigned:
        return WebhookResult(500, "server misconfigured: WEBHOOK_SECRET not set")
    event_type = h.get("x-github-event", "")
    if event_type == "ping":
        return WebhookResult(200, "pong")
    if event_type != "pull_request":
        return WebhookResult(204, f"ignored event '{event_type or 'unknown'}'")
    try:
        p = json.loads(body.decode("utf-8"))
        pr, repo = p["pull_request"], p["repository"]["full_name"]
        action = p.get("action", "")
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return WebhookResult(400, "payload is not a valid pull_request event")
    if action not in HANDLED_ACTIONS:
        return WebhookResult(204, f"ignored action '{action}'")
    if allowed_repos and repo.lower() not in allowed_repos:
        return WebhookResult(204, f"repository {repo} is not in ALLOWED_REPOS")
    if pr.get("state") != "open":
        return WebhookResult(204, "pull request is not open")
    if pr.get("draft"):
        return WebhookResult(204, "draft pull request: review starts when it is marked ready")
    try:
        ev = PREvent(platform="github", repo=repo, number=int(pr["number"]), title=pr.get("title") or "",
                     branch=pr["head"]["ref"], base=pr["base"]["ref"], head_sha=pr["head"]["sha"],
                     action=action, draft=bool(pr.get("draft")), delivery_id=h.get("x-github-delivery", ""),
                     author=(pr.get("user") or {}).get("login", ""))
    except (KeyError, TypeError, ValueError):
        return WebhookResult(400, "pull_request payload is missing required fields")
    return WebhookResult(202, "accepted", ev)
