"""memd.recall: stateless BM25 search over memory sections (active files and
archive), query-term extraction, and the `memd recall` CLI."""

import importlib
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMD_PATH = os.path.join(REPO_ROOT, "memd.py")

DECISIONS = """# Architecture Decisions

## Use SQLite FTS5 (2026-10-01)

Rejected: full text search via grep. Chosen for recall ranking.

## Port allocation (2026-10-02)

The api listens on 9090 because unifi owns 8080.

## Logging

Structured JSON logs via the stdlib logging module.
"""

STATE = """# System State

## Services

- worker: memd-worker.service, restarts on failure
"""

ARCHIVE = """---
type: archive
---

<!-- archived 2026-08-01 (curator distill) -->

## from decisions.md

Old postgres queue decision: SKIP LOCKED polling.
"""


@pytest.fixture
def recall(memd):
    return importlib.import_module("memd.recall")


@pytest.fixture
def proj(memd, env):
    root = str(env.tmp / "proj")
    memd.scaffold(root, "proj")
    mem = os.path.join(root, ".memory")
    memd.write_memory_file(os.path.join(mem, "decisions.md"), DECISIONS,
                           "proj", "decisions")
    memd.write_memory_file(os.path.join(mem, "state.md"), STATE, "proj", "state")
    os.makedirs(os.path.join(mem, "archive"), exist_ok=True)
    with open(os.path.join(mem, "archive", "2026-08.md"), "w") as f:
        f.write(ARCHIVE)
    return root


def test_finds_section_by_body_term(recall, proj):
    hits = recall.search(proj, "unifi")
    assert hits[0]["file"] == "decisions.md"
    assert hits[0]["heading"] == "## Port allocation (2026-10-02)"
    assert "9090" in hits[0]["body"]
    assert hits[0]["archived"] is False
    assert hits[0]["date"] == "2026-10-02"
    assert hits[0]["score"] > 0


def test_archive_included_and_excludable(recall, proj):
    hits = recall.search(proj, "postgres")
    assert hits and hits[0]["file"] == "archive/2026-08.md"
    assert hits[0]["archived"] is True
    assert hits[0]["heading"] == "## from decisions.md"
    assert recall.search(proj, "postgres", include_archive=False) == []


def test_heading_match_outranks_body_match(recall, proj, memd):
    mem = os.path.join(proj, ".memory")
    memd.write_memory_file(os.path.join(mem, "todo.md"),
                           "# Open Tasks\n\n## Rotate secrets\n\n- pending\n",
                           "proj", "todo")
    memd.write_memory_file(os.path.join(mem, "state.md"),
                           STATE + "- logs: rotate weekly via logrotate\n",
                           "proj", "state")
    hits = recall.search(proj, "rotate")
    assert [h["heading"] for h in hits[:2]] == ["## Rotate secrets",
                                                "## Services"]


def test_dotted_tokens_match_their_parts(recall, proj, memd):
    memd.write_memory_file(os.path.join(proj, ".memory", "todo.md"),
                           "# Open Tasks\n\n## Deploy\n\n- enable "
                           "services.memd\n", "proj", "todo")
    assert recall.search(proj, "memd")[0]["heading"] == "## Deploy"
    assert recall.search(proj, "services.memd")[0]["heading"] == "## Deploy"


def test_reads_current_file_contents(recall, proj, memd):
    p = os.path.join(proj, ".memory", "decisions.md")
    memd.write_memory_file(p, "# Architecture Decisions\n\n## Queue\n\n"
                              "Use redis streams.\n", "proj", "decisions")
    assert recall.search(proj, "unifi", include_archive=False) == []
    assert recall.search(proj, "redis")[0]["heading"] == "## Queue"


def test_ranked_and_limited(recall, proj):
    hits = recall.search(proj, "decision api unifi 9090 postgres worker",
                         limit=2)
    assert len(hits) == 2
    assert hits[0]["score"] >= hits[1]["score"]


def test_hostile_query_does_not_raise(recall, proj):
    out = recall.search(proj, 'foo-bar\'s "x" (y) AND OR NOT * ^ : {}')
    assert isinstance(out, list)


def test_empty_query_returns_nothing(recall, proj):
    assert recall.search(proj, "") == []
    assert recall.search(proj, "  !! ?? ") == []


def test_missing_memory_dir(recall, env):
    assert recall.search(str(env.tmp / "nowhere"), "anything") == []


def test_search_writes_nothing(recall, proj, memd):
    before_mem = sorted(os.listdir(os.path.join(proj, ".memory")))
    before_state = os.path.exists(memd.STATE_DIR) and sorted(
        os.listdir(memd.STATE_DIR))
    recall.search(proj, "unifi")
    assert sorted(os.listdir(os.path.join(proj, ".memory"))) == before_mem
    assert (os.path.exists(memd.STATE_DIR) and sorted(
        os.listdir(memd.STATE_DIR))) == before_state


def test_inbox_and_skeleton_text_not_indexed(recall, proj, memd):
    memd.write_inbox_note(proj, "zebra crossing note", source="test")
    assert recall.search(proj, "zebra") == []


def test_terms_from_text_ranks_by_frequency(recall):
    text = ("Moved the api port from 8080 to 9090 because unifi owns 8080. "
            "The api restart worked.")
    terms = recall.terms_from_text(text, limit=40)
    assert terms[:2] == ["api", "8080"]
    for t in ("unifi", "port", "9090"):
        assert t in terms
    for stop in ("the", "from", "because", "to"):
        assert stop not in terms
    assert len(recall.terms_from_text(text, limit=3)) == 3


def test_terms_keep_dotted_and_path_tokens(recall):
    terms = recall.terms_from_text("enable services.memd in config/server.yaml")
    assert "services.memd" in terms
    assert "server.yaml" in terms


def _cli(*args, cwd=None):
    return subprocess.run([sys.executable, MEMD_PATH, "recall", *args],
                          capture_output=True, text=True, timeout=60, cwd=cwd)


def test_cli_prints_hits(memd, proj):
    r = _cli("unifi", "port", "--project", proj)
    assert r.returncode == 0, r.stderr
    assert "[decisions.md] ## Port allocation (2026-10-02)" in r.stdout
    assert "9090" in r.stdout


def test_cli_no_matches(memd, proj):
    r = _cli("zzzqqq", "--project", proj)
    assert r.returncode == 0
    assert "no matches" in r.stdout


def test_cli_no_archive_flag(memd, proj):
    r = _cli("postgres", "--project", proj, "--no-archive")
    assert "no matches" in r.stdout


def test_cli_outside_project_errors(memd, env):
    r = _cli("anything", cwd=str(env.tmp))
    assert r.returncode == 2
    assert "not in a memd project" in r.stderr


def test_brief_note_mentions_recall(memd):
    assert "memd recall" in memd.BRIEF_NOTE
