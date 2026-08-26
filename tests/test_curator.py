"""call_curator(): output parsing variants and failure modes, all via fake
shell scripts wired through the curator_cmd config contract."""

import json

import pytest

CURATOR_OBJ = {
    "summary": "unit parse",
    "state_body": "## S\n\nnew fact.",
    "decisions_body": None,
    "todo_body": None,
    "mistakes_new_entries": [],
    "archive_entries": [],
}


@pytest.fixture
def cfg_for(memd, fake_curator):
    def _make(stdout_text, exit_code=0):
        cfg = dict(memd.DEFAULT_CONFIG)
        cfg["curator_cmd"] = [fake_curator(stdout_text, exit_code), "{model}"]
        return cfg
    return _make


def test_claude_envelope(memd, cfg_for):
    # claude -p --output-format json shape: {"result": "<text with JSON>"}
    stdout = json.dumps({"result": json.dumps(CURATOR_OBJ)})
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_bare_json_object(memd, cfg_for):
    # custom curator_cmd contract: bare curator JSON object on stdout
    stdout = json.dumps(CURATOR_OBJ)
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_json_in_fences(memd, cfg_for):
    stdout = "```json\n" + json.dumps(CURATOR_OBJ, indent=2) + "\n```\n"
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_json_embedded_in_prose(memd, cfg_for):
    stdout = ("Sure, here is the updated memory as requested.\n\n"
              + json.dumps(CURATOR_OBJ)
              + "\n\nLet me know if anything else is needed.\n")
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_envelope_with_fenced_result(memd, cfg_for):
    stdout = json.dumps(
        {"result": "```json\n" + json.dumps(CURATOR_OBJ) + "\n```"})
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_nonzero_exit_raises(memd, cfg_for, no_retry_delay):
    cfg = cfg_for(json.dumps(CURATOR_OBJ), exit_code=3)
    # CuratorError subclasses RuntimeError: old-style callers keep working
    with pytest.raises(RuntimeError, match="rc=3"):
        memd.call_curator(cfg, "prompt", "haiku")


def test_no_json_raises(memd, cfg_for, no_retry_delay):
    with pytest.raises(memd.CuratorError, match="no JSON"):
        memd.call_curator(cfg_for("nothing to see here, no braces at all"),
                          "prompt", "haiku")


def test_raw_newline_in_string_is_accepted(memd, cfg_for):
    # Local models routinely emit a literal newline inside a JSON string rather
    # than \n. strict=False accepts it; rejecting would make them unusable.
    body = "## S\n\nport 9090."
    stdout = ('{"summary": "moved", "state_body": "' + body + '",'
              ' "decisions_body": null, "todo_body": null,'
              ' "mistakes_new_entries": [], "archive_entries": []}')
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku")["state_body"] == body


def test_malformed_json_raises_curator_error(memd, cfg_for, no_retry_delay):
    # Regression: a local model (ollama/llama3.1) emitted a bad \escape inside a
    # string. raw_decode's JSONDecodeError used to escape uncaught and crash
    # memd with a traceback; it must surface as a retryable CuratorError.
    bad = '{"summary": "moved", "state_body": "port 9090 \\p not an escape"}'
    with pytest.raises(memd.CuratorError, match="not valid JSON"):
        memd.call_curator(cfg_for(bad), "prompt", "haiku")


def test_truncated_json_raises_curator_error(memd, cfg_for, no_retry_delay):
    # Output cut off mid-object (context limit reached) is the other common
    # weak-model failure; same contract.
    with pytest.raises(memd.CuratorError, match="not valid JSON"):
        memd.call_curator(cfg_for('{"summary": "abc", "state_body": '),
                          "prompt", "haiku")


def test_missing_binary_raises(memd):
    # no no_retry_delay fixture on purpose: a ConfigError must fail fast
    # without retry sleeps, or this test hangs for the full backoff
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg["curator_cmd"] = ["/nonexistent/curator-xyz", "{model}"]
    with pytest.raises(memd.ConfigError, match="curator backend not found"):
        memd.call_curator(cfg, "prompt", "haiku")


