"""Silent-ingestion-breakage signal: memd parses other tools' private
transcript formats, so a format change stops it learning without raising.
`memd status` has to say so rather than looking healthy."""

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


def test_unrecognised_transcript_is_flagged(memd, env, memd_config):
    """The real failure: a format memd does not understand. Bytes are read,
    nothing is extracted, and no exception is raised."""
    memd_config(git_commit=False, auto_scaffold=False)
    # Plausible-looking JSONL in a schema memd knows nothing about.
    body = "".join(
        json.dumps({"event": "turn", "payload": {"text": "x" * 200}}) + "\n"
        for _ in range(40)
    )
    root, _ = _project_with_transcript(memd, env, "broken", body)
    assert len(body) > memd.INGEST_STALL_BYTES

    assert memd.sync_project(memd.load_config(), root) is None  # no crash

    meta = memd.load_json(memd.META_PATH, {})[root]
    assert meta["ingest_stalled_bytes"] >= memd.INGEST_STALL_BYTES
    assert meta["ingest_stalled_since"]


def test_status_reports_the_stall(memd, env, memd_config, capsys):
    memd_config(git_commit=False, auto_scaffold=False)
    body = "".join(
        json.dumps({"event": "turn", "payload": {"text": "y" * 200}}) + "\n"
        for _ in range(40)
    )
    root, _ = _project_with_transcript(memd, env, "broken", body)
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
        "summary": "ok", "state_body": "## S\n\nfact.", "decisions_body": None,
        "todo_body": None, "mistakes_new_entries": [], "archive_entries": [],
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


def test_small_unparsed_remnant_is_not_flagged(memd, env, memd_config):
    """A stray truncated line is normal; only a meaningful volume of
    unrecognised content is a signal."""
    memd_config(git_commit=False, auto_scaffold=False)
    root, _ = _project_with_transcript(memd, env, "tiny", "{broken\n")

    memd.sync_project(memd.load_config(), root)

    meta = memd.load_json(memd.META_PATH, {}).get(root, {})
    assert "ingest_stalled_bytes" not in meta


def test_stall_clears_once_parsing_works_again(memd, env, memd_config,
                                               fake_curator):
    """The warning must not be sticky: it describes the present, and the
    backlog replays once parsing recovers."""
    canned = json.dumps({
        "summary": "recovered", "state_body": "## S\n\nback.",
        "decisions_body": None, "todo_body": None,
        "mistakes_new_entries": [], "archive_entries": [],
    })
    memd_config(git_commit=False, auto_scaffold=False)
    unknown = "".join(
        json.dumps({"event": "turn", "payload": {"text": "q" * 200}}) + "\n"
        for _ in range(40)
    )
    root, src = _project_with_transcript(memd, env, "recovers", unknown)
    memd.sync_project(memd.load_config(), root)
    assert memd.load_json(memd.META_PATH, {})[root]["ingest_stalled_bytes"]

    # memd learns the format (or the tool reverts): same file, now parseable.
    memd_config(curator_cmd=[fake_curator(canned), "{model}"],
                git_commit=False, auto_scaffold=False)
    with open(src, "a") as f:
        f.write(json.dumps({"type": "user",
                            "message": {"content": "now parseable"}}) + "\n")

    assert memd.sync_project(memd.load_config(), root) is True
    assert "ingest_stalled_bytes" not in memd.load_json(memd.META_PATH, {})[root]
