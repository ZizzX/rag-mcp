# T-015 · Определение проекта: путь → origin → .git → имя — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** поиск сам находит проект рабочей папки — внутри подключённой папки, в её worktree где угодно на диске, во втором клоне с тем же origin — и говорит, как нашёл; CLI `search` делает то же по cwd, `--all` ищет везде.

**Architecture:** В `projects.py` — чистая функция `match_registry(folder, projects)`: шаги path → origin → git_dir против реестра. `Pipeline.detect_project` собирает весь порядок explicit → env → реестр (с кэшем в процессе) → старое правило по имени → `hint` и возвращает `Detection` вместо строки. Сервер кладёт поля `Detection` в `scope` ответа `search` и в `index_status`; CLI `search` и `projects` зовут то же по cwd.

**Tech Stack:** Python 3.12, stdlib, pytest; git CLI (опционально).

**Spec:** `docs/superpowers/specs/2026-10-16-project-sources-design.md` — часть 2; из части 3 строки `search … [--all]`, «← здесь» в `projects` и `hint`; из части 5 «повреждённый projects.json» и тесты «Определение» / «CLI». MCP `add_project`, `INSTRUCTIONS` и `hint` с упоминанием `add_project` — T-016.

## Global Constraints

- База — код T-014 в этой ветке: `projects._key` / `_inside` (realpath + normcase), `git()` (таймаут по умолчанию 2 с; `None`, если git нет, он упал или завис), `origin_of`, `git_common_dir`, `load_projects`, `RegistryError`.
- `matched_by` — ровно одно из `explicit | env | path | origin | git_dir | name`.
- `docs_from` — `str(Project.path)`, когда найденный проект подключён и `matched_by != "path"`; иначе поля нет. Для папок `data/docs` — нет.
- Порядок: explicit → env (`RAG_PROJECT`) → реестр (path → origin → git_dir) по всем рабочим папкам по очереди → правило `name` по всем рабочим папкам (старый цикл без изменений: имя папки или любого её предка среди `store.projects()`) → нет совпадения. Рабочие папки — в порядке `workspace_paths` (лучший источник первым).
- Несколько проектов с тем же origin: тот, чей `git_dir` совпал; иначе первый по имени + `note`: `connected projects a, b share this origin; using a`.
- Повреждённый `projects.json` при определении = пустой реестр (работает только `name`), без исключения.
- Пустой реестр → git не запускается. origin спрашивается, только если у кого-то в реестре есть `origin`; `--git-common-dir` — только если есть `git_dir`.
- **Кэш:** результат `match_registry` хранится в процессе по ключу `(_key(folder), подпись реестра)`, подпись — кортеж `(name, path, origin, git_dir)` всех записей. Отступление от spec (там `mtime_ns`): на Linux mtime тикает грубо (~4 мс), две записи реестра подряд дают тот же `mtime_ns`; по содержимому сброс точный, а маленький JSON на каждый поиск читается дёшево. Правило `name` не кэшируется: git не зовёт, а зависит от индекса.
- Тексты на английском. `hint` (точная строка): ``f"{folder} is not connected to rag-mcp; run `rag-mcp add` in that folder to index its documents"``, `folder` — `Path(первая рабочая папка)` (на Windows roots приходят как `C:/…`, `Path` даёт родной вид).
- Маркер в `rag-mcp projects` — ASCII `<- here`, не `←`: `rag-mcp projects > file` на Windows пишет в cp1252 и упал бы на `←`.

## Review Focus

- Подключена подпапка (`rag-mcp add ./docs`), рабочая папка — корень репо или соседняя подпапка → проект находится по git_dir/origin. → тест в Task 1.
- git нет в PATH или он завис (сетевой диск) → шаги origin/git_dir пропущены, path работает, исключения нет. → тест в Task 1.
- Несколько рабочих папок (multi-root): первая совпадает только по имени, вторая внутри подключённого проекта → проект второй (реестр важнее имени). → тест в Task 2.
- Рабочая папка не существует (root на удалённую папку) → нет совпадения, `hint`, без исключения. → тест в Task 2.
- Повреждённый `projects.json` → определение по `name` работает, исключения нет. → тест в Task 2.

---

## Файлы

