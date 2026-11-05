"""Component 2b: Story Fetcher. Adapters for Jira Cloud and Azure DevOps behind one interface."""
from __future__ import annotations

import base64
import html
import re
import urllib.parse
from typing import Protocol

from .errors import HttpError, SafeStop
from .http import Http
from .models import Ticket

AC_HEADING = re.compile(r"^\s*(?:#+\s*|\*+\s*)?(acceptance\s+criteria|ac)\s*[:\-]?\s*\**\s*$", re.I)
NEXT_HEADING = re.compile(r"^\s*(?:#+\s*\S|\*\*[^*]+\*\*\s*:?\s*$|[A-Z][A-Za-z ]{2,40}:\s*$)")


class TicketProvider(Protocol):
    def fetch(self, ticket_id: str) -> Ticket: ...


def adf_to_text(node) -> str:
    """Jira Cloud descriptions use Atlassian Document Format (JSON). Convert to plain text lines."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    inner = [adf_to_text(c) for c in node.get("content", []) or []]
    if t in ("paragraph", "heading"):
        prefix = "#" * node.get("attrs", {}).get("level", 0) + " " if t == "heading" else ""
        return prefix + "".join(inner) + "\n"
    if t == "listItem":
        return "- " + "".join(inner).strip() + "\n"
    if t == "hardBreak":
        return "\n"
    return "".join(inner)


def html_to_text(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s or "")
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


BULLET = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)]|\[[ xX]\]|[☐☑☒✓✔])\s*")
_STRIP = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])?\s*(?:\[[ xX]\]|[☐☑☒✓✔])?\s*")


def split_criteria(text: str) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        line = _STRIP.sub("", line).strip()
        if line:
            items.append(line)
    return items


def criteria_from_description(text: str) -> list[str]:
    """Take the lines under an 'Acceptance Criteria' heading, up to the next heading."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if AC_HEADING.match(line.strip().strip("*")):
            block: list[str] = []
            in_list = False
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    continue
                is_item = bool(BULLET.match(nxt))
                # A bulleted list ends at the first line that is not a list item
                # (e.g. the next section title such as "Suggested sub-tasks").
                if in_list and not is_item:
                    break
                if not is_item and NEXT_HEADING.match(nxt):
                    break
                in_list = in_list or is_item
                block.append(nxt)
            return split_criteria("\n".join(block))
    return []


def _no_ac(ticket_id: str) -> SafeStop:
    return SafeStop("NO_ACCEPTANCE_CRITERIA",
                    f"Ticket {ticket_id} has no acceptance criteria, so there is nothing to verify the change "
                    "against. Add an 'Acceptance Criteria' section to the ticket and push again.")


def _fetch_error(ticket_id: str, e: HttpError, system: str) -> SafeStop:
    if e.status == 404:
        return SafeStop("TICKET_NOT_FOUND", f"Ticket {ticket_id} was not found in {system}. Check the ticket key.")
    if e.status in (401, 403):
        return SafeStop("TICKET_AUTH_FAILED", f"The reviewer is not allowed to read {ticket_id} in {system} "
                        f"(HTTP {e.status}). Check the service credentials.")
    return SafeStop("TICKET_FETCH_FAILED", f"Ticket {ticket_id} could not be fetched from {system} ({e}).")


class JiraProvider:
    def __init__(self, http: Http, base_url: str, email: str, token: str, ac_field: str = ""):
        self.http, self.base, self.ac_field = http, base_url.rstrip("/"), ac_field
        self.auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()

    def fetch(self, ticket_id: str) -> Ticket:
        fields = "summary,description" + (f",{self.ac_field}" if self.ac_field else "")
        url = f"{self.base}/rest/api/3/issue/{urllib.parse.quote(ticket_id)}?fields={fields}"
        try:
            data = self.http.json("GET", url, {"Authorization": self.auth, "Accept": "application/json"})
        except HttpError as e:
            raise _fetch_error(ticket_id, e, "Jira") from None
        f = data.get("fields") or {}
        desc = adf_to_text(f.get("description")) if isinstance(f.get("description"), dict) else (f.get("description") or "")
        ac: list[str] = []
        if self.ac_field and f.get(self.ac_field):
            v = f[self.ac_field]
            ac = split_criteria(adf_to_text(v) if isinstance(v, dict) else str(v))
        if not ac:
            ac = criteria_from_description(desc)
        if not ac:
            raise _no_ac(ticket_id)
        return Ticket(ticket_id, f.get("summary") or "", desc.strip(), ac, f"{self.base}/browse/{ticket_id}")


class AzureDevOpsProvider:
    AC_FIELD = "Microsoft.VSTS.Common.AcceptanceCriteria"

    def __init__(self, http: Http, org_url: str, project: str, pat: str):
        self.http, self.org, self.project = http, org_url.rstrip("/"), project
        self.auth = "Basic " + base64.b64encode(f":{pat}".encode()).decode()

    def fetch(self, ticket_id: str) -> Ticket:
        num = re.sub(r"\D", "", ticket_id.split("-")[-1])
        if not num:
            raise SafeStop("TICKET_NOT_FOUND", f"'{ticket_id}' is not a valid Azure DevOps work item ID.")
        url = (f"{self.org}/{urllib.parse.quote(self.project)}/_apis/wit/workitems/{num}"
               f"?fields=System.Title,System.Description,{self.AC_FIELD}&api-version=7.1")
        try:
            data = self.http.json("GET", url, {"Authorization": self.auth, "Accept": "application/json"})
        except HttpError as e:
            raise _fetch_error(ticket_id, e, "Azure DevOps") from None
        f = data.get("fields") or {}
        desc = html_to_text(f.get("System.Description", ""))
        ac = split_criteria(html_to_text(f.get(self.AC_FIELD, ""))) or criteria_from_description(desc)
        if not ac:
            raise _no_ac(ticket_id)
        return Ticket(ticket_id, f.get("System.Title", ""), desc.strip(), ac,
                      f"{self.org}/{self.project}/_workitems/edit/{num}")
