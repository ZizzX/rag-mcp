# T-014 · Реестр проектов и индексация подключённых папок — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rag-mcp add` регистрирует папку проекта, её документы индексируются на месте под префиксом `<имя>/`, `remove` и `projects` управляют реестром.

**Architecture:** Новый модуль `projects.py` держит реестр (`<index_dir>/projects.json`), git-хелперы, обход файлов и проверки `add`. `Pipeline` обходит пары `(path, source)` из `data/docs` и всех подключённых проектов; всё, что ниже `source` (doc_id, project, скоуп, хранилище), не меняется. CLI получает `add`, `remove`, `projects`.

**Tech Stack:** Python 3.12, stdlib (`subprocess`, `json`, `os.path`), pytest; git CLI (опционально).

**Spec:** `docs/superpowers/specs/2026-10-16-project-sources-design.md` — часть 1, CLI из части 3, ошибки и тесты части 5. Определение проекта (часть 2), MCP (часть 3) и маркер «← здесь» в `projects` — это T-015/T-016, не здесь.

## Global Constraints

- **Старт:** после мёржа PR #5 (T-008: `needs_ingest` по `stat`) — `git rebase origin/main` ветки `feat/project-sources`. План опирается на код T-008: `_stat(path)`, сравнение `entry.get("stat")`.
- Поддерживаемые файлы — `SUPPORTED_SUFFIXES` из `loaders.py` (`.md .markdown .txt .pdf`), скрытые сегменты пропускаются (`_hidden`).
- Реестр: `<index_dir>/projects.json`, запись через `store._atomic_write`; `store.reset()` его не трогает (уже так).
- Имя проекта: `^[A-Za-z0-9_-][A-Za-z0-9._-]*$`.
- Лимит файлов `add`: 5000 (после фильтра по типам); снимается `force=True`.
- Каталоги, пропускаемые без git: `node_modules .venv venv build dist target vendor __pycache__`.
- Таймаут каждого вызова git: 2 с; нет `git` в PATH или ошибка → `None`.
- Сравнение путей — `os.path.normcase(os.path.realpath(p))`.
- `write_lock()` не реентерабелен (flock на новом дескрипторе): `add`/`remove` берут его только на запись реестра и отпускают **до** `ingest()`.
- Тексты ошибок и вывод CLI — на английском, как весь код. Тесты, которым нужен git, пропускаются при `shutil.which("git") is None`.

## Review Focus

- Пути с пробелами и кириллицей (`docs/Спец ификация.md`) — `ls-files -z` и `source` с ними работают. → тест в Task 2.
- Файл удалён из рабочей копии, но ещё в git-индексе (`--cached` его отдаёт) — не ошибка, просто не индексируется. → тест в Task 2.
- `add` во время чужого ingest — понятный `IndexBusy`, реестр не тронут. → тест в Task 4.
- Переименование: `remove shop` + `add --name shop2` того же пути — старые чанки `shop/…` ушли, новые `shop2/…` есть. → тест в Task 4.
- Тот же каталог через симлинк (`/tmp/link` → `/tmp/repo`) — считается тем же путём: повторный `add` под другим именем отказ «already connected». → тест в Task 3.

---

## Файлы

| Файл | Что |
|---|---|
| Create `src/rag_mcp/projects.py` | `Project`, `ProjectError`, `RegistryError`, `normalize_origin`, `git`, `repo_root`, `git_common_dir`, `origin_of`, `load_projects`, `save_projects`, `list_files`, `resolve_project` |
| Modify `src/rag_mcp/loaders.py` | `load_document(path, source)` |
| Modify `src/rag_mcp/pipeline.py` | `SourceScan`, `Pipeline.scan()`, `_ingest`/`needs_ingest` по скану, `add_project`, `remove_project`, `projects_info`, `status` |
| Modify `src/rag_mcp/cli.py` | `add`, `remove`, `projects` |
| Modify `tests/conftest.py` | фикстура `make_repo` |
| Create `tests/test_project_sources.py` | тесты Task 1–5 |