| Файл | Что |
|---|---|
| Modify `src/rag_mcp/projects.py` | `match_registry` |
| Modify `src/rag_mcp/pipeline.py` | `Detection`, `Pipeline.detect_project(paths, explicit)`, кэш |
| Modify `src/rag_mcp/server.py` | `resolve_scope` и `index_status` с полями `Detection`; докстринг модуля |
| Modify `src/rag_mcp/cli.py` | `search` по cwd, `--all`, строка скоупа; `<- here` в `projects` |
| Modify `tests/conftest.py` | фикстура `cli_env` переезжает сюда из `tests/test_project_sources.py` |
| Create `tests/test_project_detection.py` | тесты Task 1–3 |
| Modify `tests/test_projects.py`, `tests/test_server_projects.py` | старые проверки `detect_project` и скоупа под `Detection` |

---

### Task 1: `match_registry` — path → origin → git_dir

**Files:** Modify `src/rag_mcp/projects.py`; Test `tests/test_project_detection.py`

**Interfaces — Produces:**
```python
def match_registry(folder: Path, projects: dict[str, Project]) -> tuple[Project, str, str | None] | None
    # (project, matched_by ∈ {"path", "origin", "git_dir"}, note) или None
```

- [ ] **Step 1: падающие тесты** (шапка: `import subprocess` · `from pathlib import Path` · `import pytest` · `from rag_mcp import projects` · `from rag_mcp.projects import Project, git_common_dir, match_registry, origin_of`; Task 2 добавит `from rag_mcp.pipeline import Detection`, Task 3 — `import json` и `cli`)

```python
ORIGIN = "git@github.com:Acme/shop.git"


def reg(**repos: Path) -> dict[str, Project]:
    return {n: Project(n, p.resolve(), origin_of(p), git_common_dir(p), "") for n, p in repos.items()}


def worktree(repo: Path, where: Path) -> Path:
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(where), "-b", where.name],
                   check=True, capture_output=True)
    return where


def test_path_match(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"src/a.md": "a"}, origin=ORIGIN)
    p, by, note = match_registry(repo / "src", reg(shop=repo))
    assert (p.name, by, note) == ("shop", "path", None)


def test_worktree_elsewhere_matches_by_git_dir(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"})  # no origin: only the shared .git links them
    p, by, _ = match_registry(worktree(repo, tmp_path / "orca" / "ATS-3010"), reg(shop=repo))
    assert (p.name, by) == ("shop", "git_dir")


def test_second_clone_matches_by_origin(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"}, origin=ORIGIN)
    clone = make_repo(tmp_path / "tmp" / "shop-copy", {"a.md": "a"}, origin="https://github.com/acme/shop")
    p, by, note = match_registry(clone, reg(shop=repo))
    assert (p.name, by, note) == ("shop", "origin", None)


def test_same_origin_twice_prefers_own_git_dir(make_repo, tmp_path):
    a = make_repo(tmp_path / "a" / "shop", {"a.md": "a"}, origin=ORIGIN)
    b = make_repo(tmp_path / "b" / "shop", {"a.md": "a"}, origin=ORIGIN)
    connected = reg(shop=a, shop2=b)
    p, by, note = match_registry(worktree(b, tmp_path / "wt"), connected)
    assert (p.name, by, note) == ("shop2", "origin", None)
    other = make_repo(tmp_path / "c" / "shop", {"a.md": "a"}, origin=ORIGIN)
    p, by, note = match_registry(other, connected)
    assert (p.name, by) == ("shop", "origin") and "shop2" in note


def test_connected_subfolder_found_from_repo(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "a", "src/x.md": "x"})
    connected = {"shop": Project("shop", (repo / "docs").resolve(), None, git_common_dir(repo / "docs"), "")}
    p, by, _ = match_registry(repo / "src", connected)
    assert (p.name, by) == ("shop", "git_dir")


def test_no_match(make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"}, origin=ORIGIN)
    other = make_repo(tmp_path / "other", {"a.md": "a"}, origin="git@github.com:Acme/other.git")
    assert match_registry(other, reg(shop=repo)) is None
    assert match_registry(tmp_path / "missing", reg(shop=repo)) is None
    assert match_registry(tmp_path, {}) is None


@pytest.mark.parametrize("failure", [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 2)])
def test_without_git_only_path_matches(make_repo, tmp_path, monkeypatch, failure):
    repo = make_repo(tmp_path / "shop", {"src/a.md": "a"})
    wt = worktree(repo, tmp_path / "wt")
    connected = reg(shop=repo)

    def broken(*a, **kw):
        raise failure

    monkeypatch.setattr(projects.subprocess, "run", broken)
    assert match_registry(wt, connected) is None
    assert match_registry(repo / "src", connected)[1] == "path"
```

