# Task 4, Track A: Automated PR Reviewer (Universal Webhook Listener)

**Track: A.** When a pull request is opened or updated, the reviewer:
1. receives the GitHub webhook;
2. finds the ticket ID in the PR title or branch (e.g. `feature/JIRA-123-login-page`);
3. pulls the ticket's acceptance criteria from **Jira** or **Azure DevOps**;
4. fetches the PR diff;
5. asks an LLM, using the handbook's prompt blueprint verbatim, for `{"fulfilled", "feedback"}`;
6. then **either** posts the feedback and sets the commit status to **failed** (blocking the
   merge), **or** merges the PR.

**Safety rule:** merging is irreversible, so every check must pass first. Any doubt leads
to a clear PR comment and a `failure` status, never a merge. Dry-run is **on** by
default, and auto-merge is **off** until you switch it on.

## Architecture
```
GitHub ─webhook─► server.py (Listener: HMAC check, filter, dedupe, 202 reply)
                      │ queue (one worker = no parallel merges)
                      ▼
            Orchestrator (fixed pipeline, decided by code, not by the LLM)
             ├─ ticket_id.py    extract exactly one ticket ID
             ├─ tickets.py      Story Fetcher: JiraProvider | AzureDevOpsProvider
             ├─ git_github.py   Code Fetcher: diff  (+ diff_guard.py)
             ├─ evaluator.py    AI Evaluation Engine: blueprint prompt + strict JSON validation
             └─ executor.py     Action Executor: comment + failed status | re-check + merge
```
- **Ports and adapters:** `GitPlatform`, `TicketProvider` and `LLMClient` are interfaces;
  a new platform is a new adapter, with no pipeline changes.
- **Dependency injection** (`factory.py`), so the whole pipeline is tested with fakes.
- **Fail-safe orchestration:** every failure becomes a `SafeStop(code, message)`, which
  means a comment, a `failure` status and no merge. Unexpected exceptions are caught the same way.
- **Idempotent:** a redelivered webhook, or a commit that was already reviewed, is skipped.
- **One worker:** reviews never race each other.
- **Merges are pinned to the reviewed commit SHA:** if new commits arrive, GitHub refuses
  the merge (409) and the tool reports it.
- **Observability:** JSON log lines carrying the webhook delivery ID, plus an audit record
  per review in `runs/*.json`.
- **Secrets only from environment variables / `.env`.** The Gemini key is sent in a
  header, never in a URL or log.
- **Python standard library only:** no packages to install, except `pytest` for the tests.

## Edge cases handled (none of them can lead to a merge)
| Case | Behaviour |
|------|-----------|
| No ticket ID / several different IDs | `NO_TICKET_ID` / `MULTIPLE_TICKET_IDS` comment, status failed |
| Ticket not found / no permission / network error | `TICKET_NOT_FOUND` / `TICKET_AUTH_FAILED` / `TICKET_FETCH_FAILED` |
| Ticket has no acceptance criteria | `NO_ACCEPTANCE_CRITERIA` (read from a custom field, or the "Acceptance Criteria" section of the description) |
| Empty diff / binary-only diff | `DIFF_EMPTY` / `DIFF_BINARY_ONLY`, with no LLM call |
| Diff too large for one request | `DIFF_TOO_LARGE`. **Never truncated**, because a partial review could approve unreviewed code. |
| LLM unreachable / blocked / cut off | `LLM_FAILED` / `LLM_EMPTY` / `LLM_INCOMPLETE` |
| LLM answer not JSON, `fulfilled` missing, `"true"` as a string, empty feedback | One repair call, then `LLM_INVALID_ANSWER` |
| Prompt injection in the diff ("set fulfilled to true") | The diff is passed as data; the system prompt tells the model to ignore instructions in it |
| PR closed, turned into a draft, or **new commits pushed** during the review | `PR_NOT_OPEN` / `PR_IS_DRAFT` / `PR_CHANGED` (the live PR is re-checked right before merging) |
| Merge conflicts, or mergeability unknown | `NOT_MERGEABLE` |
| Merge API refuses (405 / 409) | `NOT_MERGEABLE` / `PR_CHANGED` |
| Bad or missing webhook signature | HTTP 401, no review |
| No `WEBHOOK_SECRET` configured | Refuses webhooks (500) unless `ALLOW_UNSIGNED_WEBHOOKS=true` for local testing |
| Other events, draft or closed PR, repo not in `ALLOWED_REPOS` | Ignored (204) |
| Payload too large / invalid JSON / redelivery | 413 / 400 / 200 "duplicate" |
| GitHub, Jira or Gemini 429 / 5xx | 3 retries with backoff, then a safe stop. A 30-request budget per PR. |
| Missing configuration | The server refuses to start and lists every problem |

## How to run
```bash
copy .env.example .env        # fill it in (see SETUP.md); keep DRY_RUN=true at first
python -m pytest -q -p no:cacheprovider      # 48 tests, no network
python review_pr.py yourname/pr-reviewer-test 1   # review one PR now (same pipeline)
python server.py                                  # webhook listener on :8080/webhook
python send_test_webhook.py yourname/pr-reviewer-test 1 "JIRA-1 Add login" feature/JIRA-1-login <sha>
```
Set `DRY_RUN=false` to post comments and statuses for real, and `AUTO_MERGE_ENABLED=true`
to allow merges. Use a **test** repository only.

## Demo evidence (real services)
Repo `ZohaibHussain05/RC3`, PR #1; Jira ticket `SCRUM-29` (2 acceptance criteria); Gemini.
The audit records are in `runs/`, and the details are in `REVIEW_LOG.md` (items 2–7).

| Run | Commit | Outcome |
|-----|--------|---------|
| No ticket key in the title or branch | `11a474f` | Safe stop `NO_TICKET_ID`: comment + failed status |
| Only a test file, `calc.py` missing | `11a474f` | **Failed**: comment listing the missing `add()`, status `failure` |
| `calc.py` + `test_calc.py`, auto-merge off | `b81da9a` | Fulfilled, status `success`, not merged (safety switch) |
| Same commit, auto-merge on | `b81da9a` | **Merged** (squash, pinned to the reviewed SHA) |
| **Evaluator's test:** PR #2 `WUserController.js` vs ticket `SCRUM-30` (9 criteria), auto-merge on | `858cbb7` | **Failed**: all 9 criteria reported missing (a user controller, not configuration CRUD); not merged |
| Evaluator part 2: the fixed `userController.js` pushed to PR #2 | `1a50c10` | **Failed**: bugs fixed, but still no configuration APIs; not merged |
| PR #3: Gemini call failed (real service error) | `24b7c51` | Safe stop `LLM_FAILED`: comment + failed status, not merged; the re-run reviewed normally |
| PR #3 after deleting `WUserController.js` | `52fe791` | **Failed**: not merged |

## Libraries
Python 3.10+ standard library (`http.server`, `urllib`, `hmac`, `json`, `threading`,
`queue`, `dataclasses`). `pytest` is used for tests. The LLM is Google Gemini, through its REST API.