---

### Task 1: Реестр и git-хелперы

**Files:** Create `src/rag_mcp/projects.py`; Modify `tests/conftest.py`; Test `tests/test_project_sources.py`

**Interfaces — Produces:**
```python
class ProjectError(ValueError): ...        # отказ add/remove, текст для пользователя
class RegistryError(RuntimeError): ...     # projects.json не читается

@dataclass
class Project:
    name: str
    path: Path            # абсолютный, resolve()
    origin: str | None    # normalize_origin(...)
    git_dir: Path | None  # абсолютный --git-common-dir
    added: str            # "YYYY-MM-DD"

def normalize_origin(url: str) -> str
def git(cwd: Path, *args: str) -> str | None          # stdout.strip() или None
def repo_root(path: Path) -> Path | None               # --show-toplevel
def git_common_dir(path: Path) -> Path | None          # абсолютный
def origin_of(path: Path) -> str | None                # normalize_origin(remote get-url origin)
def load_projects(index_dir: Path) -> dict[str, Project]   # {} если файла нет
def save_projects(index_dir: Path, projects: dict[str, Project]) -> None
```

- [ ] **Step 1: фикстура `make_repo` в `tests/conftest.py`**

`make_repo(root: Path, files: dict[str, str], origin: str | None = None, commit: bool = True) -> Path` — `git init -q`, пишет файлы (с родительскими папками, utf-8), при `origin` — `git remote add origin <url>`, при `commit` — `git add -A` + `git -c user.name=t -c user.email=t@t commit -qm init`. Возвращает `root`. Фикстура пропускает тест, если git нет.

- [ ] **Step 2: падающие тесты** (шапка файла: `import shutil, subprocess` · `from pathlib import Path` · `import pytest` · `from rag_mcp import cli, projects` · `from rag_mcp.projects import *` нужных имён · `from rag_mcp.store import IndexBusy`)

```python
@pytest.mark.parametrize("url", [
    "git@github.com:Acme/shop.git",
    "https://user@github.com/Acme/shop",
    "https://github.com/acme/shop.git/",
    "ssh://git@github.com:22/Acme/shop.git",
])
def test_normalize_origin(url):
    assert normalize_origin(url) == "github.com/acme/shop"

def test_normalize_origin_keeps_nested_groups():
    assert normalize_origin("git@gitlab.mycar.kz:myhr/frontend/apps/ats.git") == "gitlab.mycar.kz/myhr/frontend/apps/ats"

def test_registry_round_trip(tmp_path):
    p = Project("shop", tmp_path / "shop", "github.com/acme/shop", tmp_path / "shop/.git", "2026-10-16")
    save_projects(tmp_path, {"shop": p})
    assert load_projects(tmp_path) == {"shop": p}
    assert load_projects(tmp_path / "nothing-here") == {}

def test_corrupt_registry_raises_with_path(tmp_path):
    (tmp_path / "projects.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(RegistryError, match="projects.json"):
        load_projects(tmp_path)

def test_git_helpers(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"README.md": "# Shop"}, origin="git@github.com:Acme/shop.git")
    (repo / "docs").mkdir()
    assert repo_root(repo / "docs") == repo.resolve()
    assert git_common_dir(repo / "docs") == (repo / ".git").resolve()
    assert origin_of(repo) == "github.com/acme/shop"
    assert repo_root(tmp_path) is None and origin_of(tmp_path) is None
```

- [ ] **Step 3: прогнать — падают** · `uv run pytest tests/test_project_sources.py -q` → `ModuleNotFoundError: rag_mcp.projects`

- [ ] **Step 4: реализовать в `src/rag_mcp/projects.py`**

