"""Partial curator view: memory above curator_view_chars is shown to the
curator as every heading plus only the bodies relevant to the digest; ops on
hidden sections are dropped. Small memory keeps the full, unchanged prompt."""

import datetime as dt
import importlib
import json
import os

import pytest

FILLER = "".join(
    f"## Filler decision {i} (2026-01-0{1 + i % 9})\n\n"
    f"Filler body {i}: zq{i}x widget gearbox sprocket calibration notes that are "
    f"unrelated to networking, kept long enough to matter for budgets.\n\n"
    for i in range(30))
DECISIONS = "# Architecture Decisions\n\n" + FILLER
STATE = ("# System State\n\n## Services\n\n- api: localhost:8080\n"
         "- worker: memd-worker.service\n\n## Hardware\n\n- ryzen 7840u, 32G ram\n")
TODO = "# Open Tasks\n\n## Release 0.5\n\n- draft changelog\n"
DIGEST = "U: move the api port 8080 to 9090, unifi owns 8080\nA: done, api on 9090"


def _memory():
    return {"state.md": STATE, "decisions.md": DECISIONS, "todo.md": TODO,
            "mistakes.md": ""}


def _cfg(memd, **kw):
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg.update(kw)
    return cfg


def _shown_chars(memd, memory, view):
    return sum(len(h) + len(b) for f, heads in view.items()
               for h, b in memd.split_sections(memory[f]) if h in heads)


def test_default_budget(memd):
    assert memd.DEFAULT_CONFIG["curator_view_chars"] == 20000


def test_small_memory_gets_full_view(memd):
    assert memd.curator.select_view(_cfg(memd), _memory(), DIGEST, []) is None


def test_large_memory_selects_relevant_sections(memd):
    mem = _memory()
    view = memd.curator.select_view(_cfg(memd, curator_view_chars=2000), mem,
                                     DIGEST, [])
    assert set(view) == {"state.md", "decisions.md", "todo.md"}
    assert "## Services" in view["state.md"]
    assert "## Hardware" not in view["state.md"]
    assert len(view["decisions.md"]) < 5
    assert _shown_chars(memd, mem, view) <= 2000


def test_inbox_notes_drive_selection_too(memd):
    view = memd.curator.select_view(_cfg(memd, curator_view_chars=2000),
                                    _memory(), "", ["the ryzen box needs more ram"])
    assert "## Hardware" in view["state.md"]


def test_recent_sections_fill_remaining_budget(memd):
    mem = _memory()
    today = dt.date.today().isoformat()
    mem["decisions.md"] += f"## Fresh unrelated choice ({today})\n\nbanana.\n"
    view = memd.curator.select_view(_cfg(memd, curator_view_chars=2000), mem,
                                    DIGEST, [])
    assert f"## Fresh unrelated choice ({today})" in view["decisions.md"]


def test_full_view_prompt_is_unchanged(memd):
    args = (_cfg(memd), "/p", "p", _memory(), DIGEST, [])
    assert memd.build_prompt(*args, view=None) == memd.build_prompt(*args)


def test_partial_prompt_hides_unselected_bodies(memd):
    mem = _memory()
    view = {"state.md": {"## Services"}, "decisions.md": set(), "todo.md": set()}
    p = memd.build_prompt(_cfg(memd), "/p", "p", mem, DIGEST, [], view=view)
    assert p.count("PARTIAL VIEW") == 3
    assert "localhost:8080" in p                 # shown body
    assert "ryzen 7840u" not in p                # hidden body
    assert "[body not shown: " in p
    for i in range(30):                          # every heading still listed
        assert f"## Filler decision {i} (" in p
    assert "zq7x" not in p
    assert "draft changelog" not in p


def test_oversized_marker_only_on_large_shown_sections(memd):
    mem = _memory()
    mem["state.md"] += "\n## Big\n\n" + "word " * 700
    view = {"state.md": {"## Services", "## Big"}, "decisions.md": set(),
            "todo.md": set()}
    p = memd.build_prompt(_cfg(memd), "/p", "p", mem, DIGEST, [], view=view)
    assert p.count("[oversized section:") == 1
    i = p.index("## Big")
    assert "[oversized section:" in p[i:i + 200]


def _result(*ops):
    return {"summary": "s", "ops": list(ops), "mistakes_new_entries": [],
            "archive_entries": []}


