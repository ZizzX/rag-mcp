---
name: eval-real-notes-2026-11
description: "2026-11-09 eval of rag-mcp on 141 real ATS notes — nomic hybrid loses to grep, bge-m3 hybrid wins; where the questions and scripts live"
metadata:
  node_type: memory
  type: project
  originSessionId: dae1915d-9a40-42cb-a959-b6f168c3a6fc
  modified: 2026-11-09T10:11:49.708Z
---

On 2026-11-09 we measured rag-mcp on real notes (`~/.gstack/projects/apps-ats`, 141 Markdown files, Russian + English) with 20 gold questions. hit@5 over all 20: grep script 0.75, BM25 only 0.80, nomic hybrid 0.65 (the current default), bge-m3 hybrid 0.85. On paraphrase questions: grep 0.50, BM25 0.70, nomic hybrid 0.50, bge-m3 hybrid 0.80.

Also found: rag-mcp sends no `search_query:` / `search_document:` prefixes to nomic, and `rag-mcp eval` crashes when Ollama is down.

**Why:** the user asked whether the plugin is worth continuing. The answer depends on real use, and this was the first measurement on a real corpus.

**How to apply:**
- Don't defend the nomic default. Moving to bge-m3 is the measured fix.
- Questions are in `/Users/a.isapov/rag-mcp/eval/questions.jsonl` (gitignored).
- Scripts and the full table are in `~/Library/Application Support/Claude/side-session-notes/local_16199423-a3f5-4165-b747-821263298d23/rag-eval-2026-11/`.
- The planned next step is a week of real use, then counting `mcp__rag-mcp__search` calls in transcripts.
