"""Component 2a: extract exactly one ticket ID from the PR title and branch name."""
from __future__ import annotations

import re

from .errors import SafeStop


def extract_ticket_id(title: str, branch: str, pattern: str) -> str:
    rx = re.compile(pattern)
    # Branch names are often lower-case (feature/jira-123-login); match case-insensitively there.
    found = [m.upper() for m in rx.findall(title or "")]
    found += [m.upper() for m in re.compile(pattern, re.I).findall(branch or "")]
    unique = list(dict.fromkeys(found))
    if not unique:
        raise SafeStop("NO_TICKET_ID",
                       "No ticket ID was found in the PR title or branch name, so the change cannot be checked "
                       "against acceptance criteria. Add the ticket key (e.g. `JIRA-123`) to the title or branch.",
                       f"title={title!r} branch={branch!r}")
    if len(unique) > 1:
        raise SafeStop("MULTIPLE_TICKET_IDS",
                       f"Several different ticket IDs were found ({', '.join(unique)}). It is not clear which ticket "
                       "this PR implements, so it was not reviewed. Keep one ticket key per PR.")
    return unique[0]
