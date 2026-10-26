"""Silent-ingestion-breakage signal: memd reads other tools' transcript
formats, so a format change stops it learning without raising. `memd status`
has to say so rather than looking healthy.

Since the universal ingestion adapter, genuinely unknown formats degrade to
the text-log digester instead of stalling — the surviving blindspot is
claude-SHAPE drift: JSONL that routes to the structured parser but yields no
lines from its entries."""

import json
import os

import pytest


def _project_with_transcript(memd, env, name, body):
    """A registered project whose only source is an extra_sources transcript."""
    root = str(env.tmp / name)
    memd.scaffold(root, name)
    src = os.path.join(root, "session.jsonl")
    with open(src, "w") as f:
        f.write(body)
    cfg = memd.load_config()
    cfg["projects"][root] = {"name": name, "extra_sources": [src]}
    memd.save_config(cfg)
    return root, src


def _drift_body(ch):
    """Claude-shaped JSONL whose entries yield nothing: right envelope keys
    (so the sniffer picks the structured parser), unknown content blocks so
    no U/A/T/R line ever comes out."""
    return "".join(
        json.dumps({
            "type": "user",
            "timestamp": "2026-08-24T10:00:00.000Z",
            "message": {"role": "user",
                        "content": [{"type": "future_block", "data": ch * 200}]},
        }) + "\n"
        for _ in range(40)
    )


def test_unrecognised_transcript_is_flagged(memd, env, memd_config):
    """The real failure: claude-shape drift. Bytes are read past the cursor,
    nothing is extracted, and no exception is raised."""
    memd_config(git_commit=False, auto_scaffold=False)
    body = _drift_body("x")
    root, _ = _project_with_transcript(memd, env, "broken", body)
    assert len(body) > memd.INGEST_STALL_BYTES

    assert memd.sync_project(memd.load_config(), root) is None  # no crash

    meta = memd.load_json(memd.META_PATH, {})[root]
    assert meta["ingest_stalled_bytes"] >= memd.INGEST_STALL_BYTES
    assert meta["ingest_stalled_since"]


def test_status_reports_the_stall(memd, env, memd_config, capsys):
    memd_config(git_commit=False, auto_scaffold=False)
    root, _ = _project_with_transcript(memd, env, "broken", _drift_body("y"))
    memd.sync_project(memd.load_config(), root)

    import memd.cli
    memd.cli.cmd_status(memd.load_config())
    out = capsys.readouterr().out
    assert "ingestion" in out
    assert "nothing usable extracted" in out


def test_recognised_transcript_is_not_flagged(memd, env, memd_config,
                                              fake_curator):
    """A format memd does understand must not trip the signal."""
    canned = json.dumps({
        "summary": "ok", "ops": [{"op": "set_body", "file": "state.md",
                                  "body": "## S\n\nfact."}],
        "mistakes_new_entries": [], "archive_entries": [],
    })
    memd_config(curator_cmd=[fake_curator(canned), "{model}"],
                git_commit=False, auto_scaffold=False)
    body = "".join(
        json.dumps({"type": "user", "message": {"content": "hello " + "z" * 200}})
        + "\n" for _ in range(40)
    )
    root, _ = _project_with_transcript(memd, env, "working", body)

    assert memd.sync_project(memd.load_config(), root) is True

    meta = memd.load_json(memd.META_PATH, {})[root]
    assert "ingest_stalled_bytes" not in meta


def test_unknown_tool_format_digests_via_fallback(memd, env, memd_config,
                                                  fake_curator):
    """The adapter contract: a schema memd has never seen is best-effort
    digested as text instead of stalling — the tool keeps learning."""
    canned = json.dumps({
        "summary": "ok", "ops": [{"op": "set_body", "file": "state.md",
                                  "body": "## S\n\nfact."}],
        "mistakes_new_entries": [], "archive_entries": [],
    })
    memd_config(curator_cmd=[fake_curator(canned), "{model}"],
                git_commit=False, auto_scaffold=False)
    body = "".join(
        json.dumps({"event": "turn", "payload": {"text": "q" * 200}}) + "\n"
        for _ in range(40)
    )
    root, _ = _project_with_transcript(memd, env, "foreign", body)

    assert memd.sync_project(memd.load_config(), root) is True
    assert "ingest_stalled_bytes" \
        not in memd.load_json(memd.META_PATH, {}).get(root, {})