`git`: `subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", timeout=2)`; `None` при `FileNotFoundError`, `TimeoutExpired`, `returncode != 0`. `git_common_dir`: результат `rev-parse --git-common-dir` может быть относительным — `(cwd / out).resolve()`. `normalize_origin`: снять схему (`xxx://`), `user@`, порт (`:\d+/`), scp-форму `host:path` → `host/path`, хвостовые `/` и `.git`, `.lower()`. JSON реестра: `{name: {"path": str, "origin", "git_dir": str|None, "added"}}`, `json.dumps(..., ensure_ascii=False, indent=1)`; ошибка `json.JSONDecodeError`/неверная форма → `RegistryError(f"Cannot read {file}: {e}. Fix or delete it.")`.

- [ ] **Step 5: прогнать — проходят** · та же команда → PASS

- [ ] **Step 6: коммит** · `git add src/rag_mcp/projects.py tests/conftest.py tests/test_project_sources.py && git commit -m "Add project registry and git helpers"`

---

### Task 2: Обход файлов проекта

**Files:** Modify `src/rag_mcp/projects.py`; Test `tests/test_project_sources.py`

**Interfaces — Produces:**
```python
SKIP_DIRS = {"node_modules", ".venv", "venv", "build", "dist", "target", "vendor", "__pycache__"}
def list_files(root: Path, skip: Path | None = None) -> tuple[list[Path], str | None]
#   файлы под root (абсолютные, отсортированы), warning — текст, если пришлось обойти без git
```
**Consumes:** `git` (Task 1), `SUPPORTED_SUFFIXES`, `_hidden` из `loaders.py`.

- [ ] **Step 1: падающие тесты**

```python
def rel(files, root): return sorted(p.relative_to(root).as_posix() for p in files)

def test_list_files_uses_git(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r", {".gitignore": "out/\n", "README.md": "x", "docs/a.md": "x",
                                      "src/app.py": "x", ".github/x.md": "x", "out/gen.md": "x"})
    (repo / "docs/new.md").write_text("untracked", encoding="utf-8")
    (repo / "docs/Спец ификация.md").write_text("x", encoding="utf-8")
    (repo / "README.md").unlink()                       # deleted, still in the git index
    files, warning = list_files(repo)
    assert rel(files, repo) == ["docs/a.md", "docs/new.md", "docs/Спец ификация.md"] and warning is None

def test_list_files_subfolder_only(make_repo, tmp_path):
    repo = make_repo(tmp_path / "r", {"README.md": "x", "docs/a.md": "x"})
    assert rel(list_files(repo / "docs")[0], repo / "docs") == ["a.md"]

def test_list_files_without_git_skips_junk(tmp_path):
    for f in ["a.md", "node_modules/p/readme.md", "build/x.md", ".cache/y.md", "sub/b.pdf", "c.py"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True); (tmp_path / f).write_text("x")
    assert rel(list_files(tmp_path)[0], tmp_path) == ["a.md", "sub/b.pdf"]

def test_list_files_falls_back_with_warning(make_repo, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", {"a.md": "x"})
    monkeypatch.setattr(projects, "git", lambda cwd, *a: None)   # e.g. "dubious ownership"
    files, warning = list_files(repo)
    assert rel(files, repo) == ["a.md"] and "git" in warning

def test_list_files_skips_subtree(tmp_path):
    for f in ["a.md", "data/docs/b.md"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True); (tmp_path / f).write_text("x")
    assert rel(list_files(tmp_path, skip=tmp_path / "data/docs")[0], tmp_path) == ["a.md"]
```

- [ ] **Step 2: прогнать — падают** · `uv run pytest tests/test_project_sources.py -q -k list_files` → `ImportError: list_files`

- [ ] **Step 3: реализовать `list_files`**

С git: `git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")`, `split("\0")`, пустые пропустить, `dict.fromkeys` против дублей (cached+others). Без git (`repo_root(root) is None`) — `root.rglob("*")` с пропуском сегментов из `SKIP_DIRS` и скрытых; если `repo_root` есть, но `ls-files` вернул `None` — тот же обход и `warning = "git ls-files failed in <root>; indexed without .gitignore"`. Общий фильтр: `is_file()`, суффикс в `SUPPORTED_SUFFIXES`, не `_hidden(p, root)`, не внутри `skip`.

