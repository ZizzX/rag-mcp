---
name: eval-real-notes-2026-11
description: "2026-11-09 eval of rag-mcp on 141 real ATS notes — nomic hybrid loses to grep, bge-m3 hybrid wins; where the questions and scripts live"
metadata:
  node_type: memory
  type: project
  originSessionId: dae1915d-9a40-42cb-a959-b6f168c3a6fc
  modified: 2026-11-18T05:01:21.675Z
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
- 2026-11-11: the user added `RAG_EMBED_MODEL=bge-m3` to `/Users/a.isapov/rag-mcp/.env`, and CLI search there works without the env var. A worktree hook blocks agents from editing that `.env`, so the user changes it. If the line is missing, runs fail with "Index has dim 1024, embedder produces 768".
- Verdict criteria:
  - Count `mcp__rag-mcp__search` calls in `~/.claude/projects/*/*.jsonl` newer than 2026-11-11.
  - Check whether their results were actually used in the answer.
  - Few calls, or results ignored, means freeze the plugin.

**Week of real use — result (checked 2026-11-18, scheduled task rag-mcp-week-verdict):**
- 268 transcripts (108 main sessions, mostly edo and ats Orca worktrees) changed after 2026-11-11. Real `mcp__rag-mcp__*` tool calls: 1, successful: 0.
- The one call: 2026-11-13, edo-plankton, a read-only research subagent, `search("D-099 имена коллег маска PDP без кнопки раскрытия", all_projects)`. It failed with "No such tool available: mcp__rag-mcp__search" (the subagent type has only Read/Grep/Glob/Bash), and the agent went to grep.
- The tool was in the deferred list of almost every session, and the rule plus skill were in place from 2026-11-11 19:39 +05 (PR #11 merged 2026-11-11T14:44Z), so the whole week ran with the rule. No main agent ever loaded the tool through ToolSearch.
- In the same week agents looked up past context elsewhere: `plans.mjs find` 12 times, grep in `~/mystartups/plans` 14 times, `pm/` files 13 times. Nobody touched `~/.gstack/projects/apps-ats` (the only indexed source) outside rag-mcp's own sessions.
- Verdict by the criteria: freeze. My reading of the cause: the index covers a folder agents no longer consult (notes moved to `~/mystartups/plans` and pm boards), the tool is deferred, and subagents can't call it. If the user wants one more try before freezing: connect `~/mystartups/plans` as a project and repeat the count for a week.