# --------------------------------------------------------------------------
# retry / backoff and the error hierarchy (Task D)
# --------------------------------------------------------------------------


@pytest.fixture
def no_retry_delay(memd, monkeypatch):
    monkeypatch.setattr(memd.curator, "CURATOR_RETRY_DELAYS", (0, 0))


def test_error_hierarchy(memd):
    assert issubclass(memd.MemdError, RuntimeError)
    for exc in (memd.ConfigError, memd.DigestError, memd.CuratorError):
        assert issubclass(exc, memd.MemdError)


def test_retry_delays_default(memd):
    assert memd.curator.CURATOR_RETRY_DELAYS == (5, 15)


def test_retry_recovers_from_transient_failure(memd, env, no_retry_delay):
    # backend fails on the first call, succeeds on the second
    d = env.tmp / "curators"
    d.mkdir(exist_ok=True)
    marker = d / "tried-once"
    script = d / "flaky.sh"
    script.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f"if [ -e {marker} ]; then echo '{json.dumps(CURATOR_OBJ)}'\n"
        f"else touch {marker}; exit 1\nfi\n")
    script.chmod(0o755)
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg["curator_cmd"] = [str(script), "{model}"]
    assert memd.call_curator(cfg, "prompt", "haiku") == CURATOR_OBJ
    with open(memd.LOG_PATH) as f:
        assert "failed, retrying in" in f.read()


def test_retry_exhaustion_raises_last_error(memd, cfg_for, no_retry_delay):
    cfg = cfg_for("no json at all", exit_code=0)
    with pytest.raises(memd.CuratorError):
        memd.call_curator(cfg, "prompt", "haiku")
    with open(memd.LOG_PATH) as f:
        # first two attempts logged as retries, third raised
        assert f.read().count("failed, retrying in") == 2


# --------------------------------------------------------------------------
# validate(): section-op normalization & policing
# --------------------------------------------------------------------------

EMPTY_MEMORY = {"state.md": "", "decisions.md": "", "todo.md": "",
                "mistakes.md": ""}


def test_validate_legacy_bodies_convert_to_set_body_ops(memd):
    result = {"summary": "s", "state_body": "## S\n\nfact.",
              "decisions_body": None, "todo_body": None}
    out = memd.validate(result, dict(EMPTY_MEMORY))
    assert out["ops"] == [{"op": "set_body", "file": "state.md",
                           "body": "## S\n\nfact."}]
    assert out["decisions_body"] is None
    assert out["todo_body"] is None
    # transition mirror: callers predating apply_ops read these keys
    assert out["state_body"] == "## S\n\nfact."
    assert set(out) >= {"summary", "ops", "mistakes_new_entries",
                        "archive_entries"}


def test_validate_legacy_empty_string_body_is_noop(memd):
    # legacy sync skipped falsy bodies; conversion must not resurrect them as
    # shrink-guard trips or wholesale wipes
    out = memd.validate({"state_body": ""}, dict(EMPTY_MEMORY))
    assert out["ops"] == []
    assert out["state_body"] is None


def test_validate_native_ops_pass_through(memd):
    ops = [{"op": "replace_section", "file": "todo.md", "heading": "## T",
            "body": "done: x"},
           {"op": "delete_section", "file": "decisions.md",
            "heading": "### Old"}]
    out = memd.validate({"summary": "s", "ops": ops}, dict(EMPTY_MEMORY))
    assert out["ops"] == ops


