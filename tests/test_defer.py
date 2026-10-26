"""Defer tiny distills until content accumulates."""

import datetime as dt
import json
import os

import pytest

CANNED = json.dumps({
    "summary": "defer test",
    "ops": [{"op": "set_body", "file": "state.md", "body": "## S\n\nfact."}],
    "mistakes_new_entries": [],
    "archive_entries": [],
})


def _project(memd, env, name, turns=2, age_hours=0):
    root = str(env.tmp / name)
    memd.scaffold(root, name)
    src = os.path.join(root, "session.jsonl")
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=age_hours)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with open(src, "w") as f:
        for i in range(turns):
            f.write(json.dumps({
                "type": "user",
                "timestamp": ts,
                "message": {"content": f"turn {i} " + "z" * 200},
            }) + "\n")
    cfg = memd.load_config()
    cfg["projects"][root] = {"name": name, "extra_sources": [src]}
    memd.save_config(cfg)
    return root


@pytest.mark.parametrize("trigger", ["sweep", "session-end", "pre-compact"])
def test_defer_tiny_fresh_digest(memd, env, memd_config, fake_curator, trigger):
    """Tiny fresh digests wait for more content across deferrable triggers."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, f"tiny-{trigger}", turns=2)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger=trigger) is None

    state_file = os.path.join(root, ".memory", "state.md")
    with open(state_file) as f:
        assert "fact." not in f.read()

    src = os.path.join(root, "session.jsonl")
    cursors = memd.load_json(memd.CURSORS_PATH, {})
    assert src not in cursors

    with open(memd.LOG_PATH) as f:
        assert "deferred" in f.read()


def test_defer_expired_distills(memd, env, memd_config, fake_curator):
    """Oldest span at or past max_defer_hours triggers distill despite small digest."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "expired", turns=2, age_hours=48)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="sweep") is True


def test_manual_trigger_does_not_defer(memd, env, memd_config, fake_curator):
    """Non-deferrable triggers like manual always distill."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "manual", turns=2)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="manual") is True


def test_inbox_note_prevents_defer(memd, env, memd_config, fake_curator):
    """Pending inbox notes override deferral to deliver user input promptly."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "inbox", turns=2)
    memd.write_inbox_note(root, "x", source="test")
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="sweep") is True


def test_min_digest_chars_zero_disables_defer(memd, env, memd_config, fake_curator):
    """min_digest_chars=0 disables deferral entirely."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False,
                min_digest_chars=0)
    root = _project(memd, env, "zero-min", turns=2)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="sweep") is True


def test_digest_above_min_chars_does_not_defer(memd, env, memd_config, fake_curator):
    """Digests at or above min_digest_chars distill immediately."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "large-digest", turns=30)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="sweep") is True


def test_deferred_digest_replays(memd, env, memd_config, fake_curator):
    """Deferred digest replays in full on a later pass once eligible."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "replay", turns=2)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="sweep") is None
    state_file = os.path.join(root, ".memory", "state.md")
    with open(state_file) as f:
        assert "fact." not in f.read()

    cfg["max_defer_hours"] = 0
    assert memd.sync_project(cfg, root, trigger="sweep") is True
    with open(state_file) as f:
        assert "fact." in f.read()


def test_defer_expired_edge_cases(memd):
    """defer_expired behavior with empty spans, undated spans, and recent spans."""
    assert memd.sync.defer_expired([], 24) is True

    span_none = memd.digest.Span(source="claude-code", file="f", start=None)
    assert memd.sync.defer_expired([span_none], 24) is True

    start_1h = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    span_recent = memd.digest.Span(source="claude-code", file="f", start=start_1h)
    assert memd.sync.defer_expired([span_recent], 24) is False


def test_dry_run_defer(memd, env, memd_config, fake_curator, capsys):
    """Dry run prints deferred message and returns None without distill."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "dry-run", turns=2)
    cfg = memd.load_config()

    assert memd.sync_project(cfg, root, trigger="sweep", dry_run=True) is None
    out, _ = capsys.readouterr()
    assert "deferred" in out