def test_validate_drops_ops_on_hidden_sections(memd):
    mem = _memory()
    view = {"state.md": {"## Services"}, "decisions.md": set(), "todo.md": set()}
    out = memd.validate(_result(
        {"op": "replace_section", "file": "state.md", "heading": "## Services",
         "body": "- api: localhost:9090"},
        {"op": "replace_section", "file": "state.md", "heading": "## Hardware",
         "body": "- invented"},
        {"op": "delete_section", "file": "decisions.md",
         "heading": "## Filler decision 3 (2026-01-04)"},
        {"op": "append_section", "file": "decisions.md", "heading": "## Ports",
         "body": "api on 9090"},
        {"op": "set_body", "file": "todo.md", "body": "# Open Tasks\n\n## X\n\ny"},
    ), mem, view=view)
    kept = [(o["op"], o["file"], o.get("heading")) for o in out["ops"]]
    assert kept == [("replace_section", "state.md", "## Services"),
                    ("append_section", "decisions.md", "## Ports")]


def test_validate_without_view_is_unchanged(memd):
    out = memd.validate(_result(
        {"op": "replace_section", "file": "state.md", "heading": "## Hardware",
         "body": "- 64G ram"}), _memory())
    assert len(out["ops"]) == 1


def test_charter_documents_partial_view(memd):
    assert "PARTIAL VIEW" in memd.CHARTER
    assert "[body not shown" in memd.CHARTER
    assert "oversized" in memd.CHARTER


def test_rank_orders_sections(memd):
    recall = importlib.import_module("memd.recall")
    secs = [{"heading": "## A", "body": "nothing here"},
            {"heading": "## Ports", "body": "api on 9090"}]
    ranked = recall.rank(secs, "api 9090")
    assert ranked[0][0] == 1 and ranked[0][1] > 0
    assert all(i != 0 for i, _ in ranked)


# --------------------------------------------------------------------------
# end to end through sync_project
# --------------------------------------------------------------------------


@pytest.fixture
def big_project(memd, env, memd_config):
    def _make(curator_out, **cfg_kw):
        d = env.tmp / "curators"
        d.mkdir(exist_ok=True)
        stdin_copy = d / "stdin.txt"
        payload = d / "out.json"
        payload.write_text(json.dumps(curator_out))
        script = d / "capture.sh"
        script.write_text(f"#!/bin/sh\ncat > {stdin_copy}\ncat {payload}\n")
        script.chmod(0o755)
        memd_config(curator_cmd=[str(script), "{model}"], git_commit=False,
                    auto_scaffold=False, min_digest_chars=0, **cfg_kw)
        root = str(env.tmp / "big")
        memd.scaffold(root, "big")
        mem = os.path.join(root, ".memory")
        for fname, body in _memory().items():
            if body:
                memd.write_memory_file(os.path.join(mem, fname), body, "big",
                                       fname.split(".")[0])
        src = env.tmp / "s.jsonl"
        src.write_text("".join(json.dumps(
            {"type": "user", "message": {"content": line}}) + "\n"
            for line in DIGEST.splitlines()))
        cfg = memd.load_config()
        cfg["projects"][root] = {"name": "big", "extra_sources": [str(src)]}
        memd.save_config(cfg)
        return root, stdin_copy
    return _make


def test_sync_uses_partial_view_for_large_memory(memd, big_project):
    root, stdin_copy = big_project(_result(), curator_view_chars=2000)
    assert memd.sync_project(memd.load_config(), root) in (True, None)
    sent = stdin_copy.read_text()
    assert "(PARTIAL VIEW:" in sent
    assert "localhost:8080" in sent
    assert "zq7x" not in sent
    with open(memd.LOG_PATH) as f:
        assert "partial view" in f.read()


def test_sync_full_view_under_budget(memd, big_project):
    root, stdin_copy = big_project(_result())
    memd.sync_project(memd.load_config(), root)
    sent = stdin_copy.read_text()
    # the charter (also on stdin) names PARTIAL VIEW; the file header doesn't
    assert "(PARTIAL VIEW:" not in sent
    assert "zq7x" in sent


def test_sync_applies_shown_ops_and_drops_hidden(memd, big_project):
    root, _ = big_project(_result(
        {"op": "replace_section", "file": "state.md", "heading": "## Services",
         "body": "- api: localhost:9090 (unifi owns 8080)"},
        {"op": "replace_section", "file": "state.md", "heading": "## Hardware",
         "body": "- invented hardware"}), curator_view_chars=2000)
    assert memd.sync_project(memd.load_config(), root) is True
    state = memd.read_memory(root)["state.md"]
    assert "localhost:9090" in state
    assert "ryzen 7840u" in state
    assert "invented hardware" not in state
