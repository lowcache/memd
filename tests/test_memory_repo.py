"""Tests for memory repo init, worktree sharing, and migration."""

import os
import shutil
import subprocess
import sys

import pytest

def test_init_memory_repo(memd, tmp_path):
    import memd.memory
    mem = tmp_path / ".memory"
    mem.mkdir()
    (mem / "state.md").write_text("seed")
    memd.memory.init_memory_repo(str(mem), "seed message")
    assert (mem / ".git").is_dir()
    
    r = subprocess.run(["git", "-C", str(mem), "log", "-1", "--format=%s"], capture_output=True, text=True)
    assert r.stdout.strip() == "seed message"

def test_migration_helper(memd, tmp_path):
    top = tmp_path / "repo"
    top.mkdir()
    subprocess.run(["git", "-C", str(top), "init", "-q"])
    
    mem = top / ".memory"
    mem.mkdir()
    (mem / "state.md").write_text("state")
    
    subprocess.run(["git", "-C", str(top), "add", ".memory"], capture_output=True)
    subprocess.run(["git", "-C", str(top), "commit", "-m", "add mem"], capture_output=True)
    
    # Run migration
    import memd.memory
    memd.memory.migrate_parent_untrack(str(top))
    
    # Ensure untracked
    r = subprocess.run(["git", "-C", str(top), "ls-files", "--", ".memory"], capture_output=True, text=True)
    assert r.stdout.strip() == ""
    
    # Ensure gitignored
    assert (top / ".gitignore").read_text().strip() == ".memory/"
    
    # Ensure file still on disk
    assert (mem / "state.md").read_text() == "state"

def test_worktree_sharing(memd, tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "-C", str(main), "init", "-q"])
    subprocess.run(["git", "-C", str(main), "commit", "--allow-empty", "-m", "init"])
    
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", str(wt), "-b", "feature"])
    
    import memd.memory
    assert memd.memory.main_worktree_root(str(wt)) == str(main)
    assert memd.memory.main_worktree_root(str(main)) == str(main)

def test_cli_init_integration(memd, env, tmp_path, monkeypatch):
    import memd.cli
    cfg = memd.load_config()
    cfg["memory_own_repo"] = True
    cfg["git_commit"] = True
    memd.save_config(cfg)
    
    # 1. Non-git project still scaffolds
    nongit = tmp_path / "nongit"
    nongit.mkdir()
    monkeypatch.setattr(sys, "argv", ["memd", "init", str(nongit), "--name", "nongit"])
    memd.cli.main()
    assert (nongit / ".memory" / "state.md").exists()
    assert (nongit / ".memory" / ".git").exists()
    
    # 2. Main repo: commit lands in .memory repo not parent
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "-C", str(main), "init", "-q"])
    subprocess.run(["git", "-C", str(main), "commit", "--allow-empty", "-m", "init"])
    
    monkeypatch.setattr(sys, "argv", ["memd", "init", str(main), "--name", "main"])
    memd.cli.main()
    
    mem = main / ".memory"
    assert (mem / ".git").is_dir()
    r = subprocess.run(["git", "-C", str(mem), "log", "-1", "--format=%s"], capture_output=True, text=True)
    assert r.stdout.strip() == "memd: seed project memory root"
    
    # Parent git status shouldn't have .memory tracked, but .gitignore is updated
    r = subprocess.run(["git", "-C", str(main), "ls-files", "--", ".memory"], capture_output=True, text=True)
    assert r.stdout.strip() == ""
    assert ".memory/" in (main / ".gitignore").read_text()
    
    # 3. Linked worktree: symlink resolving to the main store
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", str(wt), "-b", "feature"])
    
    monkeypatch.setattr(sys, "argv", ["memd", "init", str(wt), "--name", "wt"])
    memd.cli.main()
    
    wt_mem = wt / ".memory"
    assert wt_mem.is_symlink()
    assert os.path.realpath(str(wt_mem)) == os.path.realpath(str(mem))

