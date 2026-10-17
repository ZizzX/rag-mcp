---
name: squash-merge-stacked-branch
description: "rag-mcp PRs are squash-merged (\"обычный merge, как у #4\" = squash); a branch stacked on a merged PR branch then conflicts on `git rebase origin/main`"
metadata:
  node_type: memory
  type: project
  originSessionId: 08bc9086-39d8-4981-89f6-cfea7a8a525a
  modified: 2026-10-17T18:49:51.805Z
---

PRs in ZizzX/rag-mcp are merged with squash: the commit on main is one `<title> (#N)` with a single parent (#2, #4, #5, #6). When the user says "смёржи обычным merge, как у #4", that means `gh pr merge N --squash`.

**Why:** 2026-10-17: #6 (T-014, branch feat/project-sources) was squash-merged as a8bcc75 while the T-015 session already worked on a branch cut from feat/project-sources tip a6573af.

**How to apply:** check `git log --format='%h %p %s' -3 origin/main` before choosing a merge method. To move a stacked branch onto main after its base was squash-merged: `git fetch git@github.com:ZizzX/rag-mcp.git main:refs/remotes/origin/main && git rebase --onto origin/main <old base tip>` — a plain `git rebase origin/main` replays the base's commits and conflicts with their squash copy.
