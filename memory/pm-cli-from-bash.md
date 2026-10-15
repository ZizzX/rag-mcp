---
name: pm-cli-from-bash
description: "pm board CLI in Bash tool: \"(eval):1: no such file or directory: node …pm.mjs\" or \"defining function based on alias pm\" — pm is a zsh alias"
metadata:
  type: reference
---

`pm` is a zsh alias for `node /Users/a.isapov/.claude/plugins/marketplaces/project-memory/scripts/pm.mjs`. In the Bash tool, `PM="node …"; $PM claim T-1` fails (zsh does not word-split the variable) and `pm() {…}` fails because the alias already exists.

**Why:** hit 2026-10-15 claiming T-002..T-004 from worktree reverent-dijkstra.

**How to apply:** `P=/Users/a.isapov/.claude/plugins/marketplaces/project-memory/scripts/pm.mjs; node $P <command>`.
