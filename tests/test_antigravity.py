"""antigravity adapter: digest_ag_db() / ag_max_idx() against an in-test
SQLite fixture DB mimicking the observed conversation schema — a `steps`
table with idx / step_type / step_payload columns, protobuf-ish BLOB payloads
from which printable-ASCII runs >= 12 chars are extracted.

Observed step_type meanings: 14=user, 33=assistant, 15=tool call, 17=error.
"""

import os
import sqlite3


def make_ag_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE steps (idx INTEGER PRIMARY KEY, "
        "step_type INTEGER, step_payload BLOB)"
    )
    con.executemany("INSERT INTO steps VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return path


ROWS = [
    # user: protobuf-ish framing bytes around a printable run
    (1, 14, b"\x08\x02\x1a$Please refactor the sync module today\x00"),
    # assistant: a short (<12 chars) run that must be ignored, plus the text
    (2, 33, b"junk\x01I refactored sync and added tests\x02\x05"),
    # tool call: JSON descriptor embedded mid-run
    (3, 15, b"\x00\x10pfx{\"toolSummary\": \"Ran pytest on tests/\"}\x00tail"),
    # error
    (4, 17, b"\x00\x00Error: build failed with exit 1\x00"),
    # payload with no printable run >= 12 chars: contributes no line
    (5, 14, b"\x00short\x00tiny\x00"),
    # unknown step type: idx still advances the cursor
    (6, 99, b"\x00some long printable run that is ignored anyway\x00"),
]


def test_digest_ag_db_extracts_text_and_cursor(memd, env):
    db = make_ag_db(str(env.tmp / "conv.db"), ROWS)
    digest, new_idx = memd.digest_ag_db(db, 0)
    lines = digest.splitlines()

    assert "U: Please refactor the sync module today" in lines
    assert "A: I refactored sync and added tests" in lines
    assert "T: Ran pytest on tests/" in lines
    assert any(ln.startswith("E: ") and "build failed with exit 1" in ln
               for ln in lines)
    # step 5 produced nothing; step 6 (unknown type) produced nothing
    assert len(lines) == 4
    # cursor is the max idx seen, including line-less steps
    assert new_idx == 6


def test_digest_ag_db_resume_from_cursor(memd, env):
    db = make_ag_db(str(env.tmp / "conv.db"), ROWS)
    _, idx = memd.digest_ag_db(db, 0)
    d2, idx2 = memd.digest_ag_db(db, idx)
    assert d2 == ""
    assert idx2 == idx

    # partial resume only sees rows past the cursor
    d3, idx3 = memd.digest_ag_db(db, 2)
    assert "Please refactor" not in d3
    assert "I refactored sync" not in d3
    assert "T: Ran pytest on tests/" in d3.splitlines()
    assert idx3 == 6


def test_ag_max_idx(memd, env):
    db = make_ag_db(str(env.tmp / "conv.db"), ROWS)
    assert memd.ag_max_idx(db) == 6


def test_ag_max_idx_empty_table(memd, env):
    db = make_ag_db(str(env.tmp / "empty.db"), [])
    assert memd.ag_max_idx(db) == 0


def test_ag_max_idx_missing_db(memd, env):
    assert memd.ag_max_idx(str(env.tmp / "no-such.db")) == 0


def test_digest_ag_db_missing_db(memd, env):
    d, idx = memd.digest_ag_db(str(env.tmp / "no-such.db"), 5)
    assert d == ""
    assert idx == 5


def test_digest_ag_db_logs_unknown_step_type(memd, env):
    db = make_ag_db(str(env.tmp / "conv.db"), ROWS)
    memd.digest_ag_db(db, 0)
    with open(memd.LOG_PATH) as f:
        assert "unknown ag step type: 99" in f.read()


def test_ag_workspace_non_sqlite_file(memd, env):
    # garbage file -> sqlite3.DatabaseError inside, None out
    bad = env.tmp / "not-a-db.db"
    bad.write_bytes(b"this is not a sqlite file at all")
    assert memd.ag_workspace(str(bad)) is None


def test_ag_workspace_missing_expected_table(memd, env):
    # valid sqlite DB without trajectory_metadata_blob -> OperationalError
    db = make_ag_db(str(env.tmp / "conv.db"), ROWS)
    assert memd.ag_workspace(db) is None


# --------------------------------------------------------------------------
# _pb_strings: printable-run heuristic over protobuf-ish blobs
# --------------------------------------------------------------------------


def test_pb_strings_extracts_long_printable_run(memd):
    blob = b"\x08\x02\x1aExactly this readable sentence\x00\xff"
    assert memd._pb_strings(blob) == [b"Exactly this readable sentence"]


def test_pb_strings_no_printable_runs(memd):
    assert memd._pb_strings(b"\x00\x01\x02\xfe\xff" * 10) == []


def test_pb_strings_eleven_char_run_below_threshold(memd):
    # boundary: 11 printable chars must not match, 12 must
    assert memd._pb_strings(b"\x00elevenchars\x00") == []
    assert memd._pb_strings(b"\x00twelve-chars\x00") == [b"twelve-chars"]


def test_pb_strings_multiple_runs_and_none_blob(memd):
    blob = b"first run of text\x00\x01second run of text\xff"
    assert memd._pb_strings(blob) == [b"first run of text",
                                      b"second run of text"]
    assert memd._pb_strings(None) == []
