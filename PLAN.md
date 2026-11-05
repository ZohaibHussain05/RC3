# Task 4, Track A: Automated PR Reviewer (Universal Webhook Listener). Plan

## Goal
When a pull request is opened or updated, the system:
1. receives the webhook;
2. finds the ticket ID in the PR title or branch;
3. pulls the ticket's acceptance criteria (AC) from Jira or Azure DevOps;
4. fetches the PR diff;
5. asks an LLM, using the handbook's prompt blueprint, whether the diff fulfils the AC;
6. then **either** comments on the PR and sets the commit status to `failure` (which
   blocks merging), **or** merges the PR.

**Safety principle: merging is the only irreversible action, so it needs every check to
pass.** Any doubt (missing data, bad AI output, a changed PR, a configuration problem)
leads to a clear comment and a `failure` status, never a merge.

## Architecture
A fixed pipeline run by a code orchestrator. No LLM decides the control flow; the LLM is
one bounded step whose output is validated.

```
GitHub ──webhook──► [1 Listener] ──PREvent──► queue ──► [Orchestrator]
                      verify HMAC signature,            │
                      filter events, dedupe             ├─ 2 Ticket ID extractor   (code)
                      reply 202 within 10 s             ├─ 3 Story fetcher         (Jira | Azure DevOps adapter)
                                                        ├─ 4 Code fetcher          (GitHub adapter) + diff guard
                                                        ├─ 5 AI evaluation engine  (LLM adapter + strict JSON validator)
                                                        └─ 6 Action executor       (comment + status | merge), dry-run
```

| Component | Interface (port) | Adapters |
|-----------|------------------|----------|
| Git platform | `GitPlatform`: get_pr, get_diff, comment, set_status, merge | `GitHubPlatform` (+ a fake for tests) |
| Ticket system | `TicketProvider`: fetch(ticket_id) → Ticket | `JiraProvider`, `AzureDevOpsProvider` |
| LLM | `LLMClient`: complete(system, user) → text | `GeminiClient` (REST), fake for tests |

These are design principles, not extra features:
- ports and adapters, so platforms can be swapped;
- single responsibility per module;
- dependency injection, so everything is testable without a network;
- configuration only from the environment;
- typed dataclasses;
- structured JSON logs carrying a correlation ID (the webhook delivery ID);
- an audit record of every run.

## Edge cases (each one means: no merge, a clear PR comment, a `failure` status)
| Stage | Case |
|-------|------|
| Listener | Missing or bad signature (401). Unknown event or action ignored (204). `ping` (200). Body too large (413). Invalid JSON (400). Draft PR ignored. Duplicate delivery ignored (idempotency). Repository not in `ALLOWED_REPOS` ignored. |
| Ticket ID | None in the title or branch. **Several different IDs** (ambiguous). An ID is a project key that isn't configured. |
| Story fetcher | Ticket not found (404). Auth error (401/403). Network error or timeout (retried, then stop). Ticket has **no acceptance criteria**. |
| Code fetcher | Diff fetch fails. **Empty diff.** **Diff too large** for one LLM request: no silent truncation; it needs human review. Binary-only changes. |
| AI engine | Network/HTTP error (retried, then stop). Answer is **not valid JSON**. The **`fulfilled` key is missing** or not a real boolean (`"true"` the string is rejected). `feedback` missing or empty. One repair attempt, then stop. The diff is treated as data (prompt-injection guard). |
| Executor | PR closed or merged meanwhile. **New commits pushed during review** (head SHA changed): stop, and merge pins the reviewed SHA. Not mergeable (conflicts, branch protection), or mergeability unknown. Merge API error (405/409). `DRY_RUN=true` (the default) prints the actions instead of doing them. `AUTO_MERGE_ENABLED=false` means the PR is approved but not merged. |

## Limits
At most 2 LLM calls per PR (1 + 1 repair). At most 30 API requests per PR. Diff limit
`MAX_DIFF_CHARS` (default 60,000). Request timeouts of 20 s. 3 retries with backoff on
429/5xx.

## Tests
Unit and end-to-end tests with fake platforms, covering every row of the edge-case table.
Two required demo runs on a **test** repo and a **test** Jira project: one ending in
"Failed" with a comment, one ending in a merge.
