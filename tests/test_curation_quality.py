"""Tests for curation quality normalizations, deduplication, repeat collapsing,
and curator-prompt input slimming."""

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
# validate normalizations tests (section-op protocol)
# --------------------------------------------------------------------------


EMPTY_MEMORY = {"state.md": "", "decisions.md": "", "todo.md": "",
                "mistakes.md": ""}


def test_validate_fences_stripped(memd):
    """Fenced markdown bodies are stripped of fences and logged."""
    result = {"ops": [
        {"op": "set_body", "file": "state.md",
         "body": "```markdown\n## System State\n\nActive configuration.\n```"},
        {"op": "set_body", "file": "decisions.md",
         "body": "```md\n## Decisions\n\nNo constraints.\n```"},
        {"op": "set_body", "file": "todo.md",
         "body": "```\n## Todo\n\n- task 1\n```"},
    ]}

    res = memd.validate(result, dict(EMPTY_MEMORY))
    assert res["ops"][0]["body"] == "## System State\n\nActive configuration."
    assert res["ops"][1]["body"] == "## Decisions\n\nNo constraints."
    assert res["ops"][2]["body"] == "## Todo\n\n- task 1"

    with open(memd.LOG_PATH) as f:
        log_text = f.read()
        assert log_text.count("stripped fences/frontmatter from") == 3


def test_validate_frontmatter_stripped(memd):
    """YAML frontmatter is stripped from curator-emitted bodies and logged."""
    result = {"ops": [{"op": "set_body", "file": "state.md",
                       "body": "---\ntype: state\n---\n## System State\n\n"
                               "Active configuration."}]}

    res = memd.validate(result, dict(EMPTY_MEMORY))
    assert res["ops"][0]["body"] == "## System State\n\nActive configuration."
    with open(memd.LOG_PATH) as f:
        assert "stripped fences/frontmatter from" in f.read()


def test_validate_skeleton_nullified(memd):
    """Emitted set_body bodies matching the skeleton are dropped as no-change."""
    result = {"ops": [
        {"op": "set_body", "file": "state.md", "body": memd.SKELETONS["state.md"]},
        {"op": "set_body", "file": "decisions.md", "body": memd.SKELETONS["decisions.md"]},
        {"op": "set_body", "file": "todo.md", "body": memd.SKELETONS["todo.md"]},
    ]}
    memory = {"state.md": "some state", "decisions.md": "some decisions",
              "todo.md": "some todo", "mistakes.md": ""}

    res = memd.validate(result, memory)
    assert res["ops"] == []

    with open(memd.LOG_PATH) as f:
        log_text = f.read()
        assert log_text.count("; treated as no-change") == 3


def test_validate_clean_passthrough(memd):
    """Clean body passes through byte-identical."""
    result = {"ops": [{"op": "set_body", "file": "state.md",
                       "body": "## System State\n\nActive configuration."}]}

    res = memd.validate(result, dict(EMPTY_MEMORY))
    assert res["ops"][0]["body"] == "## System State\n\nActive configuration."


def test_validate_shrink_guard_regression(memd):
    """Shrink guard still trips when a set_body drops a large file drastically."""
    result = {"ops": [{"op": "set_body", "file": "state.md", "body": "x" * 100}],
              "archive_entries": []}
    memory = {"state.md": "S" * 1000, "decisions.md": "", "todo.md": "",
              "mistakes.md": ""}
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
# build_prompt(): mistakes slimming + previous distills
# --------------------------------------------------------------------------

MISTAKES_FIXTURE = (
    "# Mistake Audit Log (append-only)\n\n"
    "### 2026-07-01 — old mistake\nOLD-BODY-MARKER.\n\n"
    "### 2026-07-02 — mid mistake\nMID-BODY-MARKER.\n\n"
    "### 2026-07-03 — newest mistake\nNEWEST-BODY-MARKER."
)


def make_prompt(memd, memory, **kw):
    return memd.build_prompt(dict(memd.DEFAULT_CONFIG), "/proj", "proj",
                             memory, "DIGEST TEXT", [], **kw)