@pytest.mark.parametrize("bad_op,match", [
    ({"op": "explode", "file": "state.md", "heading": "## A"}, r"unknown op"),
    ({"op": "replace_section", "file": "notes.txt", "heading": "## A",
      "body": "x"}, r"unknown file"),
    ({"op": "replace_section", "file": "state.md", "body": "x"},
     r"missing or non-string heading"),
    ({"op": "delete_section", "file": "state.md"},
     r"missing or non-string heading"),
    ({"op": "append_section", "file": "state.md", "heading": "## A"},
     r"missing or non-string body"),
    ({"op": "set_body", "file": "state.md"},
     r"missing or non-string body"),
    ({"op": "replace_section", "file": "state.md", "heading": 7, "body": "x"},
     r"non-string heading"),
    ({"op": "set_body", "file": "state.md", "body": 7},
     r"non-string body"),
    ({"op": "replace_section", "file": "mistakes.md", "heading": "### A",
      "body": "x"}, r"append-only"),
    ({"op": "append_section", "file": "mistakes.md", "heading": "### A",
      "body": "x"}, r"append-only"),
    ({"op": "delete_section", "file": "mistakes.md", "heading": "### A"},
     r"append-only"),
])
def test_validate_rejects_bad_ops(memd, bad_op, match):
    with pytest.raises(memd.CuratorError, match=match):
        memd.validate({"summary": "s", "ops": [bad_op]}, dict(EMPTY_MEMORY))


def test_validate_rejects_malformed_ops_container(memd):
    with pytest.raises(memd.CuratorError, match="ops is not a list"):
        memd.validate({"ops": {"op": "set_body"}}, dict(EMPTY_MEMORY))
    with pytest.raises(memd.CuratorError, match="ops\\[0\\] is not an object"):
        memd.validate({"ops": ["set_body"]}, dict(EMPTY_MEMORY))


def test_validate_allows_set_body_on_mistakes(memd):
    body = "# Mistake Audit Log (append-only)\n\n### 2026-01-01 — x\nwhy."
    out = memd.validate({"ops": [{"op": "set_body", "file": "mistakes.md",
                                  "body": body}]}, dict(EMPTY_MEMORY))
    assert out["ops"][0]["body"] == body


def test_validate_drops_skeleton_set_body(memd):
    out = memd.validate(
        {"ops": [{"op": "set_body", "file": "todo.md",
                  "body": memd.SKELETONS["todo.md"]}]},
        {"state.md": "", "decisions.md": "", "mistakes.md": "",
         "todo.md": "real todo content"})
    assert out["ops"] == []
    assert out["todo_body"] is None
    with open(memd.LOG_PATH) as f:
        assert "; treated as no-change" in f.read()


def test_validate_strips_fences_and_frontmatter_inside_op_bodies(memd):
    out = memd.validate({"ops": [
        {"op": "set_body", "file": "state.md",
         "body": "```markdown\n---\ntype: state\n---\n## S\n\nfact.\n```"},
        {"op": "replace_section", "file": "todo.md", "heading": "## T",
         "body": "```\nfenced fact.\n```"}]}, dict(EMPTY_MEMORY))
    assert out["ops"][0]["body"] == "## S\n\nfact."
    assert out["ops"][1]["body"] == "fenced fact."
    with open(memd.LOG_PATH) as f:
        log_text = f.read()
        assert log_text.count("stripped fences/frontmatter from") == 2


def big_memory():
    return {"state.md": "S" * 1000, "decisions.md": "", "todo.md": "",
            "mistakes.md": ""}


def test_validate_shrink_guard_on_set_body(memd):
    with pytest.raises(memd.CuratorError, match="shrink guard.*state\\.md"):
        memd.validate({"ops": [{"op": "set_body", "file": "state.md",
                                "body": "x" * 100}]}, big_memory())


def test_validate_shrink_guard_archive_compensates_on_set_body(memd):
    out = memd.validate(
        {"ops": [{"op": "set_body", "file": "state.md", "body": "x" * 100}],
         "archive_entries": [{"source": "state.md", "content": "y" * 400}]},
        big_memory())
    assert len(out["ops"]) == 1


