"""Wires adapters together (dependency injection). A fresh Http per PR gives a per-PR request budget."""
from __future__ import annotations

from .config import Settings
from .evaluator import Evaluator, GeminiClient
from .git_github import GitHubPlatform
from .http import Http
from .orchestrator import Orchestrator
from .tickets import AzureDevOpsProvider, JiraProvider


def build(s: Settings, transport=None, audit_dir: str | None = "runs") -> Orchestrator:
    http = Http(transport, max_requests=s.max_api_requests_per_pr) if transport else Http(max_requests=s.max_api_requests_per_pr)
    platform = GitHubPlatform(http, s.github_token)
    if s.ticket_provider == "azure":
        tickets = AzureDevOpsProvider(http, s.azure_org_url, s.azure_project, s.azure_pat)
    else:
        tickets = JiraProvider(http, s.jira_base_url, s.jira_email, s.jira_api_token, s.jira_ac_field)
    evaluator = Evaluator(GeminiClient(http, s.gemini_api_key, s.gemini_model), s.max_llm_calls_per_pr)
    return Orchestrator(s, platform, tickets, evaluator, audit_dir)