def test_prompt_mistakes_block_slimmed(memd):
    """All headings stay visible (duplicate detection matches them verbatim);
    historical entry bodies vanish; the newest entry remains as an exemplar."""
    p = make_prompt(memd, {"state.md": "", "decisions.md": "", "todo.md": "",
                           "mistakes.md": MISTAKES_FIXTURE})
    assert ("===== CURRENT mistakes.md (headings-only; full log is "
            "append-only and archived in the repo) =====") in p
    for heading in ("### 2026-07-01 — old mistake",
                    "### 2026-07-02 — mid mistake",
                    "### 2026-07-03 — newest mistake"):
        assert heading in p
    assert "NEWEST-BODY-MARKER" in p
    assert "OLD-BODY-MARKER" not in p
    assert "MID-BODY-MARKER" not in p


def test_prompt_mistakes_block_caps_and_counts_omissions(memd):
    # bodies never enter the block, so the cap is a function of heading count:
    # ~40 chars each means a few hundred headings are needed to overflow it
    headings = "\n".join(f"### 2026-07-01 — numbered mistake {m}"
                         for m in range(1, 201))
    body = "# Mistake Audit Log (append-only)\n\n" + headings
    p = make_prompt(memd, {"state.md": "", "decisions.md": "", "todo.md": "",
                           "mistakes.md": body})
    block = p[p.index("headings-only"):p.index("===== CURRENT todo.md")]
    assert "[... omitted:" in block and "over budget ...]" in block
    # oldest headings shed first; the newest survive with an exemplar
    assert "numbered mistake 1\n" not in block
    assert "numbered mistake 200" in block
    assert len(block) <= 3000 + 120  # cap + ellipsis-marker slack


def test_prompt_previous_distills_placement(memd):
    distills = ["2026-08-24T09:00 [sweep/haiku] pruned done todo items",
                "2026-08-25T10:11 [session-end/sonnet] archived stale state"]
    p = make_prompt(memd, {"state.md": "", "decisions.md": "", "todo.md": "",
                           "mistakes.md": ""}, recent_distills=distills)
    marker = ("===== PREVIOUS DISTILLS (already applied — do not redo or "
              "re-archive their prunes) =====")
    i_marker = p.index(marker)
    assert p.index("===== CURRENT todo.md") < i_marker
    assert i_marker < p.index("===== SESSION DIGEST")
    assert "2026-08-25T10:11 [session-end/sonnet]" in p


def test_prompt_omits_previous_distills_when_absent_or_empty(memd):
    memory = {"state.md": "", "decisions.md": "", "todo.md": "",
              "mistakes.md": ""}
    # the charter mentions the section by name, so match the full marker
    assert "===== PREVIOUS DISTILLS" not in make_prompt(memd, memory)
    assert "===== PREVIOUS DISTILLS" not in make_prompt(
        memd, memory, recent_distills=[])


def test_charter_documents_op_protocol_and_digest_format(memd):
    c = memd.CHARTER
    for token in ("replace_section", "append_section", "delete_section",
                  "set_body", "MINIMAL set of ops", "empty ops list",
                  '"heading"', "last resort"):
        assert token in c, token
    # digest-format notes: defensive ("when present"), but present
    assert "--- session span | ... ---" in c
    assert '"  SUB> "' in c
    assert '"[... omitted: ...]"' in c
    # anti-reapply rule points at the PREVIOUS DISTILLS section as evidence
    assert "PREVIOUS DISTILLS" in c


# --------------------------------------------------------------------------
# E2E test
# --------------------------------------------------------------------------


def test_e2e_normalization_and_dedupe(memd, e2e, fake_curator, memd_config):
    """End-to-end e2e verification of curation quality normalizations and deduplication."""
    canned = {
        "summary": "e2e quality test",
        "ops": [{"op": "set_body", "file": "state.md",
                 "body": "```markdown\n---\ntype: state\n---\n## System State\n\nnormalized active config.\n```"}],
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