- [ ] **Step 2: прогнать — падают** · `uv run pytest tests/test_project_detection.py -q` → `ImportError: cannot import name 'match_registry'`

- [ ] **Step 3: реализовать `match_registry` в `projects.py`** по порядку из Global Constraints: path — первый проект с `_inside(folder, p.path)`; затем (лениво, см. ограничения) `origin_of(folder)` и `git_common_dir(folder)`; совпавшие по origin, отсортированные по имени: свой `git_dir` (сравнение через `_key`) → он без `note`, иначе первый + `note` при двух и более; затем первый по имени с совпавшим `git_dir` → `"git_dir"`; иначе `None`.

- [ ] **Step 4: прогнать** · `uv run pytest tests/test_project_detection.py -q` → PASS

- [ ] **Step 5: коммит** · `git commit -m "Match a folder to a connected project by path, origin or .git"`

---

### Task 2: `Pipeline.detect_project` → `Detection`; `scope` и `index_status`

**Files:** Modify `src/rag_mcp/pipeline.py`, `src/rag_mcp/server.py`; Test `tests/test_project_detection.py`, `tests/test_projects.py`, `tests/test_server_projects.py`

**Interfaces — Consumes:** `match_registry` (Task 1). **Produces:**
```python
@dataclass
class Detection:                      # pipeline.py
    project: str | None = None
    matched_by: str | None = None     # explicit | env | path | origin | git_dir | name
    docs_from: str | None = None
    note: str | None = None
    hint: str | None = None           # только когда ничего не совпало и рабочие папки были

    def fields(self) -> dict          # matched_by/docs_from/note/hint, которые не None, — для JSON

class Pipeline:
    def detect_project(self, workspace_paths: list[str], explicit: str | None = None) -> Detection
```

Скоуп `search` (`resolve_scope`): найден — `{"mode": "project", "prefixes", "project", "workspace", **d.fields()}`; не найден — `{"mode": "all", "prefixes": None, "project": None, "workspace", **d.fields()}` (там `hint`; старый `note` «workspace folder does not match…» убрать — `hint` его заменяет); `project=` задан и проиндексирован — `{"mode": "project", "prefixes", "project", **pipe.detect_project([], explicit=project).fields()}`; `project=` не проиндексирован и `source_prefix` / `all_projects` — без изменений. `index_status`: `detected_project = d.project`, `matched_by = d.matched_by` (всегда, может быть `None`), плюс остальные `d.fields()`.

- [ ] **Step 1: падающие тесты**

В `tests/test_project_detection.py` (в шапку `from rag_mcp.pipeline import Detection`):

```python
def add_team_folder(pipeline):
    (pipeline.settings.docs_dir / "team").mkdir()
    (pipeline.settings.docs_dir / "team" / "t.md").write_text("# T\n\nteam", encoding="utf-8")


def test_detect_order_and_docs_from(pipeline, make_repo, tmp_path):
    add_team_folder(pipeline)
    repo = make_repo(tmp_path / "team" / "shop-repo", {"src/a.md": "a"})  # parent named like a docs folder
    pipeline.add_project(repo, "shop")
    pipeline.ingest()
    src = str(repo / "src")
    assert pipeline.detect_project([src]) == Detection("shop", "path")
    wt = worktree(repo, tmp_path / "orca" / "ATS-3010")
    assert pipeline.detect_project([str(wt)]) == Detection("shop", "git_dir", docs_from=str(repo.resolve()))
    assert pipeline.detect_project([str(tmp_path / "code" / "team" / "x")]) == Detection("team", "name")
    assert pipeline.detect_project([str(tmp_path / "code" / "team"), src]) == Detection("shop", "path")
    assert pipeline.detect_project([], explicit="shop") == Detection("shop", "explicit", str(repo.resolve()))
    pipeline.settings.project = "team"
    assert pipeline.detect_project([src]) == Detection("team", "env")


def test_no_match_gives_hint(pipeline, tmp_path):
    missing = str(tmp_path / "missing")
    d = pipeline.detect_project([missing])
    assert (d.project, d.matched_by) == (None, None)
    assert missing in d.hint and "rag-mcp add" in d.hint
    assert pipeline.detect_project([]) == Detection()


def test_cache_reruns_git_only_after_registry_changes(pipeline, make_repo, tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "shop", {"a.md": "a"}, origin=ORIGIN)
    other = make_repo(tmp_path / "other", {"a.md": "a"}, origin="git@github.com:Acme/other.git")
    pipeline.add_project(repo)
    timeouts = []
    real_run = subprocess.run

    def run(cmd, *a, **kw):
        timeouts.append(kw.get("timeout"))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(projects.subprocess, "run", run)
    assert pipeline.detect_project([str(other)]).project is None
    assert timeouts and set(timeouts) == {2}
    n = len(timeouts)
    assert pipeline.detect_project([str(other)]).project is None
    assert len(timeouts) == n  # cached: git is not run again for the same folder
    pipeline.add_project(other)  # the registry changed
    assert pipeline.detect_project([str(other)]) == Detection("other", "path")


def test_corrupt_registry_falls_back_to_name(pipeline, tmp_path):
    add_team_folder(pipeline)
    pipeline.ingest()
    (pipeline.settings.index_dir / "projects.json").write_text("{broken", encoding="utf-8")
    assert pipeline.detect_project([str(tmp_path / "code" / "team")]) == Detection("team", "name")
```