- [ ] **Step 4: прогнать — проходят** · → PASS

- [ ] **Step 5: коммит** · `git commit -am "List a project's documents via git ls-files"` (+ `git add` новых файлов)

---

### Task 3: Проверки `add` — `resolve_project`

**Files:** Modify `src/rag_mcp/projects.py`; Test `tests/test_project_sources.py`

**Interfaces — Produces:**
```python
MAX_FILES = 5000
def resolve_project(path: str | Path | None, name: str | None, *, cwd: Path, docs_dir: Path,
                    existing: dict[str, Project], force: bool = False) -> tuple[Project, list[Path]]
#   готовый Project (added = date.today().isoformat()) и его файлы; ProjectError при отказе
```
**Consumes:** Task 1–2.

- [ ] **Step 1: падающие тесты** (все `ProjectError` проверять `match=` по ключевым словам ниже)

```python
def test_default_path_is_repo_root_and_name_is_main_repo(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "x"}, origin="git@github.com:Acme/shop.git")
    wt = tmp_path / "ATS-3010"; subprocess.run(["git", "-C", repo, "worktree", "add", "-q", wt], check=True)
    p, files = resolve_project(None, None, cwd=wt / "docs", docs_dir=tmp_path / "d", existing={})
    assert p.name == "shop" and p.path == wt.resolve() and p.origin == "github.com/acme/shop"
    assert p.git_dir == (repo / ".git").resolve() and len(files) == 1

def test_explicit_subfolder_and_name(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "x", "README.md": "x"})
    p, files = resolve_project("docs", "shop-docs", cwd=repo, docs_dir=tmp_path / "d", existing={})
    assert p.path == (repo / "docs").resolve() and p.name == "shop-docs" and len(files) == 1

def test_non_git_folder(tmp_path):
    (tmp_path / "notes").mkdir(); (tmp_path / "notes/a.md").write_text("x")
    p, _ = resolve_project(tmp_path / "notes", None, cwd=tmp_path, docs_dir=tmp_path / "d", existing={})
    assert (p.name, p.origin, p.git_dir) == ("notes", None, None)

@pytest.mark.parametrize("bad", [".hidden", "a/b", "x y"])
def test_invalid_name(tmp_path, bad):
    with pytest.raises(ProjectError, match="name"):
        resolve_project(tmp_path, bad, cwd=tmp_path, docs_dir=tmp_path / "d", existing={})
```

`test_refusals` — `@pytest.mark.parametrize` по строкам таблицы; в каждой `pytest.raises(ProjectError, match=…)`. Подготовка: `docs_dir = tmp_path/"docs"` с подпапкой `shop/`; `existing = {"other": Project("other", tmp_path/"other", None, None, "2026-10-16")}` (папка создана).

| Случай | Вызов | `match` |
|---|---|---|
| имя занято папкой `docs_dir/shop` | `resolve_project(tmp_path/"x", "shop", …)` | `taken` |
| имя занято проектом с другим путём | `resolve_project(tmp_path/"x", "other", …)` | `taken` |
| путь внутри проекта | `resolve_project(tmp_path/"other"/"sub", "y", …)` | `nested` |
| путь содержит проект | `resolve_project(tmp_path, "y", …)` | `nested` |
| тот же путь через симлинк (Review Focus) | `os.symlink(tmp_path/"other", tmp_path/"link")`; `resolve_project(tmp_path/"link", "y", …)` | `already connected` |
| путь внутри `docs_dir` | `resolve_project(docs_dir/"shop", "y", …)` | `docs_dir` |
| домашняя папка без `force` | `resolve_project(Path.home(), "y", …)` | `too broad` |
| нет пути / это файл | `resolve_project(tmp_path/"absent", "y", …)` | `not a folder` |

