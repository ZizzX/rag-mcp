import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from rag_mcp import register as reg
from rag_mcp.cli import main


class FakeClaude:
    """Stands in for the `claude` CLI: keeps user-scope servers in a dict."""

    def __init__(self, servers: dict | None = None):
        self.servers = dict(servers or {})
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        sub, name = cmd[2], cmd[3] if cmd[2] == "get" else cmd[5]
        out, code = "", 0
        if sub == "get":
            if name in self.servers:
                out = f"{name}:\n  Scope: User config (available in all your projects)\n  Args: {self.servers[name]}\n"
            else:
                code = 1
        elif sub == "remove":
            code = 0 if self.servers.pop(name, None) is not None else 1
        elif sub == "add":
            self.servers[name] = " ".join(cmd[cmd.index("--") + 2 :])
        return subprocess.CompletedProcess(cmd, code, out, "")


@pytest.fixture
def env(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".cursor").mkdir()
    (home / "Library" / "Application Support" / "Claude").mkdir(parents=True)
    tools = {"uv": "/opt/bin/uv", "claude": "/opt/bin/claude"}
    return reg.Env(
        home=home,
        platform="darwin",
        repo=tmp_path / "rag-mcp",
        which=tools.get,
        run=FakeClaude(),
        now=lambda: datetime(2026, 11, 11, 12, 0, 0),
    )


def statuses(steps):
    return [(s.client, s.what, s.status) for s in steps]


def register_all(env, **kw):
    return [s for c in reg.CLIENTS for s in reg.register_client(c, env, **kw)]


