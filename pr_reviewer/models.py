from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PREvent:
    platform: str          # "github"
    repo: str              # owner/name
    number: int
    title: str
    branch: str
    base: str
    head_sha: str
    action: str
    draft: bool
    delivery_id: str = ""
    author: str = ""


@dataclass
class Ticket:
    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    url: str = ""


@dataclass
class Verdict:
    fulfilled: bool
    feedback: str
    raw: str = ""


@dataclass
class RunRecord:
    """Audit trail of one review; saved as JSON."""
    delivery_id: str
    repo: str
    pr: int
    head_sha: str
    steps: list[dict] = field(default_factory=list)
    outcome: str = ""          # merged | approved_not_merged | failed | stopped | ignored
    reason: str = ""
    dry_run: bool = True

    def step(self, name: str, **info) -> None:
        self.steps.append({"step": name, **info})
