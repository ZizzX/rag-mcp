# rag-mcp

<!-- One board per git repository, shared by every worktree and every direction of work. -->
## Goal

Локальный гибридный RAG (Markdown/PDF) как MCP-сервер, который работает сразу в нескольких сессиях Claude Code и не требует ручной возни с индексом.

## Milestones

- M1 · Несколько сессий: любая сессия ищет сразу, ingest не конфликтует с запущенным сервером, ошибки понятные.
- M2 · Качество поиска (реранкер, переписывание запроса, трассировка) — отложено до M1.

## Current focus

- multi-session: хранилище без блокировки (T-002) в работе в ворктри reverent-dijkstra; PR #1 с понятной ошибкой ждёт CI.

## Changelog
- 2026-10-15 · board created
- 2026-10-15 · цель, вехи и задачи M1 из аудита rag-mcp-next-plan.md · координация нескольких агентов по ворктри
