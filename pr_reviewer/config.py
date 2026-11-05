"""All configuration comes from environment variables (or a local .env file). No secrets in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path: str | None = None) -> None:
    """Minimal .env reader (stdlib only). Real environment variables always win."""
    path = path or os.path.join(_ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # Git platform
    github_token: str = ""
    webhook_secret: str = ""
    allow_unsigned_webhooks: bool = False          # only for local testing; never in production
    allowed_repos: set[str] = field(default_factory=set)  # empty = all repos the token can access
    # Ticket system
    ticket_provider: str = "jira"                  # "jira" or "azure"
    ticket_pattern: str = r"\b([A-Z][A-Z0-9]{1,9}-\d{1,7})\b"  # Jira-style key, e.g. JIRA-123
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_ac_field: str = ""                        # optional custom field id, e.g. customfield_10042
    azure_org_url: str = ""                        # e.g. https://dev.azure.com/myorg
    azure_project: str = ""
    azure_pat: str = ""
    # LLM
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    max_llm_calls_per_pr: int = 2
    # Safety
    dry_run: bool = True                           # default: print actions, change nothing
    auto_merge_enabled: bool = False               # must be switched on explicitly
    merge_method: str = "squash"                   # merge | squash | rebase
    status_context: str = "ai-pr-reviewer"
    max_diff_chars: int = 60_000
    max_api_requests_per_pr: int = 30
    max_body_bytes: int = 2_000_000
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = os.environ.get
        return cls(
            github_token=e("GITHUB_TOKEN", ""),
            webhook_secret=e("WEBHOOK_SECRET", ""),
            allow_unsigned_webhooks=_bool("ALLOW_UNSIGNED_WEBHOOKS", False),
            allowed_repos={r.strip().lower() for r in e("ALLOWED_REPOS", "").split(",") if r.strip()},
            ticket_provider=e("TICKET_PROVIDER", "jira").strip().lower(),
            ticket_pattern=e("TICKET_PATTERN", cls.ticket_pattern),
            jira_base_url=e("JIRA_BASE_URL", "").rstrip("/"),
            jira_email=e("JIRA_EMAIL", ""),
            jira_api_token=e("JIRA_API_TOKEN", ""),
            jira_ac_field=e("JIRA_AC_FIELD", ""),
            azure_org_url=e("AZURE_ORG_URL", "").rstrip("/"),
            azure_project=e("AZURE_PROJECT", ""),
            azure_pat=e("AZURE_PAT", ""),
            gemini_api_key=e("GEMINI_API_KEY", ""),
            gemini_model=e("GEMINI_MODEL", "gemini-2.5-flash"),
            max_llm_calls_per_pr=_int("MAX_LLM_CALLS_PER_PR", 2),
            dry_run=_bool("DRY_RUN", True),
            auto_merge_enabled=_bool("AUTO_MERGE_ENABLED", False),
            merge_method=e("MERGE_METHOD", "squash"),
            status_context=e("STATUS_CONTEXT", "ai-pr-reviewer"),
            max_diff_chars=_int("MAX_DIFF_CHARS", 60_000),
            max_api_requests_per_pr=_int("MAX_API_REQUESTS_PER_PR", 30),
            max_body_bytes=_int("MAX_BODY_BYTES", 2_000_000),
            port=_int("PORT", 8080),
        )

    def problems(self) -> list[str]:
        """Configuration errors that must stop the service at start-up (fail fast)."""
        p = []
        if not self.github_token:
            p.append("GITHUB_TOKEN is not set")
        if not self.webhook_secret and not self.allow_unsigned_webhooks:
            p.append("WEBHOOK_SECRET is not set (set ALLOW_UNSIGNED_WEBHOOKS=true only for local tests)")
        if self.ticket_provider == "jira" and not (self.jira_base_url and self.jira_email and self.jira_api_token):
            p.append("Jira needs JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN")
        if self.ticket_provider == "azure" and not (self.azure_org_url and self.azure_project and self.azure_pat):
            p.append("Azure DevOps needs AZURE_ORG_URL, AZURE_PROJECT and AZURE_PAT")
        if self.ticket_provider not in ("jira", "azure"):
            p.append("TICKET_PROVIDER must be 'jira' or 'azure'")
        if not self.gemini_api_key:
            p.append("GEMINI_API_KEY is not set")
        if self.merge_method not in ("merge", "squash", "rebase"):
            p.append("MERGE_METHOD must be merge, squash or rebase")
        return p
