"""One memory store, one project. Two registered paths can reach the same
.memory directory: a bind mount (/home/u/x and /persist/home/u/x) or a linked
worktree whose .memory symlinks to the main store. realpath() tells them apart,
so they used to be distilled as separate projects under separate locks, and two
curators could rewrite the same files at once. Identity is now the store's
(st_dev, st_ino); the extra paths become aliases of one entry.

A symlinked .memory stands in for a bind mount here: both give a second path
whose store stats to the same inode."""

import json
import os

import pytest


def _store(root):
    os.makedirs(os.path.join(root, ".memory"), exist_ok=True)
    return root


def _alias(root, target):
    """A second project path whose .memory is the target's store."""
    os.makedirs(root, exist_ok=True)
    os.symlink(os.path.join(target, ".memory"), os.path.join(root, ".memory"))
    return root


@pytest.fixture
def pair(env):
    canon = _store(os.path.realpath(str(env.tmp / "p")))
    alias = _alias(os.path.realpath(str(env.tmp / "persist" / "p")), canon)
    return canon, alias


def _write_projects(env, projects, **extra):
    d = env.xdg_config / "memd"
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"global_root": str(env.tmp / "ghome"), "auto_scaffold": False,
           "projects": projects, **extra}
    (d / "config.json").write_text(json.dumps(cfg))


# --- lock --------------------------------------------------------------------

def test_lock_is_shared_by_paths_to_one_store(memd, pair):
    canon, alias = pair
    fd = memd.project_lock(canon)
    assert fd is not None
    try:
        assert memd.project_lock(alias) is None
    finally:
        fd.close()


def test_lock_distinct_stores_do_not_contend(memd, env):
    a = _store(str(env.tmp / "a"))
    b = _store(str(env.tmp / "b"))
    fa = memd.project_lock(a)
    fb = memd.project_lock(b)
    try:
        assert fa is not None and fb is not None
    finally:
        fa.close()
        fb.close()


def test_sync_through_alias_respects_canonical_lock(memd, env, pair):
    canon, alias = pair
    # pending inbox + a curator that always fails: unlocked, this returns False
    _write_projects(env, {canon: {"name": "p", "extra_sources": []}},
                    curator_cmd=["false"], min_digest_chars=0, git_commit=False)
    cfg = memd.load_config()
    memd.write_inbox_note(canon, "a fact", source="test")
    assert memd.sync_project(cfg, alias, trigger="manual") is False
    memd.write_inbox_note(canon, "another fact", source="test")
    fd = memd.project_lock(canon)
    try:
        assert memd.sync_project(cfg, alias, trigger="manual") is None
    finally:
        fd.close()


# --- registry ----------------------------------------------------------------

def test_load_config_merges_entries_sharing_a_store(memd, env, pair):
    canon, alias = pair
    _write_projects(env, {
        alias: {"name": "p-persist", "extra_sources": ["/y/*.jsonl"]},
        canon: {"name": "p", "extra_sources": ["/x/*.jsonl"]},
    })
    projects = memd.load_config()["projects"]
    assert list(projects) == [canon]          # shortest path is canonical
    entry = projects[canon]
    assert entry["name"] == "p"
    assert entry["aliases"] == [alias]
    assert entry["extra_sources"] == ["/x/*.jsonl", "/y/*.jsonl"]


def test_load_config_leaves_unrelated_and_missing_stores_alone(memd, env):
    a = _store(os.path.realpath(str(env.tmp / "a")))
    b = _store(os.path.realpath(str(env.tmp / "b")))
    gone = os.path.realpath(str(env.tmp / "gone"))
    _write_projects(env, {a: {"name": "a", "extra_sources": []},
                          b: {"name": "b", "extra_sources": []},
                          gone: {"name": "gone", "extra_sources": []}})
    projects = memd.load_config()["projects"]
    assert set(projects) == {a, b, gone}
    assert not any(e.get("aliases") for e in projects.values())


def test_global_entry_is_never_merged(memd, env):
    gr = _store(os.path.realpath(str(env.tmp / "ghome")))
    other = _alias(os.path.realpath(str(env.tmp / "elsewhere")), gr)
    _write_projects(env, {gr: {"name": "global", "extra_sources": [], "global": True},
                          other: {"name": "x", "extra_sources": []}})
    projects = memd.load_config()["projects"]
    assert projects[gr].get("global") is True
    assert not projects[gr].get("aliases")
    assert other in projects


