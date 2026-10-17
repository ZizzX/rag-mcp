---
name: pm-cli-from-bash
description: "pm board CLI in Bash tool: \"(eval):1: no such file or directory: node …pm.mjs\" or \"defining function based on alias pm\" — pm is a zsh alias"
metadata:
  node_type: memory
  type: reference
  originSessionId: 08bc9086-39d8-4981-89f6-cfea7a8a525a
  modified: 2026-10-17T18:50:05.324Z
---

`pm` is a zsh alias for `node /Users/a.isapov/.claude/plugins/marketplaces/project-memory/scripts/pm.mjs`. In the Bash tool, `PM="node …"; $PM claim T-1` fails (zsh does not word-split the variable) and `pm() {…}` fails because the alias already exists.

**Why:** hit 2026-10-15 claiming T-002..T-004 from worktree reverent-dijkstra.

**How to apply:** `P=/Users/a.isapov/.claude/plugins/marketplaces/project-memory/scripts/pm.mjs; node $P <command>` (the session summary prints the exact path, e.g. `…/plugins/cache/project-memory/project-memory/<ver>/scripts/pm.mjs`).

**Update 2026-10-16/17 (T-005..T-014):** hit the same `$PM` error twice more — read this file first. Also `pm …` run with cwd = the board folder (`~/.claude/projects/<key>/pm`) answers "no board here — run: pm init": run pm from the project/worktree folder. `pm sync` saying "offline — changes are committed locally" while a fetch works: push the board yourself with `git -C ~/.claude/projects/-Users-a-isapov-rag-mcp/pm push git@github.com:ZizzX/rag-mcp.git pm:pm`. A "sync conflict in tasks/T-NNN.md" that `pm sync` keeps reporting after you resolved it: `git -C <pm> merge origin/pm`, fix the file, commit, push over ssh as above — `pm sync` itself replays with `pull --rebase` and hits the old conflict again.