def test_validate_shrink_guard_covers_cumulative_section_ops(memd):
    """Deleting every heading empties a file just as thoroughly as a bad
    set_body; each op looks harmless on its own, so the guard has to see the
    result of applying them all."""
    body = "# System State\n\n## A\n\n" + "x" * 600 + "\n\n## B\n\n" + "y" * 600 + "\n"
    memory = {"state.md": body, "decisions.md": "", "todo.md": "",
              "mistakes.md": ""}
    with pytest.raises(memd.CuratorError,
                       match="shrink guard.*state\\.md section ops"):
        memd.validate({"ops": [
            {"op": "delete_section", "file": "state.md", "heading": "## A"},
            {"op": "delete_section", "file": "state.md", "heading": "## B"}]},
            memory)


def test_validate_section_ops_archive_compensates(memd):
    body = "# System State\n\n## A\n\n" + "x" * 1200 + "\n"
    memory = {"state.md": body, "decisions.md": "", "todo.md": "",
              "mistakes.md": ""}
    out = memd.validate(
        {"ops": [{"op": "delete_section", "file": "state.md",
                  "heading": "## A"}],
         "archive_entries": [{"source": "state.md", "content": "x" * 1200}]},
        memory)
    assert len(out["ops"]) == 1


def test_validate_allows_honest_section_edits(memd):
    """The guard must not fire on ordinary curation: rewriting a section
    shorter is the job."""
    body = "# System State\n\n## A\n\n" + "x" * 1200 + "\n"
    memory = {"state.md": body, "decisions.md": "", "todo.md": "",
              "mistakes.md": ""}
    out = memd.validate({"ops": [{"op": "replace_section", "file": "state.md",
                                  "heading": "## A", "body": "y" * 700}]},
                        memory)
    assert len(out["ops"]) == 1


def test_validate_legacy_shrink_guard_names_the_key(memd):
    memory = {"state.md": "", "decisions.md": "D" * 2000, "todo.md": "",
              "mistakes.md": ""}
    with pytest.raises(memd.CuratorError,
                       match="shrink guard.*decisions_body"):
        memd.validate({"decisions_body": "x" * 10}, memory)


def test_validate_summary_normalized_and_defaults_present(memd):
    out = memd.validate({"summary": 42}, {})
    assert out["summary"] == ""
    for key in ("ops", "mistakes_new_entries", "archive_entries"):
        assert out[key] == []


def test_validate_rejects_bad_mistakes_entries(memd):
    with pytest.raises(memd.CuratorError, match="not a list"):
        memd.validate({"mistakes_new_entries": "### x"}, dict(EMPTY_MEMORY))
    with pytest.raises(memd.CuratorError, match="non-string"):
        memd.validate({"mistakes_new_entries": ["ok", 1]}, dict(EMPTY_MEMORY))


def test_validate_rejects_non_dict(memd):
    with pytest.raises(memd.CuratorError, match="not an object"):
        memd.validate(["nope"], dict(EMPTY_MEMORY))


# --------------------------------------------------------------------------
# apply_ops(): pure section-op application
# --------------------------------------------------------------------------

CANON_BODY = ("# Title\n\nPreamble line.\n\n## One\n\nfirst body\n\n"
              "## Two\n\nsecond body\n\n### Nested\n\nthird body\n")


def test_split_join_roundtrips_byte_identically(memd):
    pre, secs = memd.curator._split_sections_raw(CANON_BODY)
    assert pre == "# Title\n\nPreamble line.\n\n"
    assert [s[0] for s in secs] == ["## One", "## Two", "### Nested"]
    assert memd.curator._join_sections_raw(pre, secs) == CANON_BODY
    ragged = "# T\nline two\n## A\nno trailing newline"
    pre, secs = memd.curator._split_sections_raw(ragged)
    assert memd.curator._join_sections_raw(pre, secs) == ragged


def test_apply_ops_empty_means_unchanged(memd):
    bodies = {"state.md": CANON_BODY, "todo.md": ""}
    assert memd.curator.apply_ops([], bodies) == {"state.md": None,
                                                  "todo.md": None}


