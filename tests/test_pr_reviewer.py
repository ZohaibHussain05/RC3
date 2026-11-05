"""Edge-case tests for Track A (no network). Run: python -m pytest -q -p no:cacheprovider"""
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pr_reviewer.config import Settings  # noqa: E402
from pr_reviewer.diff_guard import check_diff  # noqa: E402
from pr_reviewer.errors import BudgetExceeded, HttpError, SafeStop  # noqa: E402
from pr_reviewer.evaluator import BLUEPRINT, Evaluator, GeminiClient, parse_verdict  # noqa: E402
from pr_reviewer.git_github import GitHubPlatform  # noqa: E402
from pr_reviewer.http import Http  # noqa: E402
from pr_reviewer.models import PREvent, Ticket  # noqa: E402
from pr_reviewer.orchestrator import Orchestrator  # noqa: E402
from pr_reviewer.ticket_id import extract_ticket_id  # noqa: E402
from pr_reviewer.tickets import AzureDevOpsProvider, JiraProvider, adf_to_text, criteria_from_description  # noqa: E402
from pr_reviewer.webhook import parse_github, verify_signature  # noqa: E402

PATTERN = Settings().ticket_pattern
SHA = "a" * 40
DIFF = "diff --git a/login.py b/login.py\n@@ -0,0 +1,3 @@\n+def login(user, pw):\n+    return check(user, pw)\n"


# ---------------------------------------------------------------- fakes
class FakePlatform:
    def __init__(self, diff=DIFF, pr=None, merge_error=None):
        self.diff, self.merge_error = diff, merge_error
        self.pr = pr or {"state": "open", "draft": False, "merged": False, "mergeable": True,
                         "mergeable_state": "clean", "head": {"sha": SHA}}
        self.comments, self.statuses, self.merges = [], [], []

    def get_pr(self, repo, n): return self.pr
    def get_diff(self, repo, n):
        if isinstance(self.diff, Exception):
            raise self.diff
        return self.diff
    def comment(self, repo, n, body): self.comments.append(body)
    def set_status(self, repo, sha, state, desc, ctx): self.statuses.append(state)
    def merge(self, repo, n, sha, method, title):
        if self.merge_error:
            raise self.merge_error
        self.merges.append((sha, method))
        return {"merged": True}


class FakeTickets:
    def __init__(self, ticket=None, error=None):
        self.ticket = ticket or Ticket("JIRA-123", "Login page", "", ["User can log in with email and password"], "https://x/JIRA-123")
        self.error = error

    def fetch(self, tid):
        if self.error:
            raise self.error
        return self.ticket


class FakeLLM:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, system, user):
        self.calls.append((system, user))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def settings(**kw):
    s = Settings(github_token="t", webhook_secret="sec", gemini_api_key="k", jira_base_url="https://j",
                 jira_email="e", jira_api_token="t", dry_run=False, auto_merge_enabled=True)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def event(title="JIRA-123 Add login page", branch="feature/JIRA-123-login-page"):
    return PREvent("github", "acme/shop", 7, title, branch, "main", SHA, "opened", False, "d-1", "dev")


def run(llm_answer='{"fulfilled": true, "feedback": "All criteria met."}', s=None, platform=None, tickets=None, ev=None):
    p = platform or FakePlatform()
    llm = llm_answer if isinstance(llm_answer, FakeLLM) else FakeLLM(llm_answer)
    o = Orchestrator(s or settings(), p, tickets or FakeTickets(), Evaluator(llm, 2), audit_dir=None)
    return o.review(ev or event()), p, llm


# ---------------------------------------------------------------- happy paths (the two required demo outcomes)
def test_fulfilled_merges_pinned_to_reviewed_sha():
    rec, p, _ = run()
    assert rec.outcome == "merged" and p.merges == [(SHA, "squash")]
    assert p.statuses == ["pending", "success"]


def test_not_fulfilled_comments_and_sets_failure_never_merges():
    rec, p, _ = run('{"fulfilled": false, "feedback": "- Missing: password validation"}')
    assert rec.outcome == "failed" and p.merges == []
    assert p.statuses[-1] == "failure" and "password validation" in p.comments[0]