```python

def test_file_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "MAX_FILES", 2)
    for n in range(3): (tmp_path / f"{n}.md").write_text("x")
    with pytest.raises(ProjectError, match="3 documents"): resolve_project(tmp_path, "x", cwd=tmp_path, docs_dir=tmp_path / "d", existing={})
    assert len(resolve_project(tmp_path, "x", cwd=tmp_path, docs_dir=tmp_path / "d", existing={}, force=True)[1]) == 3

def test_same_path_same_name_is_an_update(tmp_path):
    (tmp_path / "x").mkdir()
    old = Project("x", (tmp_path / "x").resolve(), None, None, "2020-01-01")
    p, _ = resolve_project(tmp_path / "x", "x", cwd=tmp_path, docs_dir=tmp_path / "d", existing={"x": old})
    assert p.name == "x" and p.path == old.path
```

- [ ] **Step 2: прогнать — падают** · `-k "resolve or refusal or limit or name or folder"` → ImportError

- [ ] **Step 3: реализовать `resolve_project`**

Порядок: путь (`None` → `repo_root(cwd) or cwd`; относительный — от `cwd`; `resolve()`; не папка → `not a folder`) → имя (`None` → по `git_common_dir`: `.parent.name` если `.name == ".git"`, иначе `.name.removesuffix(".git")`; без git → `path.name`; regex) → `too broad` (`path == Path.home()` или `path == Path(path.anchor)`, кроме `force`) → `docs_dir` (`path` внутри `docs_dir` или равен) → `taken` (папка `docs_dir/<name>`; проект `name` с другим путём) → для каждого другого проекта: равный путь → `already connected as <name>`, вложенность в любую сторону → `nested` → `list_files(path, skip=docs_dir)` → лимит (`f"{n} documents, more than {MAX_FILES}; pass --force"`). Сравнение путей — normcase(realpath).

- [ ] **Step 4: прогнать — проходят** · → PASS
- [ ] **Step 5: коммит** · `git commit -m "Validate projects before connecting them"`

---

### Task 4: Pipeline — индексация подключённых проектов

**Files:** Modify `src/rag_mcp/loaders.py`, `src/rag_mcp/pipeline.py`; Test `tests/test_project_sources.py`

**Interfaces — Produces:**
```python
# loaders.py
def load_document(path: Path, source: str) -> Document   # source задаёт вызывающий

# pipeline.py
@dataclass
class SourceScan:
    files: list[tuple[Path, str]]   # (файл, source)
    missing: list[str]              # имена проектов, чей path не существует
    warnings: list[str]             # из list_files

class Pipeline:
    def scan(self) -> SourceScan                       # RegistryError пробрасывает
    def add_project(self, path, name=None, *, cwd: Path | None = None, force=False) -> tuple[Project, int]
    def remove_project(self, name: str) -> Project     # ProjectError("no project <name>")
    def projects_info(self) -> list[dict]              # см. Step 3
```
`add_project`/`remove_project` только меняют реестр под `self.store.write_lock()`; ingest вызывает сторона (CLI, позже MCP).

- [ ] **Step 1: падающие тесты** (фикстура `pipeline` из conftest + `make_repo`)

