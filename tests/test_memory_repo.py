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
