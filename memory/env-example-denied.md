---
name: env-example-denied
description: ".env.example in rag-mcp: Read gives \"File is in a directory that is denied by your permission settings\"; any Bash command touching it is denied whole"
metadata:
  type: reference
---

Permission settings block every `.env*` file, including `rag-mcp/.env.example`. A Bash command that only reads it (`sed -n … .env.example`) gets the whole command denied, including unrelated edits chained after it.

**Why:** 2026-10-15, PR #2: a README/CI edit script failed because it started with `sed -n 15,22p .env.example`. Fix left to the user as board task T-006.

**How to apply:** never chain `.env.example` with other work in one Bash call; ask the user to edit it or to allow access.
