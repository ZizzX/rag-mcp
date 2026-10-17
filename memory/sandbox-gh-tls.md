---
name: sandbox-gh-tls
description: "gh in the Bash sandbox: \"tls: failed to verify certificate: x509: OSStatus -26276\" on api.github.com — rerun the same gh command outside the sandbox"
metadata:
  node_type: memory
  type: reference
  originSessionId: 08bc9086-39d8-4981-89f6-cfea7a8a525a
  modified: 2026-10-17T18:49:51.586Z
---

With the Bash sandbox on, `gh pr view` / `gh api graphql` fail with `Post "https://api.github.com/graphql": tls: failed to verify certificate: x509: OSStatus -26276`. The filtering proxy's certificate is not trusted by gh (Go uses the macOS keychain). `git push` to the ssh URL kept working.

**Why:** 2026-10-17, checking and merging ZizzX/rag-mcp#6 from worktree reverent-dijkstra.

**How to apply:** rerun the exact gh command with `dangerouslyDisableSandbox: true` (it goes through the permission prompt) and mention `/sandbox` to the user; do not change git/gh TLS settings or export `GODEBUG`/`SSL_CERT_FILE` hacks.
