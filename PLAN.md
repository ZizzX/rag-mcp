# rag-mcp

<!-- One board per git repository, shared by every worktree and every direction of work. -->
## Goal

Локальный гибридный RAG (Markdown/PDF) как MCP-сервер, который работает сразу в нескольких сессиях Claude Code и не требует ручной возни с индексом.

## Milestones

- M1 · Несколько сессий: любая сессия ищет сразу, ingest не конфликтует с запущенным сервером, ошибки понятные.
- M2 · Качество поиска (реранкер, переписывание запроса, трассировка) — отложено до M1.

## Current focus

- multi-session: всё M1 в ревью — PR #1 (T-001) и PR #2 (T-002..T-004); оба трогают store.py, мёржить по очереди.

## Changelog
- 2026-10-15 · board created
- 2026-10-15 · цель, вехи и задачи M1 из аудита rag-mcp-next-plan.md · координация нескольких агентов по ворктри