В `tests/test_projects.py`: `test_detect_project_walks_up_from_subfolder` — сравнивать с `Detection("shop-backend", "name")` для обоих путей, `.project is None` для `unrelated`, `detect_project([]) == Detection()`; `test_detect_project_override` — `== Detection("mobile-app", "env")`.

В `tests/test_server_projects.py`: дописать в `test_search_scopes_to_detected_project` — `out["scope"]["matched_by"] == "name"`; в `test_search_falls_back_to_all_when_workspace_unknown` — `"something-else" in out["scope"]["hint"]` и `"note" not in out["scope"]`; в `test_index_status_shows_detected_project` — `status["matched_by"] == "name"`; в `test_explicit_project_parameter` (первый вызов) — `out["scope"]["matched_by"] == "explicit"`. Новый (сквозной сценарий «Успех» из spec; `import subprocess` в шапку):

```python
@pytest.mark.anyio
async def test_search_from_worktree_of_connected_repo(workspace, make_repo, tmp_path):
    repo = make_repo(tmp_path / "shop", {"docs/stripe.md": "# Stripe\n\nRefunds via the Stripe dashboard."})
    server._pipeline.add_project(repo)
    server._pipeline.ingest()
    wt = tmp_path / "orca" / "ATS-3010"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "ATS-3010"],
                   check=True, capture_output=True)
    out = json.loads(await server.search("refunds", top_k=5, ctx=fake_ctx(wt)))
    scope = out["scope"]
    assert (scope["mode"], scope["project"], scope["matched_by"]) == ("project", "shop", "git_dir")
    assert scope["docs_from"] == str(repo.resolve()) and scope["prefixes"] == ["shop/", "common/"]
    assert out["results"] and {r["project"] for r in out["results"]} <= {"shop", "common"}
```

- [ ] **Step 2: прогнать — падают** · `uv run pytest tests/test_project_detection.py tests/test_projects.py tests/test_server_projects.py -q` → `ImportError: Detection` / сравнения со строкой

- [ ] **Step 3: реализовать** `Detection` и `detect_project` в `pipeline.py` по порядку из Global Constraints. Кэш — `self._registry_matches: dict` в `__init__`; на каждый вызов `load_projects` (при `RegistryError` — `{}`), подпись и ключ — как в Global Constraints. `docs_from` для explicit/env/name — тоже по загруженному реестру. Обновить `resolve_scope` и `index_status` в `server.py` по контракту выше и абзац «Multi-project scoping» в докстринге модуля (порядок определения, `matched_by`, `hint`).

- [ ] **Step 4: прогнать весь набор** · `uv run pytest -q` → PASS

- [ ] **Step 5: коммит** · `git commit -m "Detect the project by path, origin, .git or name; show how in scope"`

---

### Task 3: CLI — `search` по cwd и `--all`; `<- here` в `projects`

