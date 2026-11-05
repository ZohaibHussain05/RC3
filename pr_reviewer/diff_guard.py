"""Checks on the diff before it is sent to the LLM. Never truncate silently."""
from __future__ import annotations

import re

from .errors import SafeStop


def check_diff(diff: str, max_chars: int) -> str:
    if not diff or not diff.strip():
        raise SafeStop("DIFF_EMPTY", "The PR has no code changes (empty diff), so there is nothing to verify.")
    files = re.findall(r"^diff --git a/(\S+)", diff, re.M)
    text_hunks = re.findall(r"^@@ ", diff, re.M)
    if files and not text_hunks:
        raise SafeStop("DIFF_BINARY_ONLY", "The PR only changes binary files, which cannot be reviewed "
                       f"automatically ({', '.join(files[:5])}). This PR needs a human review.")
    if len(diff) > max_chars:
        raise SafeStop("DIFF_TOO_LARGE",
                       f"The diff is {len(diff):,} characters, more than the {max_chars:,} that can be sent to the "
                       f"AI reviewer in one request ({len(files)} files changed). It was not truncated, because a "
                       "partial review could approve unreviewed code. Split the PR or ask for a human review.")
    return diff
