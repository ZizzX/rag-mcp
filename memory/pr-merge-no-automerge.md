---
name: pr-merge-no-automerge
description: "ZizzX/rag-mcp has GitHub auto-merge disabled; \"merge after CI\" = background gh pr checks --watch then gh pr merge --squash, approval is per PR"
metadata:
  node_type: memory
  type: feedback
  originSessionId: dae1915d-9a40-42cb-a959-b6f168c3a6fc
  modified: 2026-11-11T14:51:44.851Z
---

GitHub auto-merge is turned off in ZizzX/rag-mcp, so `set_auto_merge` fails with "Auto merge is not allowed for this repository".

**Why:** on 2026-11-11 the user said «вливай после CI», enabling auto-merge was refused, and the merge had to be done another way.

**How to apply:**
- To merge after CI, run one background command: `gh pr checks N --watch --fail-fast`, then `gh pr merge N --squash --delete-branch`. Run it outside the sandbox, because of the gh TLS error (see [[sandbox-gh-tls]]).
- Approval covers one PR at a time. The auto-mode classifier denied merging #9 on the strength of the approval given for #8.
- Squash merge is the rule here; see [[squash-merge-stacked-branch]].
