# Demo setup (test repository and test Jira project only)

1. **Test repo:** create `pr-reviewer-test` on GitHub with a `main` branch containing one file.
   - To make the "Failed" status really block merging: go to Settings → Branches → add a
     rule for `main`, and require the status check `ai-pr-reviewer`.
2. **GitHub token:** create a fine-grained token for that repo only, with these permissions:
   Contents read/write, Pull requests read/write, Commit statuses read/write.
   Put it in `.env` as `GITHUB_TOKEN`.
3. **Jira:** use a free Jira Cloud site. Create an API token at
   id.atlassian.com → Security → API tokens. Create issue `KEY-1` whose description contains:
   ```
   Acceptance Criteria
   - Add a function add(a, b) in calc.py that returns a + b
   - Add a test for add()
   ```
4. **Run 1, ending in Failed:** create branch `feature/KEY-1-add`, commit a `calc.py` with
   `add()` but **no test**, open a PR, then run `python review_pr.py yourname/pr-reviewer-test <n>`.
   - With `DRY_RUN=false`, you get a comment listing the missing test and a red `ai-pr-reviewer` check.
5. **Run 2, ending in a merge:** add the test, push, and run the same command again with
   `AUTO_MERGE_ENABLED=true`. You get a green check and the PR merges.
6. **Optional, the real webhook:** run `python server.py`, expose it with a tunnel
   (e.g. `ngrok http 8080`), and add a webhook on the repo:
   - Payload URL `https://<tunnel>/webhook`, content type `application/json`;
   - secret = `WEBHOOK_SECRET`, event "Pull requests".

Screenshot both PRs, plus the console output, as the demo evidence.
