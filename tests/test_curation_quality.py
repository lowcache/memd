"""Tests for curation quality normalizations, deduplication, and repeat collapsing."""

import json
import os

import pytest


# --------------------------------------------------------------------------
# collapse_repeats tests
# --------------------------------------------------------------------------


def test_collapse_repeats_simple(memd):
    """Collapse runs of identical non-blank lines into one line + repeat marker."""
    text = "U: hello\nA: ok\nA: ok\nA: ok\nU: goodbye"
    expected = "U: hello\nA: ok\n[... line repeated 2 more time(s) ...]\nU: goodbye"
    assert memd.digest.collapse_repeats(text) == expected


def test_collapse_repeats_non_adjacent(memd):
    """Non-adjacent duplicate lines are left untouched."""
    text = "U: hello\nA: ok\nU: hello\nA: ok"
    assert memd.digest.collapse_repeats(text) == text


def test_collapse_repeats_blank_lines(memd):
    """Runs of blank lines are not collapsed."""
    text = "U: hello\n\n\n\nA: ok"
    assert memd.digest.collapse_repeats(text) == text


def test_collapse_repeats_empty(memd):
    """Empty and whitespace strings remain unchanged."""
    assert memd.digest.collapse_repeats("") == ""
    assert memd.digest.collapse_repeats("   \n   ") == "   \n   "


# --------------------------------------------------------------------------
# validate normalizations tests
# --------------------------------------------------------------------------


def test_validate_fences_stripped(memd):
    """Fenced markdown bodies are stripped of fences and logged."""
    result = {
        "state_body": "```markdown\n## System State\n\nActive configuration.\n```\n",
        "decisions_body": "```md\n## Decisions\n\nNo constraints.\n```",
        "todo_body": "```\n## Todo\n\n- task 1\n```"
    }
    memory = {"state.md": "", "decisions.md": "", "todo.md": "", "mistakes.md": ""}

    res = memd.validate(result, memory)
    assert res["state_body"] == "## System State\n\nActive configuration."
    assert res["decisions_body"] == "## Decisions\n\nNo constraints."
    assert res["todo_body"] == "## Todo\n\n- task 1"

    with open(memd.LOG_PATH) as f:
        log_text = f.read()
        assert "stripped fences from state_body" in log_text
        assert "stripped fences from decisions_body" in log_text
        assert "stripped fences from todo_body" in log_text


def test_validate_frontmatter_stripped(memd):
    """YAML frontmatter is stripped from curator-emitted bodies and logged."""
    result = {
        "state_body": "---\ntype: state\n---\n## System State\n\nActive configuration.",
    }
    memory = {"state.md": "", "decisions.md": "", "todo.md": "", "mistakes.md": ""}

    res = memd.validate(result, memory)
    assert res["state_body"] == "## System State\n\nActive configuration."
    with open(memd.LOG_PATH) as f:
        assert "stripped curator-emitted frontmatter from state_body" in f.read()


def test_validate_skeleton_nullified(memd):
    """Emitted bodies matching the skeleton are replaced with None and logged."""
    result = {
        "state_body": memd.SKELETONS["state.md"],
        "decisions_body": memd.SKELETONS["decisions.md"],
        "todo_body": memd.SKELETONS["todo.md"]
    }
    memory = {"state.md": "some state", "decisions.md": "some decisions", "todo.md": "some todo", "mistakes.md": ""}

    res = memd.validate(result, memory)
    assert res["state_body"] is None
    assert res["decisions_body"] is None
    assert res["todo_body"] is None

    with open(memd.LOG_PATH) as f:
        log_text = f.read()
        assert "curator returned skeleton for state_body; treated as no-change" in log_text
        assert "curator returned skeleton for decisions_body; treated as no-change" in log_text
        assert "curator returned skeleton for todo_body; treated as no-change" in log_text


def test_validate_clean_passthrough(memd):
    """Clean body passes through byte-identical."""
    result = {
        "state_body": "## System State\n\nActive configuration.",
    }
    memory = {"state.md": "", "decisions.md": "", "todo.md": "", "mistakes.md": ""}

    res = memd.validate(result, memory)
    assert res["state_body"] == "## System State\n\nActive configuration."


def test_validate_shrink_guard_regression(memd):
    """Shrink guard still trips when size drops drastically on large file."""
    result = {"state_body": "x" * 100, "archive_entries": []}
    memory = {"state.md": "S" * 1000, "decisions.md": "", "todo.md": "", "mistakes.md": ""}
    with pytest.raises(memd.CuratorError, match="shrink guard"):
        memd.validate(result, memory)


# --------------------------------------------------------------------------
# dedupe_mistakes tests
# --------------------------------------------------------------------------


def test_dedupe_mistakes_behavior(memd):
    """Duplicates dropped + logged, new kept, non-heading kept, empty dropped."""
    existing = "### 2026-07-01 — test mistake\nsymptom/cause/prevention."
    entries = [
        "### 2026-07-01 — test mistake\nsymptom/cause/prevention.",
        "### 2026-07-02 — new mistake\nsymptom/cause/prevention.",
        "not a markdown heading",
        "",
        "   \n   ",
    ]
    expected = [
        "### 2026-07-02 — new mistake\nsymptom/cause/prevention.",
        "not a markdown heading",
    ]

    assert memd.curator.dedupe_mistakes(entries, existing) == expected
    with open(memd.LOG_PATH) as f:
        assert "duplicate mistakes entry skipped: ### 2026-07-01 — test mistake" in f.read()


# --------------------------------------------------------------------------
# E2E test
# --------------------------------------------------------------------------


def test_e2e_normalization_and_dedupe(memd, e2e, fake_curator, memd_config):
    """End-to-end e2e verification of curation quality normalizations and deduplication."""
    canned = {
        "summary": "e2e quality test",
        "state_body": "```markdown\n---\ntype: state\n---\n## System State\n\nnormalized active config.\n```",
        "decisions_body": None,
        "todo_body": None,
        "mistakes_new_entries": [
            "### 2026-07-04 — e2e test entry\nsymptom/cause/prevention."
        ],
        "archive_entries": [],
    }
    curator = fake_curator(json.dumps(canned))
    memd_config(curator_cmd=[curator, "{model}"])

    res1 = e2e.sync()
    assert res1.returncode == 0
    
    state_content = e2e.read_mem("state.md")
    meta, body = memd.split_frontmatter(state_content)
    assert "normalized active config." in body
    assert "```" not in body
    assert "type: state" not in body

    mistakes_content = e2e.read_mem("mistakes.md")
    assert "### 2026-07-04 — e2e test entry" in mistakes_content
    assert mistakes_content.count("### 2026-07-04 — e2e test entry") == 1

    e2e.append_user_line("trigger second sync")
    res2 = e2e.sync()
    assert res2.returncode == 0

    mistakes_content2 = e2e.read_mem("mistakes.md")
    assert mistakes_content2.count("### 2026-07-04 — e2e test entry") == 1

    log_text = e2e.log_text()
    assert "duplicate mistakes entry skipped: ### 2026-07-04 — e2e test entry" in log_text
