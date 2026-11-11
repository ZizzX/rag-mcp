---
name: eval-real-notes-2026-11
description: "2026-11-09 eval of rag-mcp on 141 real ATS notes — nomic hybrid loses to grep, bge-m3 hybrid wins; where the questions and scripts live"
metadata:
  node_type: memory
  type: project
  originSessionId: dae1915d-9a40-42cb-a959-b6f168c3a6fc
  modified: 2026-11-11T14:02:20.217Z
---

On 2026-11-09 we measured rag-mcp on real notes (`~/.gstack/projects/apps-ats`, 141 Markdown files, Russian + English) with 20 gold questions. hit@5 over all 20: grep script 0.75, BM25 only 0.80, nomic hybrid 0.65 (the current default), bge-m3 hybrid 0.85. On paraphrase questions: grep 0.50, BM25 0.70, nomic hybrid 0.50, bge-m3 hybrid 0.80.

Also found: rag-mcp sends no `search_query:` / `search_document:` prefixes to nomic, and `rag-mcp eval` crashes when Ollama is down.

**Why:** the user asked whether the plugin is worth continuing. The answer depends on real use, and this was the first measurement on a real corpus.

**How to apply:**
- Don't defend the nomic default. Moving to bge-m3 is the measured fix.
- Questions are in `/Users/a.isapov/rag-mcp/eval/questions.jsonl` (gitignored).
- Scripts and the full table are in `~/Library/Application Support/Claude/side-session-notes/local_16199423-a3f5-4165-b747-821263298d23/rag-eval-2026-11/`.
- The planned next step is a week of real use, then counting `mcp__rag-mcp__search` calls in transcripts.

**Week of real use (started 2026-11-11, check around 2026-11-18):**
- The live checkout `/Users/a.isapov/rag-mcp` was pulled to 496d7d0 and its index rebuilt on bge-m3: 144 docs, 4485 chunks, project `ats` = `~/.gstack/projects/apps-ats`. `rag-mcp eval` on it gives hit@5 0.85, MRR 0.69.
- 2026-11-11: registered in Claude Code at user scope: `claude mcp add -s user rag-mcp -e RAG_EMBED_MODEL=bge-m3 -- uv --directory /Users/a.isapov/rag-mcp run rag-mcp serve`. `claude mcp get rag-mcp` shows Connected, and a stdio `search` call returned the right file with both dense and bm25 matching. Sessions opened before the registration don't see it.
- `.env` there still has no `RAG_EMBED_MODEL=bge-m3`, because a worktree hook blocks edits to the base checkout. The MCP server gets the model through `-e`, but CLI runs without that env var fail with "Index has dim 1024, embedder produces 768".
- Verdict criteria:
  - Count `mcp__rag-mcp__search` calls in `~/.claude/projects/*/*.jsonl` newer than 2026-11-11.
  - Check whether their results were actually used in the answer.
  - Few calls, or results ignored, means freeze the plugin.