def test_small_unparsed_remnant_is_not_flagged(memd, env, memd_config):
    """A stray truncated line is normal; only a meaningful volume of
    unrecognised content is a signal."""
    memd_config(git_commit=False, auto_scaffold=False)
    # claude-envelope entry with no usable content: below the stall threshold
    body = json.dumps(
        {"type": "user", "message": {"role": "user"}}) + "\n"
    root, _ = _project_with_transcript(memd, env, "tiny", body)

    memd.sync_project(memd.load_config(), root)

    meta = memd.load_json(memd.META_PATH, {}).get(root, {})
    assert "ingest_stalled_bytes" not in meta


def test_stall_clears_once_parsing_works_again(memd, env, memd_config,
                                               fake_curator):
    """The warning must not be sticky: it describes the present, and the
    backlog replays once parsing recovers."""
    canned = json.dumps({
        "summary": "recovered", "ops": [{"op": "set_body", "file": "state.md",
                                         "body": "## S\n\nback."}],
        "mistakes_new_entries": [], "archive_entries": [],
    })
    memd_config(git_commit=False, auto_scaffold=False)
    root, src = _project_with_transcript(memd, env, "recovers",
                                         _drift_body("q"))
    memd.sync_project(memd.load_config(), root)
    assert memd.load_json(memd.META_PATH, {})[root]["ingest_stalled_bytes"]

    # the format becomes readable again: same file, parseable entries appended
    memd_config(curator_cmd=[fake_curator(canned), "{model}"],
                git_commit=False, auto_scaffold=False)
    with open(src, "a") as f:
        f.write(json.dumps({"type": "user",
                            "message": {"content": "now parseable"}}) + "\n")

    assert memd.sync_project(memd.load_config(), root) is True
    assert "ingest_stalled_bytes" not in memd.load_json(memd.META_PATH, {})[root]


def test_stall_is_reported_even_with_an_inbox_note(memd, env, memd_config,
                                                   fake_curator):
    """An inbox note is a separate input and cannot vouch for a transcript
    source. Gating the signal on `not spans and not inbox_notes` hid the stall
    in exactly the projects someone was actively leaving notes in."""
    canned = json.dumps({
        "summary": "note only", "ops": [], "mistakes_new_entries": [],
        "archive_entries": [],
    })
    memd_config(curator_cmd=[fake_curator(canned), "{model}"],
                git_commit=False, auto_scaffold=False)
    root, _ = _project_with_transcript(memd, env, "noted", _drift_body("n"))
    memd.inbox.write_inbox_note(root, "remember this", source="test")

    # the distill runs (there is a note to curate) but ingestion still stalled
    assert memd.sync_project(memd.load_config(), root) is True

    meta = memd.load_json(memd.META_PATH, {})[root]
    assert meta["ingest_stalled_bytes"] >= memd.INGEST_STALL_BYTES


def test_binary_source_is_skipped_not_digested(memd, env, memd_config):
    """The text fallback must not treat a database, archive or image as prose:
    mojibake in the prompt costs tokens and advances the cursor as though
    something had been learned."""
    memd_config(git_commit=False, auto_scaffold=False)
    root = str(env.tmp / "binproj")
    memd.scaffold(root, "binproj")
    src = os.path.join(root, "blob.log")
    with open(src, "wb") as f:
        f.write(b"\x00\xff" * 6000)
    cfg = memd.load_config()
    cfg["projects"][root] = {"name": "binproj", "extra_sources": [src]}
    memd.save_config(cfg)

    assert memd.sync_project(memd.load_config(), root) is None

    meta = memd.load_json(memd.META_PATH, {})[root]
    assert meta["ingest_stalled_bytes"] >= memd.INGEST_STALL_BYTES
