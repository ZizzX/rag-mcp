# rag-mcp

[![CI](https://github.com/ZizzX/rag-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ZizzX/rag-mcp/actions/workflows/ci.yml)

Локальный RAG по вашим Markdown и PDF, доступный любому агенту через MCP
(Claude Code, Claude Desktop, Cursor). Гибридный поиск: векторный (Qdrant) +
BM25, слияние через RRF. Эмбеддинги через Ollama по умолчанию, без облака.

Работает на Windows, macOS и Linux. Один и тот же репозиторий на всех машинах,
установка одной командой.

## Быстрый старт

```bash
git clone https://github.com/ZizzX/rag-mcp.git && cd rag-mcp
./install.sh          # macOS / Linux
.\install.ps1         # Windows (PowerShell)
```

Установщик ставит `uv` (он сам скачает нужный Python), зависимости из
`uv.lock`, создаёт `.env`, тянет модель эмбеддингов в Ollama и регистрирует
MCP-сервер в Claude Code. Повторный запуск безопасен: после `git pull`
просто запустите его ещё раз.

Дальше:

```bash
uv run rag-mcp ingest                     # проиндексировать data/docs (инкрементально)
uv run rag-mcp search "как откатить деплой"
uv run rag-mcp status
```

В Claude Code наберите `/mcp`: сервер `rag-mcp` должен быть в списке. После
этого просто задавайте вопросы по документам, Claude сам вызовет `search`.

## Требования

- [Ollama](https://ollama.com) запущен (для эмбеддингов). Модель по умолчанию
  `nomic-embed-text`; для русского лучше `bge-m3`: `ollama pull bge-m3` и
  `RAG_EMBED_MODEL=bge-m3` в `.env`.
- Docker Desktop не обязателен. Qdrant по умолчанию работает встроенно
  (папка `data/index`). Сервер Qdrant нужен для больших корпусов или если
  индекс читают несколько процессов: `docker compose up -d` и
  `RAG_QDRANT_URL=http://localhost:6333`.
  Встроенный индекс открывает только один процесс: пока запущен MCP-сервер
  (`rag-mcp serve`), `rag-mcp ingest` сообщит, что индекс заблокирован.

## Команды

| Команда | Что делает |
|---|---|
| `rag-mcp ingest [--rebuild]` | индексирует новые и изменённые файлы, удаляет пропавшие; `--rebuild` пересоздаёт индекс (нужно после смены модели эмбеддингов) |
| `rag-mcp search "вопрос" [-k N] [--source prefix] [--json]` | поиск из терминала |
| `rag-mcp serve` | MCP-сервер по stdio (его запускает Claude Code) |
| `rag-mcp status` | где документы и индекс, какой эмбеддер, сколько чанков |
| `rag-mcp eval [eval/questions.jsonl] [-k N]` | hit-rate и MRR по золотому набору вопросов |

Инструменты MCP: `search`, `fetch_document`, `list_sources`, `index_status`.

## Конфигурация

Все настройки в `.env` (см. `.env.example`), префикс `RAG_`. Пути относительные
к корню проекта, поэтому сервер работает из любой директории.

## Документы: как переиспользовать на нескольких устройствах

Репозиторий содержит только код и установщик. Документы и индекс в git не
попадают (`data/docs`, `data/index` в `.gitignore`). Варианты синхронизации
документов между машинами, от простого к сложному:

1. **Синхронизируемая папка.** Dropbox, OneDrive, iCloud, Syncthing. В `.env`
   укажите `RAG_DOCS_DIR=/path/to/synced/folder`. Ничего больше не нужно.
2. **Отдельный приватный репозиторий `rag-docs`.** Клонируйте его в
   `data/docs` (или куда угодно и укажите `RAG_DOCS_DIR`). Код публичный,
   документы приватные, история правок есть у обоих.
3. **Один репозиторий, документы как git submodule** в `data/docs`. Удобно,
   если хочется клонировать всё одной командой, но submodule добавляет
   трения при обновлениях.

Индекс всегда локальный: он привязан к модели эмбеддингов и занимает место.
После клонирования на новой машине выполните `uv run rag-mcp ingest`, это
занимает минуты даже для тысяч страниц.

### Делить ли на несколько репозиториев

Код для Windows, macOS и Linux одинаковый, различаются только установщики
(`install.sh` и `install.ps1`), поэтому делить код по платформам не нужно.
Разумная граница одна: **код** (этот репозиторий) и **данные** (документы,
вариант 1 или 2 выше). Если позже появятся сильно разные пайплайны, например
отдельный для кода и отдельный для документов, их проще держать как разные
конфиги `.env` и разные коллекции (`RAG_COLLECTION`), а не как разные репозитории.

## Ручная регистрация MCP (другие клиенты)

Claude Desktop (`claude_desktop_config.json`) или Cursor:

```json
{
  "mcpServers": {
    "rag-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/rag-mcp", "run", "rag-mcp", "serve"]
    }
  }
}
```

На Windows путь вида `C:\\Users\\you\\rag-mcp`.

## Разработка

CI (GitHub Actions) гоняет тесты, установщики и офлайн-прогон CLI и MCP-сервера
на Ubuntu, macOS и Windows при каждом пуше в `main` и в pull request.

```bash
uv sync --extra dev
uv run pytest
RAG_EMBED_PROVIDER=hash RAG_DOCS_DIR=examples/docs uv run rag-mcp ingest   # офлайн-прогон без Ollama
```

Структура:

```
src/rag_mcp/
  config.py     настройки (.env, префикс RAG_)
  loaders.py    Markdown и PDF (PyMuPDF) -> Document
  chunking.py   разбиение по заголовкам, затем окна с перекрытием
  embeddings.py Ollama / OpenAI-совместимый API / hash (тесты)
  store.py      Qdrant (embedded или сервер) + BM25 + RRF
  pipeline.py   инкрементальный ингест и поиск
  server.py     MCP-сервер (stdio)
  evaluate.py   hit-rate и MRR по eval/questions.jsonl
  cli.py        команды
```

## Что дальше

- Реранкер (cross-encoder через API или `bge-reranker` локально) поверх RRF.
- Переписывание запроса и разложение на подвопросы для многошаговых вопросов.
- Трассировка: лог того, какие чанки реально попали в контекст ответа.