def test_apply_ops_replace_with_identical_body_roundtrips(memd):
    out = memd.curator.apply_ops(
        [{"op": "replace_section", "file": "s", "heading": "## Two",
          "body": "second body"}],
        {"s": CANON_BODY})
    assert out["s"] == CANON_BODY


def test_apply_ops_composes_left_to_right_within_a_file(memd):
    out = memd.curator.apply_ops([
        {"op": "delete_section", "file": "s", "heading": "### Nested"},
        {"op": "replace_section", "file": "s", "heading": "## Two",
         "body": "new two"},
        {"op": "append_section", "file": "s", "heading": "Three",
         "body": "appended"},
    ], {"s": CANON_BODY})
    body = out["s"]
    assert "first body" in body                      # untouched section kept
    assert body.startswith("# Title")                # preamble kept
    assert "third body" not in body and "Nested" not in body
    assert "second body" not in body and "new two" in body
    assert "## Three\n\nappended" in body            # prefix normalized to h2


def test_apply_ops_files_are_independent(memd):
    out = memd.curator.apply_ops(
        [{"op": "set_body", "file": "todo.md", "body": "T"},
         {"op": "replace_section", "file": "decisions.md", "heading": "## D",
          "body": "d"}],
        {"state.md": CANON_BODY, "decisions.md": "## D\n\nold\n",
         "todo.md": "x"})
    assert out == {"state.md": None, "decisions.md": "## D\n\nd\n\n",
                   "todo.md": "T"}


def test_apply_ops_append_normalizes_missing_prefix(memd):
    out = memd.curator.apply_ops(
        [{"op": "append_section", "file": "p", "heading": "Fresh", "body": "b"}],
        {"p": "# P\n\ntext"})
    assert out["p"].endswith("\n## Fresh\n\nb\n\n")


def test_apply_ops_append_existing_heading_is_skipped(memd):
    skipped = []
    out = memd.curator.apply_ops(
        [{"op": "append_section", "file": "s", "heading": "One", "body": "x"}],
        {"s": CANON_BODY}, skipped=skipped)
    assert out["s"] is None  # nothing applied -> file left alone
    assert len(skipped) == 1
    assert "already exists" in skipped[0] and "## One" in skipped[0]


@pytest.mark.parametrize("kind", ["replace_section", "delete_section"])
def test_apply_ops_missing_heading_is_skipped_naming_file_and_heading(
        memd, kind):
    skipped = []
    out = memd.curator.apply_ops(
        [{"op": kind, "file": "s", "heading": "## Nope", "body": "x"}],
        {"s": CANON_BODY}, skipped=skipped)
    assert out["s"] is None
    assert skipped == [f"{kind}: no section '## Nope' in s"]


def test_apply_ops_skips_only_the_bad_op(memd):
    """One invented heading must not cost the ops that did resolve — that was
    a whole distill discarded, and the same digest replayed into the same
    model next sweep."""
    skipped = []
    out = memd.curator.apply_ops(
        [{"op": "replace_section", "file": "s", "heading": "## One",
          "body": "kept"},
         {"op": "replace_section", "file": "s", "heading": "## Invented",
          "body": "lost"}],
        {"s": CANON_BODY}, skipped=skipped)
    assert "kept" in out["s"]
    assert "lost" not in out["s"]
    assert len(skipped) == 1


def test_apply_ops_skipped_list_is_optional(memd):
    out = memd.curator.apply_ops(
        [{"op": "delete_section", "file": "s", "heading": "## Nope"}],
        {"s": CANON_BODY})
    assert out["s"] is None


def test_apply_ops_set_body_replaces_wholesale(memd):
    out = memd.curator.apply_ops(
        [{"op": "set_body", "file": "s", "body": "brand new"}],
        {"s": CANON_BODY})
    assert out["s"] == "brand new"
