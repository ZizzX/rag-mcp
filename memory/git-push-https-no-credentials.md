---
name: git-push-https-no-credentials
description: "\"fatal: could not read Username for 'https://github.com': Device not configured\" on git push in rag-mcp — origin is https, auth is ssh only"
metadata:
  node_type: memory
  type: reference
  originSessionId: e5a5ce9b-300d-4d2a-a2cb-c7ea12f5167c
  modified: 2026-10-15T11:27:36.222Z
---

`git push` in rag-mcp fails with `could not read Username for 'https://github.com'`: origin is `https://github.com/ZizzX/rag-mcp.git`, but `gh auth status` shows git protocol `ssh` and no https credential helper.

**Why:** seen 2026-10-15 pushing branch claude/gallant-kapitsa-aee0bc (PR ZizzX/rag-mcp#1).

**How to apply:** push straight to the ssh URL without touching git config: `git push -u git@github.com:ZizzX/rag-mcp.git <branch>`, then `gh pr create --repo ZizzX/rag-mcp --head <branch>`. Do not run `gh auth setup-git` or rewrite the remote without asking.
