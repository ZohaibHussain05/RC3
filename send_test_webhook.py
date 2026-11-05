"""Send a signed, GitHub-style pull_request webhook to a running local server (for testing).

    python send_test_webhook.py owner/repo 12 "JIRA-123 Add login page" feature/JIRA-123-login <head_sha>
"""
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request
import uuid

from pr_reviewer.config import Settings


def main(a):
    if len(a) != 5:
        print(__doc__)
        return 2
    repo, num, title, branch, sha = a
    s = Settings.from_env()
    body = json.dumps({"action": "opened", "repository": {"full_name": repo}, "pull_request": {
        "number": int(num), "title": title, "state": "open", "draft": False,
        "head": {"ref": branch, "sha": sha}, "base": {"ref": "main"}, "user": {"login": "tester"}}}).encode()
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": str(uuid.uuid4()), "Content-Type": "application/json"}
    if s.webhook_secret:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(s.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"http://127.0.0.1:{s.port}/webhook", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(r.status, r.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