def test_register_installs_everything_then_is_idempotent(env):
    first = register_all(env)
    assert [s.status for s in first] == ["added"] * 6
    skill = env.home / ".claude" / "skills" / "rag-mcp" / "SKILL.md"
    assert skill.read_text(encoding="utf-8") == reg.SKILL_TEXT
    assert (env.home / ".cursor" / "skills" / "rag-mcp" / "SKILL.md").read_text(encoding="utf-8") == reg.SKILL_TEXT
    cursor = json.loads((env.home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert cursor["mcpServers"]["rag-mcp"] == {
        "command": "/opt/bin/uv",
        "args": ["--directory", str(env.repo), "run", "rag-mcp", "serve"],
    }
    assert env.run.servers["rag-mcp"] == f"--directory {env.repo} run rag-mcp serve"

    claude_md = env.home / ".claude" / "CLAUDE.md"
    snapshot = {p: p.read_bytes() for p in env.home.rglob("*") if p.is_file()}
    again = register_all(env)
    assert all(s.status == "unchanged" for s in again), statuses(again)
    assert {p: p.read_bytes() for p in env.home.rglob("*") if p.is_file()} == snapshot
    assert claude_md.read_text(encoding="utf-8").count(reg.BLOCK_BEGIN) == 1


def test_claude_md_block_is_appended_and_replaced_in_place(env):
    path = env.home / ".claude" / "CLAUDE.md"
    old_block = f"{reg.BLOCK_BEGIN} v0 -->\nold rule\n{reg.BLOCK_END}\n"
    path.write_text(f"# mine\n\nkeep this\n\n{old_block}\n## after\nalso mine\n", encoding="utf-8")
    step = reg.block_add(path, "claude-code", dry_run=False)
    assert step.status == "updated"
    text = path.read_text(encoding="utf-8")
    assert text == f"# mine\n\nkeep this\n\n{reg.CLAUDE_MD_BLOCK}\n## after\nalso mine\n"

    path.write_text("# mine\nno newline at end", encoding="utf-8")
    reg.block_add(path, "claude-code", dry_run=False)
    assert path.read_text(encoding="utf-8") == "# mine\nno newline at end\n\n" + reg.CLAUDE_MD_BLOCK
    reg.block_remove(path, "claude-code", dry_run=False)
    assert path.read_text(encoding="utf-8") == "# mine\nno newline at end\n"


def test_json_merge_keeps_other_servers_and_user_env_and_backs_up(env):
    path = env.home / ".cursor" / "mcp.json"
    path.write_text(json.dumps({
        "mcpServers": {
            "other": {"command": "x"},
            "rag-mcp": {"command": "uv", "args": ["old"], "env": {"RAG_EMBED_MODEL": "bge-m3"}},
        },
        "theme": "dark",
    }), encoding="utf-8")
    step = reg.json_server_add(path, "cursor", env, dry_run=False)
    assert step.status == "updated"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["rag-mcp"]["env"] == {"RAG_EMBED_MODEL": "bge-m3"}
    assert data["mcpServers"]["rag-mcp"]["args"][1] == str(env.repo)
    backup = path.with_name("mcp.json.bak-20261111-120000")
    assert json.loads(backup.read_text(encoding="utf-8"))["mcpServers"]["rag-mcp"]["args"] == ["old"]

    reg.json_server_remove(path, "cursor", env, dry_run=False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}


def test_invalid_json_is_not_overwritten(env):
    path = env.desktop_config()
    path.write_text("{ not json", encoding="utf-8")
    steps = reg.register_client("claude-desktop", env)
    assert steps[-1].status == "error" and "not valid JSON" in steps[-1].detail
    assert path.read_text(encoding="utf-8") == "{ not json"
    assert list(path.parent.iterdir()) == [path]


def test_foreign_skill_is_kept_unless_forced(env):
    skill = env.home / ".claude" / "skills" / "rag-mcp" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: rag-mcp\n---\nmy own\n", encoding="utf-8")
    assert reg.skill_add(skill.parent, "claude-code", dry_run=False, force=False).status == "skipped"
    assert reg.skill_remove(skill.parent, "claude-code", dry_run=False).status == "skipped"
    assert skill.read_text(encoding="utf-8").endswith("my own\n")
    assert reg.skill_add(skill.parent, "claude-code", dry_run=False, force=True).status == "updated"
    assert skill.read_text(encoding="utf-8") == reg.SKILL_TEXT


def test_claude_code_keeps_existing_registration_of_this_checkout(env):
    env.run.servers["rag-mcp"] = f"--directory {env.repo} run rag-mcp serve"
    assert reg.claude_mcp_add(env, dry_run=False, force=False).status == "unchanged"
    assert not any(c[2] in ("add", "remove") for c in env.run.calls)

    env.run.servers["rag-mcp"] = "--directory /elsewhere run rag-mcp serve"
    assert reg.claude_mcp_add(env, dry_run=False, force=False).status == "updated"
    assert env.run.servers["rag-mcp"].startswith(f"--directory {env.repo} ")


def test_claude_code_without_cli_prints_the_command(env):
    env.which = {"uv": "/opt/bin/uv"}.get
    step = reg.claude_mcp_add(env, dry_run=False, force=False)
    assert step.status == "skipped" and "claude mcp add -s user rag-mcp -- /opt/bin/uv --directory" in step.detail


def test_unregister_removes_only_what_register_added(env):
    claude_md = env.home / ".claude" / "CLAUDE.md"
    claude_md.write_text("# mine\n", encoding="utf-8")
    register_all(env)
    removed = [s for c in reg.CLIENTS for s in reg.unregister_client(c, env)]
    assert all(s.status == "removed" for s in removed), statuses(removed)
    assert claude_md.read_text(encoding="utf-8") == "# mine\n"
    assert not (env.home / ".claude" / "skills" / "rag-mcp").exists()
    assert "rag-mcp" not in env.run.servers
    assert json.loads((env.home / ".cursor" / "mcp.json").read_text(encoding="utf-8")) == {"mcpServers": {}}
    again = [s for c in reg.CLIENTS for s in reg.unregister_client(c, env)]
    assert all(s.status == "absent" for s in again), statuses(again)


def test_dry_run_writes_nothing(env):
    steps = register_all(env, dry_run=True)
    assert {s.status for s in steps} == {"added"}
    assert [p for p in env.home.rglob("*") if p.is_file()] == []
    assert env.run.servers == {}


def test_no_rules_registers_only_the_server(env):
    steps = register_all(env, rules=False)
    assert [s.what for s in steps] == ["mcp", "mcp", "mcp"]
    assert not (env.home / ".claude" / "CLAUDE.md").exists()


def test_choose_clients(env):
    assert reg.choose_clients(["cursor"], env, every=False, yes=False, interactive=True) == ["cursor"]
    assert reg.choose_clients([], env, every=False, yes=False, interactive=False) == list(reg.CLIENTS)
    answers = iter(["", "n", "да"])
    picked = reg.choose_clients([], env, every=False, yes=False, interactive=True, ask=lambda _: next(answers))
    assert picked == ["claude-code", "claude-desktop"]

    bare = reg.Env(home=env.home / "nobody", platform="linux", which=lambda _: None)
    assert reg.choose_clients([], bare, every=False, yes=True, interactive=False) == []
    assert reg.choose_clients([], bare, every=True, yes=True, interactive=False) == list(reg.CLIENTS)


def test_desktop_config_paths(tmp_path):
    assert reg.Env(home=tmp_path, platform="linux").desktop_config() == tmp_path / ".config/Claude/claude_desktop_config.json"
    win = reg.Env(home=tmp_path, platform="win32", appdata=tmp_path / "Roaming")
    assert win.desktop_config() == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"


def test_cli_dry_run(env, monkeypatch, capsys):
    monkeypatch.setattr(reg, "Env", lambda: env)
    assert main(["register", "--cursor", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[ok] Cursor: mcp added" in out and "dry run" in out
    assert not (env.home / ".cursor" / "mcp.json").exists()