```python
def test_connected_project_is_indexed_in_place(pipeline, make_repo, tmp_path):
    pipeline.ingest()                                   # examples/docs first, so the counts below are ours
    repo = make_repo(tmp_path / "shop", {"docs/refunds.md": "# Refunds\n\nRefunds go through Stripe."})
    pipeline.add_project(repo); r = pipeline.ingest()
    assert r.added == 1 and "shop" in pipeline.store.projects()
    hit = pipeline.search("refunds stripe", top_k=1, source_prefixes=["shop/"])[0]
    assert hit["source"] == "shop/docs/refunds.md" and pipeline.needs_ingest() is False
    (repo / "docs/refunds.md").write_text("# Refunds\n\nRefunds go through Adyen.", encoding="utf-8")
    assert pipeline.needs_ingest() is True and pipeline.ingest().updated == 1

def test_remove_drops_chunks_and_rename_reindexes(pipeline, make_repo, tmp_path):   # Review Focus
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"}); pipeline.add_project(repo); pipeline.ingest()
    pipeline.remove_project("shop"); pipeline.add_project(repo, "shop2"); pipeline.ingest()
    assert "shop" not in pipeline.store.projects() and "shop2" in pipeline.store.projects()

def test_missing_path_keeps_chunks(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"}); pipeline.add_project(repo); pipeline.ingest()
    shutil.rmtree(repo)
    assert pipeline.needs_ingest() is False
    r = pipeline.ingest()
    assert r.removed == 0 and any("shop" in e and "not found" in e for e in r.errors)
    assert "shop" in pipeline.store.projects()

def test_corrupt_registry_blocks_ingest(pipeline, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "x"}); pipeline.add_project(repo); pipeline.ingest()
    (pipeline.settings.index_dir / "projects.json").write_text("{", encoding="utf-8")
    with pytest.raises(RegistryError): pipeline.ingest()
    assert "shop" in pipeline.store.projects()

def test_add_while_index_is_busy(pipeline, make_repo, tmp_path):                     # Review Focus
    repo = make_repo(tmp_path / "shop", {"a.md": "x"})
    with pipeline._open_store().write_lock():
        with pytest.raises(IndexBusy): pipeline.add_project(repo)
    assert load_projects(pipeline.settings.index_dir) == {}

def test_only_projects_without_docs_dir(tmp_path, make_repo):
    pipe = Pipeline(Settings(docs_dir=tmp_path / "absent", index_dir=tmp_path / "idx", embed_provider="hash", _env_file=None))
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"}); pipe.add_project(repo)
    assert pipe.needs_ingest() is True and pipe.ingest().added == 1 and pipe.needs_ingest() is False

def test_projects_info(pipeline, make_repo, tmp_path):
    (pipeline.settings.docs_dir / "team").mkdir(); (pipeline.settings.docs_dir / "team/x.md").write_text("# X\n\nx")
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"}); pipeline.add_project(repo); pipeline.ingest()
    rows = {r["name"]: r for r in pipeline.projects_info()}
    assert rows["shop"] | {"chunks": 0} == {"name": "shop", "kind": "connected", "path": str(repo.resolve()),
                                         "exists": True, "documents": 1, "chunks": 0} and rows["shop"]["chunks"] > 0
    assert rows["team"]["kind"] == "folder" and rows["team"]["documents"] == 1
```

- [ ] **Step 2: прогнать — падают** · `-k "connected or remove or missing or corrupt or busy or projects_info or without_docs"` → AttributeError

- [ ] **Step 3: реализовать**

`load_document(path, source)` — убрать `relative_to(docs_dir)`; обновить вызов в `_ingest`. `scan()`: файлы `docs_dir` (если существует) как сейчас → `source = rel`; затем `load_projects(index_dir)`; для каждого проекта: нет `path` → `missing`; иначе `list_files(path, skip=docs_dir)` → `source = f"{name}/{rel}"`, warning → `warnings`. `_ingest`: вместо `iter_files` — `scan.files`; `seen` дополнить doc_id из манифеста, чей `source` начинается с `f"{name}/"` для `name in scan.missing`; в `report.errors` — `f"project {name}: path not found ({path}); run `rag-mcp remove {name}` or add it again from the new place"`; `warnings` — тоже в `report.errors`. `FileNotFoundError` про `docs_dir` — только если `docs_dir` нет **и** реестр пуст. `needs_ingest`: то же по `scan()`, документы пропавших проектов исключить из сравнения количества; `RegistryError` → `False` (поиск работает, ingest всё равно откажет с понятной ошибкой). `add_project`: `with self.store.write_lock(): existing = load_projects(...); p, files = resolve_project(path, name, cwd=cwd or Path.cwd(), docs_dir=..., existing=existing, force=force); save_projects(..., {**existing, p.name: p})` → `(p, len(files))`. `remove_project`: под тем же локом. `projects_info`: подключённые из реестра (+`exists`, счётчики по манифесту и `store.sources()`/чанкам с префиксом `name/`), затем папки `docs_dir/<name>` как `kind="folder"`. `status()` — ключ `"connected_projects": {name: str(path)}`.

