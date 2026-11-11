---
name: register-clients-choice
description: "`rag-mcp register` / install.sh asks about Cursor and Claude Desktop: user answered n to Cursor (2026-11-11); never register Cursor without asking"
metadata:
  node_type: memory
  type: user
  originSessionId: 23ee973c-8767-4693-a6a2-8bc605de9e8d
  modified: 2026-11-11T14:49:07.282Z
---

On this Mac the user wants rag-mcp only in Claude Code. Cursor: declined in the interactive `register` run on 2026-11-11. Claude Desktop: not registered (it overwrote its config while running).

**Why:** 2026-11-11, after PR #11 I ran `register --cursor` on my own and had to undo it with `unregister --cursor` plus deleting a leftover `~/.cursor/mcp.json`.

**How to apply:** run `register --claude-code` (or `--dry-run`) here, never bare `--yes` / `--cursor`; for Desktop ask first, and quit the app before `register --claude-desktop`.