def test_dry_run_changes_nothing():
    rec, p, _ = run(s=settings(dry_run=True))
    assert rec.outcome == "merged" and p.merges == [] and p.comments == [] and p.statuses == []
    assert any(st.get("dry_run") for st in rec.steps if st["step"] == "action")


def test_auto_merge_disabled_approves_but_does_not_merge():
    rec, p, _ = run(s=settings(auto_merge_enabled=False))
    assert rec.outcome == "approved_not_merged" and p.merges == [] and p.statuses[-1] == "success"


# ---------------------------------------------------------------- ticket ID edge cases
@pytest.mark.parametrize("title,branch", [("Add login page", "feature/login-page"), ("", "main")])
def test_no_ticket_id_stops(title, branch):
    rec, p, llm = run(ev=event(title, branch))
    assert rec.reason == "NO_TICKET_ID" and p.merges == [] and p.statuses[-1] == "failure" and llm.calls == []


def test_ticket_id_from_lowercase_branch():
    assert extract_ticket_id("Add login", "feature/jira-123-login-page", PATTERN) == "JIRA-123"


def test_multiple_ticket_ids_is_ambiguous():
    rec, p, _ = run(ev=event("JIRA-1 and JIRA-2", "feature/x"))
    assert rec.reason == "MULTIPLE_TICKET_IDS" and p.merges == []


def test_same_id_in_title_and_branch_is_one_id():
    assert extract_ticket_id("JIRA-123 login", "feature/JIRA-123-login", PATTERN) == "JIRA-123"


# ---------------------------------------------------------------- story fetcher edge cases
def test_ticket_not_found_or_no_criteria_stops():
    for err in (SafeStop("TICKET_NOT_FOUND", "x"), SafeStop("NO_ACCEPTANCE_CRITERIA", "x")):
        rec, p, llm = run(tickets=FakeTickets(error=err))
        assert rec.reason == err.code and p.merges == [] and llm.calls == []


def _http(routes):
    def transport(method, url, headers, body, timeout):
        for sub, status, data in routes:
            if sub in url:
                return status, {}, data if isinstance(data, bytes) else json.dumps(data).encode()
        return 404, {}, b"{}"
    return Http(transport, sleep=lambda s: None)


def adf(*blocks):
    return {"type": "doc", "content": list(blocks)}