- [ ] **Step 4: прогнать весь набор** · `uv run pytest -q` → все PASS (старые тесты тоже: `load_document` вызывается только из pipeline — проверить `grep -rn load_document src tests`)

- [ ] **Step 5: коммит** · `git commit -m "Index connected project folders under <name>/"`

---

### Task 5: CLI `add` / `remove` / `projects`

**Files:** Modify `src/rag_mcp/cli.py`; Test `tests/test_project_sources.py`

**Interfaces — Consumes:** `Pipeline.add_project`, `remove_project`, `projects_info`, `ingest` (Task 4); `ProjectError`, `RegistryError`, `IndexBusy`.

- [ ] **Step 1: падающие тесты** — через `cli.main([...])`, вывод через `capsys`. Фикстура в этом же файле:

```python
@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DOCS_DIR", str(tmp_path / "docs")); (tmp_path / "docs").mkdir()
    monkeypatch.setenv("RAG_INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("RAG_EMBED_PROVIDER", "hash")
```

```python
def test_cli_add_from_subfolder_indexes_repo(cli_env, make_repo, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "# A\n\nalpha"}, origin="git@github.com:Acme/shop.git")
    monkeypatch.chdir(repo / "docs")
    assert cli.main(["add"]) == 0
    out = capsys.readouterr().out
    assert "shop" in out and str(repo.resolve()) in out and "github.com/acme/shop" in out and "added=1" in out

def test_cli_add_refusal_exits_2(cli_env, tmp_path, capsys):
    assert cli.main(["add", str(Path.home())]) == 2 and "too broad" in capsys.readouterr().err

def test_cli_projects_and_remove(cli_env, make_repo, tmp_path, capsys):
    repo = make_repo(tmp_path / "shop", {"a.md": "# A\n\nalpha"})
    assert cli.main(["add", str(repo)]) == 0 and cli.main(["projects"]) == 0
    line = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("shop"))
    assert "connected" in line and " 1 " in line
    assert cli.main(["remove", "shop"]) == 0 and cli.main(["projects"]) == 0
    assert not any(l.startswith("shop") for l in capsys.readouterr().out.splitlines())
    assert cli.main(["remove", "ghost"]) == 2 and "no project" in capsys.readouterr().err
```

- [ ] **Step 2: прогнать — падают** · `-k cli_` → `SystemExit: 2` (argparse: invalid choice)

- [ ] **Step 3: реализовать**

Сабпарсеры: `add [path] [--name N] [--force]`, `remove name`, `projects [--json]`. `add`: `add_project(args.path, args.name, cwd=Path.cwd(), force=args.force)` → печать `connected <name>: <path> (origin <origin or "none">), <n> documents found` → `pipe.ingest()` → та же строка отчёта, что у `ingest` (вынести печать в `_print_report(r, pipe)`), затем `search from this folder: rag-mcp search "…" --project <name>`. `remove`: `remove_project` → `ingest()` → `removed <name>` + отчёт. `projects`: таблица колонками `name  kind  documents  chunks  path` (+ `  (path not found)`), `--json` — `projects_info()` как есть. `ProjectError`/`RegistryError` → stderr, код 2; `IndexBusy` → stderr, код 1.

- [ ] **Step 4: прогнать весь набор** · `uv run pytest -q` → PASS

- [ ] **Step 5: ручная проверка** на реальном репо: `uv run rag-mcp add ~/rag-mcp --name rag-mcp-docs` (или любой свой) → `projects` → `search "…" --project <имя>` → `remove`. Ожидается: README и docs/ репо найдены, `data/docs` не задвоен.

- [ ] **Step 6: коммит** · `git commit -m "CLI: add, remove and projects commands"`

---

## После плана

`pm log T-014 --did … --next …`; PR в main вместе со spec и этим планом (ветка `feat/project-sources`); в описании PR — ручная проверка из Task 5 Step 5. Маркер «← здесь» в `projects` и CLI `search` по cwd — T-015.