def test_auto_scaffold_does_not_commit_into_parent_repo(memd, env, tmp_path):
    """Regression: auto-discovery (session-start hook, sweep) used to call
    scaffold()+register() directly, leaving .memory with no repo of its own, so
    the distill commit landed in the USER'S project history — memory leaking
    into a repo they may push publicly. Every path must provision the same way
    memd init does."""
    import memd.memory
    cfg = memd.load_config()
    cfg["memory_own_repo"] = True
    cfg["git_commit"] = True

    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "-C", str(proj), "init", "-q"])
    # HOME is isolated by the env fixture, so this repo has no git identity of
    # its own; without one the setup commit fails and the assertions below would
    # pass vacuously against an empty history.
    subprocess.run(["git", "-C", str(proj), "config", "user.email", "t@t"])
    subprocess.run(["git", "-C", str(proj), "config", "user.name", "t"])
    (proj / "code.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(proj), "add", "-A"], capture_output=True)
    r = subprocess.run(["git", "-C", str(proj), "commit", "-q", "-m", "init"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"test setup commit failed: {r.stderr}"

    memd.memory.provision_project(cfg, str(proj))
    # A distill commit after auto-discovery must not touch the project's repo.
    (proj / ".memory" / "state.md").write_text("port 9090\n")
    memd.memory.git_commit_memory(str(proj), "memd: distill")

    subjects = subprocess.run(
        ["git", "-C", str(proj), "log", "--format=%s"],
        capture_output=True, text=True).stdout.split()
    assert subjects == ["init"], f"memd commits leaked into project repo: {subjects}"

    mem = proj / ".memory"
    assert (mem / ".git").is_dir(), ".memory did not get its own repo"
    tracked = subprocess.run(["git", "-C", str(proj), "ls-files", "--", ".memory"],
                             capture_output=True, text=True).stdout
    assert tracked.strip() == ""
    # Auto-discovery must ignore .memory WITHOUT touching a tracked file: the
    # rule goes in the clone-local .git/info/exclude, and .gitignore is left
    # exactly as the user had it (here: absent).
    assert ".memory/" in (proj / ".git" / "info" / "exclude").read_text()
    assert not (proj / ".gitignore").exists()
    # and the memory itself is versioned in its own store
    r = subprocess.run(["git", "-C", str(mem), "log", "--format=%s"],
                       capture_output=True, text=True)
    assert "memd: distill" in r.stdout


def test_scaffold_creates_only_the_memory_store(memd, tmp_path):
    """scaffold() creates what memd needs to run and nothing else. Writing into
    the project root is a change to the user's workspace and belongs to the
    explicit path only."""
    proj = tmp_path / "proj"
    proj.mkdir()
    memd.scaffold(str(proj), "proj")
    assert (proj / ".memory" / "state.md").exists()
    for fname in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        assert not (proj / fname).exists(), f"scaffold() wrote {fname}"
    assert not (proj / ".model").exists(), ".model/ is not memd's to create"


def test_agent_files_written_for_detected_tools(memd, tmp_path, monkeypatch):
    """AGENTS.md is the cross-tool standard and is always written; the
    tool-specific names appear only where that tool is actually in use."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    proj = tmp_path / "proj"
    proj.mkdir()
    created = memd.write_agent_files(str(proj))

    assert (proj / "AGENTS.md").exists()
    assert (proj / "CLAUDE.md").exists(), "~/.claude present but CLAUDE.md missing"
    assert not (proj / "GEMINI.md").exists(), "wrote GEMINI.md with no gemini in use"
    assert ".memory/inbox/" in (proj / "AGENTS.md").read_text()
    assert len(created) == 2


def _claude_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def test_existing_agent_file_backed_up_then_appended(memd, tmp_path, monkeypatch, capsys):
    """A hand-written CLAUDE.md keeps its content, gains the memd section, and
    the original is saved beside it."""
    proj = _claude_home(tmp_path, monkeypatch)
    mine = "# My own instructions\n\nDo not clobber me.\n"
    (proj / "CLAUDE.md").write_text(mine)

    created = memd.write_agent_files(str(proj))

    text = (proj / "CLAUDE.md").read_text()
    assert text.startswith(mine)
    assert text.count(memd.MEMD_SECTION_HEADING) == 1
    assert "Read by Claude Code" in text
    assert "# Project Instructions" not in text
    assert (proj / "CLAUDE.md.bak").read_text() == mine
    assert str(proj / "CLAUDE.md.bak") in created
    assert "CLAUDE.md" in capsys.readouterr().out
    assert (proj / "AGENTS.md").exists()


def test_existing_agent_file_found_without_tool_dir(memd, tmp_path, monkeypatch):
    """An existing GEMINI.md means the tool is in use, detected or not."""
    proj = _claude_home(tmp_path, monkeypatch)
    (proj / "GEMINI.md").write_text("# Gemini rules\n")
    memd.write_agent_files(str(proj))
    assert memd.MEMD_SECTION_HEADING in (proj / "GEMINI.md").read_text()


def test_agent_files_rerun_changes_nothing(memd, tmp_path, monkeypatch):
    proj = _claude_home(tmp_path, monkeypatch)
    (proj / "CLAUDE.md").write_text("# Mine\n")
    memd.write_agent_files(str(proj))
    snapshot = {p.name: p.read_text() for p in proj.iterdir()}

    assert memd.write_agent_files(str(proj)) == []
    assert {p.name: p.read_text() for p in proj.iterdir()} == snapshot


def test_agent_file_symlink_appended_once(memd, tmp_path, monkeypatch):
    """CLAUDE.md -> AGENTS.md is common; the shared file gets one section and
    the link stays a link."""
    proj = _claude_home(tmp_path, monkeypatch)
    (proj / "AGENTS.md").write_text("# Shared\n")
    (proj / "CLAUDE.md").symlink_to("AGENTS.md")

    memd.write_agent_files(str(proj))

    assert (proj / "CLAUDE.md").is_symlink()
    assert (proj / "AGENTS.md").read_text().count(memd.MEMD_SECTION_HEADING) == 1
    assert not (proj / "CLAUDE.md.bak").exists()


def test_existing_backup_never_overwritten(memd, tmp_path, monkeypatch):
    proj = _claude_home(tmp_path, monkeypatch)
    (proj / "CLAUDE.md").write_text("# Mine\n")
    (proj / "CLAUDE.md.bak").write_text("someone else's backup\n")

    memd.write_agent_files(str(proj))

    assert (proj / "CLAUDE.md.bak").read_text() == "someone else's backup\n"
    assert (proj / "CLAUDE.md.bak.1").read_text() == "# Mine\n"


def test_append_preserves_file_mode(memd, tmp_path, monkeypatch):
    proj = _claude_home(tmp_path, monkeypatch)
    f = proj / "CLAUDE.md"
    f.write_text("# Mine")          # no trailing newline
    f.chmod(0o600)
    memd.write_agent_files(str(proj))
    assert f.stat().st_mode & 0o777 == 0o600
    assert f.read_text().startswith("# Mine\n\n" + memd.MEMD_SECTION_HEADING)


def test_auto_discovery_writes_nothing_into_project_root(memd, tmp_path, monkeypatch):
    """The consent boundary: automatic discovery provisions the memory store
    and leaves the workspace alone."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = {"memory_own_repo": True, "git_commit": True, "projects": {}}
    memd.provision_project(cfg, str(proj))  # defaults = the automatic path

    assert (proj / ".memory" / "state.md").exists()
    for fname in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        assert not (proj / fname).exists(), f"auto-discovery wrote {fname}"


def test_explicit_init_upgrades_ignore_after_auto_discovery(memd, tmp_path):
    """A project picked up automatically has the ignore rule only in the
    clone-local .git/info/exclude. Running `memd init` later is the user asking
    for it in the shared .gitignore, and must not be a no-op just because
    .memory already has its own repo."""
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "-C", str(proj), "init", "-q"])
    subprocess.run(["git", "-C", str(proj), "config", "user.email", "t@t"])
    subprocess.run(["git", "-C", str(proj), "config", "user.name", "t"])
    (proj / "code.py").write_text("x\n")
    subprocess.run(["git", "-C", str(proj), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-q", "-m", "init"],
                   capture_output=True)
    cfg = {"memory_own_repo": True, "git_commit": True, "projects": {}}

    memd.provision_project(cfg, str(proj))                       # automatic
    assert ".memory/" in (proj / ".git" / "info" / "exclude").read_text()
    assert not (proj / ".gitignore").exists()

    memd.provision_project(cfg, str(proj), shared_ignore=True)   # explicit init
    assert ".memory/" in (proj / ".gitignore").read_text()


def test_worktree_preserves_unsaved_memory(memd, env, tmp_path, monkeypatch):
    """A linked worktree whose .memory holds uncommitted content must be
    preserved, never replaced by a symlink — memd must not lose memory."""
    import memd.cli
    cfg = memd.load_config()
    cfg["memory_own_repo"] = True
    cfg["git_commit"] = True
    memd.save_config(cfg)

    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "-C", str(main), "init", "-q"])
    subprocess.run(["git", "-C", str(main), "commit", "--allow-empty", "-m", "init"])
    monkeypatch.setattr(sys, "argv", ["memd", "init", str(main), "--name", "main"])
    memd.cli.main()

    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", str(wt), "-b", "feat"])
    # Unsaved memory in the worktree: a real .memory with untracked content.
    wt_mem = wt / ".memory"
    wt_mem.mkdir(parents=True, exist_ok=True)
    (wt_mem / "state.md").write_text("PRECIOUS UNSAVED NOTES")

    monkeypatch.setattr(sys, "argv", ["memd", "init", str(wt), "--name", "wt"])
    memd.cli.main()

    assert not wt_mem.is_symlink()
    assert (wt_mem / "state.md").read_text() == "PRECIOUS UNSAVED NOTES"