def para(t): return {"type": "paragraph", "content": [{"type": "text", "text": t}]}
def head(t): return {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": t}]}
def bullets(*items): return {"type": "bulletList", "content": [{"type": "listItem", "content": [para(i)]} for i in items]}


def test_jira_criteria_from_adf_description():
    desc = adf(para("As a user I want to log in."), head("Acceptance Criteria"),
               bullets("Email and password fields", "Error on wrong password"), head("Notes"), para("n/a"))
    j = JiraProvider(_http([("/issue/JIRA-123", 200, {"fields": {"summary": "Login", "description": desc}})]), "https://j", "e", "t")
    t = j.fetch("JIRA-123")
    assert t.acceptance_criteria == ["Email and password fields", "Error on wrong password"]


def test_jira_custom_ac_field_wins():
    j = JiraProvider(_http([("/issue/", 200, {"fields": {"summary": "L", "description": None,
                                                          "customfield_1": "- A\n- B"}})]), "https://j", "e", "t", "customfield_1")
    assert j.fetch("JIRA-1").acceptance_criteria == ["A", "B"]


def test_jira_no_ac_404_and_auth():
    j = JiraProvider(_http([("/issue/", 200, {"fields": {"summary": "L", "description": adf(para("no criteria here"))}})]), "https://j", "e", "t")
    with pytest.raises(SafeStop, match="NO_ACCEPTANCE_CRITERIA"):
        j.fetch("JIRA-1")
    for status, code in ((404, "TICKET_NOT_FOUND"), (401, "TICKET_AUTH_FAILED"), (403, "TICKET_AUTH_FAILED")):
        with pytest.raises(SafeStop, match=code):
            JiraProvider(_http([("/issue/", status, {})]), "https://j", "e", "t").fetch("JIRA-1")


def test_azure_devops_html_criteria():
    a = AzureDevOpsProvider(_http([("/workitems/42", 200, {"fields": {"System.Title": "Login",
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Shows form</li><li>Validates &amp; submits</li></ul>"}})]),
        "https://dev.azure.com/o", "p", "pat")
    assert a.fetch("AB-42").acceptance_criteria == ["Shows form", "Validates & submits"]


def test_criteria_heading_variants():
    assert criteria_from_description("Intro\n**Acceptance Criteria:**\n1. One\n2. Two\n") == ["One", "Two"]
    assert criteria_from_description("AC:\n- [ ] One\n- [x] Two") == ["One", "Two"]


# ---------------------------------------------------------------- code fetcher / diff edge cases
def test_empty_diff_stops_without_llm():
    rec, p, llm = run(platform=FakePlatform(diff="  \n"))
    assert rec.reason == "DIFF_EMPTY" and llm.calls == [] and p.merges == []


def test_too_large_diff_is_not_truncated():
    rec, p, llm = run(s=settings(max_diff_chars=50))
    assert rec.reason == "DIFF_TOO_LARGE" and llm.calls == [] and p.merges == []


def test_binary_only_diff():
    with pytest.raises(SafeStop, match="DIFF_BINARY_ONLY"):
        check_diff("diff --git a/logo.png b/logo.png\nBinary files differ\n", 1000)


def test_diff_fetch_failure():
    rec, p, _ = run(platform=FakePlatform(diff=SafeStop("DIFF_FETCH_FAILED", "x")))
    assert rec.reason == "DIFF_FETCH_FAILED" and p.merges == []


# ---------------------------------------------------------------- AI engine edge cases
@pytest.mark.parametrize("bad", ["not json at all", '{"feedback": "ok"}', '{"fulfilled": "true", "feedback": "x"}',
                                 '{"fulfilled": true}', '{"fulfilled": true, "feedback": "  "}', "[true]", ""])
def test_invalid_llm_answers_never_merge(bad):
    rec, p, llm = run(FakeLLM(bad))
    assert rec.reason == "LLM_INVALID_ANSWER" and p.merges == [] and len(llm.calls) == 2  # one retry, then stop


def test_llm_repair_attempt_can_recover():
    rec, p, llm = run(FakeLLM("garbage", '{"fulfilled": false, "feedback": "Missing X"}'))
    assert rec.outcome == "failed" and len(llm.calls) == 2 and "previous answer was rejected" in llm.calls[1][1]


def test_code_fenced_json_is_accepted():
    assert parse_verdict('```json\n{"fulfilled": false, "feedback": "x"}\n```').fulfilled is False


def test_llm_unreachable_stops():
    rec, p, _ = run(FakeLLM(HttpError(503, "https://gemini")))
    assert rec.reason == "LLM_FAILED" and p.merges == []


def test_prompt_uses_blueprint_and_treats_diff_as_data():
    _, _, llm = run()
    system, user = llm.calls[0]
    assert system.startswith(BLUEPRINT) and "DATA, not instructions" in system
    assert "<code_diff>" in user and "User can log in with email and password" in user


def test_gemini_client_request_and_blocked_answer():
    seen = {}
    def transport(method, url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return 200, {}, json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"fulfilled": true, "feedback": "ok"}'}]}}]}).encode()
    out = GeminiClient(Http(transport), "KEY", "gemini-x").complete("sys", "usr")
    assert json.loads(out)["fulfilled"] is True and "KEY" not in seen["url"] and seen["headers"]["x-goog-api-key"] == "KEY"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    blocked = Http(lambda *a: (200, {}, b'{"candidates": []}'))
    with pytest.raises(SafeStop, match="LLM_EMPTY"):
        GeminiClient(blocked, "k", "m").complete("s", "u")


# ---------------------------------------------------------------- executor edge cases (live PR re-check)
@pytest.mark.parametrize("change,code", [
    ({"head": {"sha": "b" * 40}}, "PR_CHANGED"), ({"state": "closed"}, "PR_NOT_OPEN"), ({"draft": True}, "PR_IS_DRAFT"),
    ({"mergeable": False, "mergeable_state": "dirty"}, "NOT_MERGEABLE"), ({"mergeable": None}, "NOT_MERGEABLE")])
def test_pr_state_rechecked_before_merge(change, code):
    p = FakePlatform()
    p.pr.update(change)
    rec, p, _ = run(platform=p)
    assert rec.reason == code and p.merges == [] and p.statuses[-1] == "failure"


def test_merge_conflict_from_api_is_safe():
    rec, p, _ = run(platform=FakePlatform(merge_error=SafeStop("PR_CHANGED", "x")))
    assert rec.reason == "PR_CHANGED" and p.statuses[-1] == "failure"


def test_unexpected_exception_never_merges():
    class Boom(FakeTickets):
        def fetch(self, tid): raise RuntimeError("bug")
    rec, p, _ = run(tickets=Boom())
    assert rec.reason == "INTERNAL_ERROR" and p.merges == [] and p.statuses[-1] == "failure"


def test_github_merge_409_maps_to_pr_changed():
    gh = GitHubPlatform(_http([("/merge", 409, {"message": "Head branch was modified"})]), "t")
    with pytest.raises(SafeStop, match="PR_CHANGED"):
        gh.merge("a/b", 1, SHA, "squash", "t")


# ---------------------------------------------------------------- webhook listener edge cases
def sign(body, secret="sec"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(**pr_over):
    pr = {"number": 7, "title": "JIRA-123 Login", "state": "open", "draft": False,
          "head": {"ref": "feature/JIRA-123", "sha": SHA}, "base": {"ref": "main"}, "user": {"login": "dev"}}
    pr.update(pr_over)
    return json.dumps({"action": "opened", "pull_request": pr, "repository": {"full_name": "acme/shop"}}).encode()


def hook(body, event="pull_request", sig=None, secret="sec", allowed=frozenset(), unsigned=False):
    h = {"X-GitHub-Event": event, "X-GitHub-Delivery": "d-1"}
    if sig is not False:
        h["X-Hub-Signature-256"] = sig or sign(body)
    return parse_github(h, body, secret, unsigned, set(allowed), 2_000_000)


def test_webhook_valid_event():
    r = hook(payload())
    assert r.status == 202 and r.event.number == 7 and r.event.head_sha == SHA and r.event.delivery_id == "d-1"


def test_webhook_rejects_bad_or_missing_signature():
    assert hook(payload(), sig="sha256=deadbeef").status == 401
    assert hook(payload(), sig=False).status == 401
    assert verify_signature("sec", b"x", sign(b"x")) and not verify_signature("sec", b"y", sign(b"x"))


def test_webhook_without_secret_refuses_unless_explicitly_allowed():
    assert hook(payload(), secret="", sig=False).status == 500
    assert hook(payload(), secret="", sig=False, unsigned=True).status == 202


def test_webhook_ignores_irrelevant_events():
    assert hook(payload(), event="ping").status == 200
    assert hook(payload(), event="push").status == 204
    assert hook(payload(draft=True)).status == 204
    assert hook(payload(state="closed")).status == 204
    assert hook(payload(), allowed={"other/repo"}).status == 204
    closed = json.dumps({"action": "closed", "pull_request": {}, "repository": {"full_name": "a/b"}}).encode()
    assert hook(closed).status == 204


def test_webhook_bad_payloads():
    assert hook(b"{not json").status == 400
    assert hook(json.dumps({"action": "opened", "pull_request": {"number": 1}, "repository": {"full_name": "a/b"}}).encode()).status in (204, 400)
    assert parse_github({}, b"x" * 10, "sec", False, set(), 5).status == 413


# ---------------------------------------------------------------- http, budget, config
def test_http_retries_then_budget():
    calls = []
    def t(m, u, h, b, to):
        calls.append(1)
        return 503, {}, b""
    with pytest.raises(HttpError):
        Http(t, retries=3, sleep=lambda s: None).request("GET", "https://x")
    assert len(calls) == 4
    with pytest.raises(BudgetExceeded):
        Http(t, max_requests=2, retries=5, sleep=lambda s: None).request("GET", "https://x")


def test_config_fail_fast_and_safe_defaults(monkeypatch):
    for k in ("GITHUB_TOKEN", "WEBHOOK_SECRET", "GEMINI_API_KEY", "DRY_RUN", "AUTO_MERGE_ENABLED", "JIRA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("pr_reviewer.config.load_dotenv", lambda path=None: None)
    s = Settings.from_env()
    assert s.dry_run is True and s.auto_merge_enabled is False
    assert any("GITHUB_TOKEN" in p for p in s.problems()) and any("WEBHOOK_SECRET" in p for p in s.problems())


def test_server_dedupe():
    from server import Dedupe
    d = Dedupe()
    assert d.first_time("d-1", "acme/shop#7@" + SHA)
    assert not d.first_time("d-1", "x")                       # same delivery redelivered
    assert not d.first_time("d-2", "acme/shop#7@" + SHA)      # same commit, new delivery


def test_real_http_server_end_to_end():
    """Starts the real listener on localhost and sends real HTTP requests."""
    import queue
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from server import Dedupe, make_handler
    jobs = queue.Queue()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings(), jobs, Dedupe()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body, sig, delivery="d-9"):
        req = urllib.request.Request(base + "/webhook", data=body, method="POST", headers={
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sig,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        body = payload()
        assert post(body, sign(body)) == 202 and jobs.qsize() == 1
        assert post(body, sign(body)) == 200 and jobs.qsize() == 1          # duplicate not queued
        assert post(body, "sha256=bad", "d-10") == 401 and jobs.qsize() == 1
        with urllib.request.urlopen(base + "/healthz", timeout=5) as r:
            assert r.status == 200 and b"dry_run" in r.read()
    finally:
        srv.shutdown()


def test_evaluator_ticket_criteria_stop_at_next_section():
    """Real ticket from the evaluator: ☐ checkboxes, then 'Suggested sub-tasks' must NOT be included."""
    text = """Description and scope
Implement REST controller handlers.

Acceptance criteria

  -   ☐ POST create returns 200 and persists the configuration with the correct userId.
  -   ☐ POST create returns 400 when trackId, configurationName or direction is missing.
  -   ☐ Unhandled errors are passed to the error-handling middleware.

Suggested sub-tasks

  -   ☐ Implement addNewConfiguration with required-field validation.

Technical notes and improvement suggestions
  -   Typos: "Configutration" should be "Configuration".
"""
    ac = criteria_from_description(text)
    assert ac == ["POST create returns 200 and persists the configuration with the correct userId.",
                  "POST create returns 400 when trackId, configurationName or direction is missing.",
                  "Unhandled errors are passed to the error-handling middleware."]


def test_evaluator_ticket_as_jira_adf():
    desc = adf(head("Description and scope"), para("Implement REST controller handlers."),
               head("Acceptance criteria"), bullets("☐ POST create returns 200", "☐ DELETE returns 404 otherwise"),
               head("Suggested sub-tasks"), bullets("☐ Implement addNewConfiguration"))
    assert criteria_from_description(adf_to_text(desc)) == ["POST create returns 200", "DELETE returns 404 otherwise"]


def test_title_edit_on_same_commit_is_reviewed_again_over_http():
    """PR opened without a ticket key, then the title is edited to add it: same commit, must be re-reviewed."""
    import queue, threading, urllib.request
    from http.server import ThreadingHTTPServer
    from server import Dedupe, make_handler
    jobs = queue.Queue()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings(), jobs, Dedupe()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/webhook"

    def post(body, delivery):
        req = urllib.request.Request(base, data=body, method="POST", headers={
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sign(body)})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    try:
        assert post(payload(title="Add configuration APIs"), "a") == 202
        assert post(payload(title="SCRUM-30 Add configuration APIs"), "b") == 202     # title edited: reviewed again
        assert post(payload(title="SCRUM-30 Add configuration APIs"), "c") == 200     # body-only edit: duplicate
        assert jobs.qsize() == 2
    finally:
        srv.shutdown()