**Files:** Modify `src/rag_mcp/cli.py`, `tests/conftest.py`, `tests/test_project_sources.py` (убрать `cli_env`); Test `tests/test_project_detection.py`

**Interfaces — Consumes:** `Pipeline.detect_project`, `Detection` (Task 2); `Pipeline.scope_for`, `projects_info`.

- [ ] **Step 1: перенести `cli_env`** из `tests/test_project_sources.py` в `tests/conftest.py` без изменений

- [ ] **Step 2: падающие тесты** (в шапку `import json`, `from rag_mcp import cli, projects`)

```python
def test_cli_search_scopes_to_cwd_project(cli_env, make_repo, tmp_path, monkeypatch, capsys):
    (tmp_path / "docs" / "team").mkdir()
    (tmp_path / "docs" / "team" / "t.md").write_text("# T\n\nalpha in team", encoding="utf-8")
    repo = make_repo(tmp_path / "shop", {"src/a.md": "# A\n\nalpha in shop"})
    assert cli.main(["add", str(repo)]) == 0
    monkeypatch.chdir(repo / "src")
    capsys.readouterr()
    assert cli.main(["search", "alpha", "--json"]) == 0
    assert {r["source"] for r in json.loads(capsys.readouterr().out)} == {"shop/src/a.md"}
    assert cli.main(["search", "alpha", "--all", "--json"]) == 0
    assert {r["source"] for r in json.loads(capsys.readouterr().out)} == {"shop/src/a.md", "team/t.md"}
    assert cli.main(["search", "alpha"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "scope: shop/ (matched by path)"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["search", "alpha"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("scope: all projects") and "rag-mcp add" in first
    with pytest.raises(SystemExit):
        cli.main(["search", "alpha", "--all", "--project", "shop"])


def test_cli_projects_marks_cwd_project(cli_env, make_repo, tmp_path, monkeypatch, capsys):
    (tmp_path / "docs" / "team").mkdir()
    (tmp_path / "docs" / "team" / "t.md").write_text("# T\n\nteam", encoding="utf-8")
    repo = make_repo(tmp_path / "shop", {"docs/a.md": "# A\n\nalpha"})
    assert cli.main(["add", str(repo)]) == 0
    monkeypatch.chdir(repo / "docs")
    capsys.readouterr()
    assert cli.main(["projects"]) == 0
    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines()}
    assert lines["shop"].endswith("  <- here") and not lines["team"].endswith("<- here")
```

- [ ] **Step 3: прогнать — падают** · `uv run pytest tests/test_project_detection.py -q -k cli_` → `unrecognized arguments: --all`

- [ ] **Step 4: реализовать в `cli.py`**

`search`: `--source` / `--project` / `--all` — в одной `add_mutually_exclusive_group`. Без `--source` и `--all`: `d = pipe.detect_project([os.getcwd()], explicit=args.project)`, `prefixes = pipe.scope_for(d.project)`. Без `--json` первой строкой печатается скоуп (`--json` выводит список результатов как раньше, без строки):

| Случай | Строка |
|---|---|
| `--source` | `scope: <префиксы через пробел>` |
| `--all` | `scope: all projects` |
| проект найден | `scope: <префиксы через пробел> (matched by <matched_by>)`, при `docs_from` перед `)` ещё `, documents from <docs_from>` |
| не найден | `scope: all projects (<hint>)` |

`projects` (текст): `here = pipe.detect_project([os.getcwd()]).project`; строке с `r["name"] == here` дописать `  <- here`. `--json` — без изменений.

- [ ] **Step 5: прогнать весь набор** · `uv run pytest -q` → PASS

- [ ] **Step 6: ручная проверка** — в своём подключённом репо и в его worktree: `uv run rag-mcp search "…"` печатает `scope: <имя>/ (matched by path)` / `(matched by git_dir, documents from …)`; `rag-mcp projects` помечает строку `<- here`; `--all` ищет везде.

- [ ] **Step 7: коммит** · `git commit -m "CLI: search the project of the current folder; --all; mark it in projects"`

---

## После плана

Ревью всей ветки свежим ревьюером (opus); правки Critical/Important — тестом RED→GREEN. `pm log T-015 --did … --next …`. PR: пока PR #6 (T-014) не смёржен — в `feat/project-sources`, после мёржа — перебазировать на `main` (решает пользователь). В описании PR — ручная проверка из Task 3 Step 6.