def test_register_alias_joins_existing_entry(memd, env, pair):
    canon, alias = pair
    _write_projects(env, {canon: {"name": "p", "extra_sources": []}})
    cfg = memd.load_config()
    entry = memd.register(cfg, alias, "ignored")
    assert entry is cfg["projects"][canon]
    assert alias not in cfg["projects"]
    assert cfg["projects"][canon]["aliases"] == [alias]
    # persisted, and idempotent
    memd.register(memd.load_config(), alias)
    saved = json.loads((env.xdg_config / "memd" / "config.json").read_text())
    assert saved["projects"][canon]["aliases"] == [alias]
    assert list(saved["projects"]) == [canon]


def test_find_project_maps_alias_to_canonical(memd, env, pair):
    canon, alias = pair
    _write_projects(env, {canon: {"name": "p", "extra_sources": []}})
    cfg = memd.load_config()
    memd.register(cfg, alias)
    assert memd.find_project(cfg, alias) == canon
    assert memd.find_project(cfg, os.path.join(alias, "src")) == canon


def test_exclude_alias_drops_only_the_alias(memd, env, pair, monkeypatch):
    import sys
    import memd.cli
    canon, alias = pair
    _write_projects(env, {canon: {"name": "p", "extra_sources": [],
                                  "aliases": [alias]}})
    monkeypatch.setattr(sys, "argv", ["memd", "exclude", alias])
    memd.cli.main()
    saved = json.loads((env.xdg_config / "memd" / "config.json").read_text())
    assert canon in saved["projects"]
    assert alias not in saved["projects"][canon].get("aliases", [])
    assert alias in saved["exclude"]


# --- sources -----------------------------------------------------------------

def test_transcripts_from_every_alias_are_collected(memd, env, pair):
    canon, alias = pair
    nested = _store(os.path.join(canon, "sub"))
    _write_projects(env, {canon: {"name": "p", "extra_sources": [],
                                  "aliases": [alias]},
                          nested: {"name": "sub", "extra_sources": []}})
    cfg = memd.load_config()
    claude = env.home / ".claude" / "projects"
    want = []
    for root in (canon, alias):
        d = claude / memd.encode_claude_dir(root)
        d.mkdir(parents=True)
        (d / "s.jsonl").write_text("{}\n")
        want.append(str(d / "s.jsonl"))
    # the nested project's sessions, launched through the alias path, are its own
    sub_via_alias = claude / memd.encode_claude_dir(os.path.join(alias, "sub"))
    sub_via_alias.mkdir(parents=True)
    (sub_via_alias / "s.jsonl").write_text("{}\n")

    got = memd.transcript_files(cfg, canon)
    assert sorted(got) == sorted(want)


def test_antigravity_conversation_attributed_to_alias_counts(memd, env, pair):
    canon, alias = pair
    conv = env.tmp / "ag" / "conversations"
    conv.mkdir(parents=True)
    db = conv / "c.db"
    db.write_bytes(b"")
    state = os.path.dirname(memd.AG_INDEX_PATH)
    os.makedirs(state, exist_ok=True)
    with open(memd.AG_INDEX_PATH, "w") as f:
        json.dump({str(db): {"project": alias, "scanned_idx": 99}}, f)
    _write_projects(env, {canon: {"name": "p", "extra_sources": [],
                                  "aliases": [alias]}},
                    antigravity_dir=str(env.tmp / "ag"))
    cfg = memd.load_config()
    assert memd.project_ag_dbs(cfg, canon) == [str(db)]


# --- docs --------------------------------------------------------------------

def test_aliases_documented():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "docs", "configuration.md"), encoding="utf-8") as f:
        assert "`projects.<path>.aliases`" in f.read()


def test_find_project_through_alias_reaches_nested_project(memd, env, pair):
    """/persist/.../repo/sub must resolve to the nested project registered as
    /home/.../repo/sub, not to the outer one."""
    canon, alias = pair
    nested = _store(os.path.join(canon, "sub"))
    _write_projects(env, {canon: {"name": "p", "extra_sources": [],
                                  "aliases": [alias]},
                          nested: {"name": "sub", "extra_sources": []}})
    cfg = memd.load_config()
    assert memd.find_project(cfg, os.path.join(alias, "sub", "src")) == nested
    assert memd.find_project(cfg, os.path.join(alias, "other")) == canon
