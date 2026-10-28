"""digest_jsonl(): claude-format transcript digestion and cursor semantics."""

import json
import os


def write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def ts(h, m, s=0):
    """Claude-style ISO UTC timestamp."""
    return f"2026-08-24T{h:02d}:{m:02d}:{s:02d}.000Z"


ENTRIES = [
    {"type": "user", "timestamp": ts(14, 0, 0),
     "sessionId": "abc123-def456", "cwd": "/home/x/repo",
     "message": {"role": "user", "content": "please fix the bug"}},
    # injected reminder-style user text is skipped
    {"type": "user", "timestamp": ts(14, 0, 1),
     "message": {"role": "user",
                 "content": "<system-reminder>ignore me</system-reminder>"}},
    {"type": "assistant", "timestamp": ts(14, 5, 0),
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "working on it"},
         {"type": "tool_use", "name": "Bash",
          "input": {"command": "ls -la", "timeout": 30}},
     ]}},
    {"type": "user", "timestamp": ts(14, 5, 2),
     "message": {"role": "user", "content": [
         {"type": "tool_result", "content": "file1 file2"},
         {"type": "text", "text": "followup question"},
         {"type": "text", "text": "<local-command>skipped</local-command>"},
     ]}},
    # sidechain and meta entries are ignored unless sidechains=True (meta
    # is never wanted)
    {"type": "assistant", "timestamp": ts(14, 6, 0), "isSidechain": True,
     "message": {"content": [{"type": "text", "text": "SIDECHAIN-NOISE"}]}},
    {"type": "user", "timestamp": ts(14, 6, 1), "isMeta": True,
     "message": {"content": "META-NOISE"}},
    # non-JSON garbage lines are tolerated (written manually below)
]


def test_digest_jsonl_content(memd, env):
    path = str(env.tmp / "t.jsonl")
    write_jsonl(path, ENTRIES)
    with open(path, "a") as f:
        f.write("not json at all\n")

    span, new_offset = memd.digest_jsonl(path, 0)
    lines = span.lines

    assert "U: please fix the bug" in lines
    assert "A: working on it" in lines
    assert "U: followup question" in lines
    # tool call: salient key surfaced verbatim, non-salient keys dropped
    assert "T: Bash ls -la" in lines
    assert '"timeout"' not in "\n".join(lines)
    # result: size-aware placeholder, not a raw echo
    assert "R: [11 ch] file1 file2" in lines
    # user lines appear before assistant reply order-wise
    assert lines.index("U: please fix the bug") < lines.index("A: working on it")

    joined = "\n".join(lines)
    assert "system-reminder" not in joined
    assert "local-command" not in joined
    assert "SIDECHAIN-NOISE" not in joined
    assert "META-NOISE" not in joined

    # span metadata harvested from entries
    assert span.source == "claude-code"
    assert span.session_id == "abc123-def456"
    assert span.cwd == "/home/x/repo"
    assert span.start == ts(14, 0, 0)
    assert span.end == ts(14, 6, 1)
    assert not span.approx

    rendered = memd.render_span(span)
    head = rendered.splitlines()[0]
    assert head.startswith("--- session span | ")
    assert "source=claude-code" in head
    # session id truncated to 12 chars
    assert "session=abc123-def456" not in head
    assert "session=abc123-def45 |" in head
    assert "2026-08-24 14:00→2026-08-24 14:06 UTC" in head
    assert "cwd=/home/x/repo" in head
    assert "≈" not in head

    assert new_offset == os.path.getsize(path)


def test_digest_jsonl_offset_resume(memd, env):
    path = str(env.tmp / "t.jsonl")
    write_jsonl(path, ENTRIES)
    _, off1 = memd.digest_jsonl(path, 0)

    # repeated call from the returned offset yields nothing new
    d2, off2 = memd.digest_jsonl(path, off1)
    assert d2 is None
    assert off2 == off1

    # appended content is picked up from the cursor, without old lines
    with open(path, "a") as f:
        f.write(json.dumps({"type": "user",
                            "message": {"content": "brand new ask"}}) + "\n")
    d3, off3 = memd.digest_jsonl(path, off1)
    assert d3.lines == ["U: brand new ask"]
    assert off3 == os.path.getsize(path)


def test_digest_jsonl_missing_file(memd, env):
    d, off = memd.digest_jsonl(str(env.tmp / "nope.jsonl"), 7)
    assert d is None
    assert off == 7


def test_digest_jsonl_unreadable_file_logs(memd, env):
    p = env.tmp / "locked.jsonl"
    p.write_text('{"type": "user"}\n')
    p.chmod(0)
    d, off = memd.digest_jsonl(str(p), 0)
    # cursor unchanged (content replays next sweep), but the failure is loud
    assert (d, off) == (None, 0)
    with open(memd.LOG_PATH) as f:
        assert "digest error" in f.read()


# --------------------------------------------------------------------------
# sidechain capture
# --------------------------------------------------------------------------


def test_sidechains_captured_prefixed_and_capped(memd, env):
    path = str(env.tmp / "t.jsonl")
    long_sub = "x" * 2000
    write_jsonl(path, ENTRIES[:-2] + [
        {"type": "assistant", "isSidechain": True,
         "message": {"content": [{"type": "text", "text": long_sub}]}},
        {"type": "assistant", "isSidechain": True,
         "message": {"content": [
             {"type": "tool_use", "name": "Read",
              "input": {"file_path": "/a/b.py"}},
         ]}},
        {"type": "user", "isSidechain": True,
         "message": {"content": [{"type": "tool_result",
                                  "content": "y" * 500}]}},
    ])
    span, _ = memd.digest_jsonl(path, 0, sidechains=True)
    subs = [ln for ln in span.lines if ln.startswith("  SUB> ")]
    assert any(ln == "  SUB> A: " + "x" * 800 for ln in subs)
    assert any(ln.startswith("  SUB> T: Read /a/b.py") for ln in subs)
    assert any(ln.startswith("  SUB> R: [500 ch] ") for ln in subs)
    # main-chain lines carry no prefix
    assert "U: please fix the bug" in span.lines


def test_sidechains_off_by_default(memd, env):
    path = str(env.tmp / "t.jsonl")
    write_jsonl(path, ENTRIES)
    span, off = memd.digest_jsonl(path, 0)
    assert not any("SUB>" in ln for ln in span.lines)


# --------------------------------------------------------------------------
# span ordering + budget triage
# --------------------------------------------------------------------------


def test_sort_spans_chronological_undated_last(memd, env):
    import memd.digest as dg
    s1 = dg.Span(source="a", file="a", start=ts(15, 0), end=ts(15, 0))
    s2 = dg.Span(source="b", file="b", start=ts(14, 0), end=ts(14, 0))
    s3 = dg.Span(source="c", file="c")
    assert [s.file for s in dg.sort_spans([s1, s2, s3])] == ["b", "a", "c"]


def test_truncate_spans_drops_lowest_score_keeps_newest(memd, env):
    import memd.digest as dg
    # one huge assistant line: low score (no user intent) but big bytes, so
    # dropping it alone frees enough budget for the two valuable spans
    filler = ["A: " + "word " * 900]
    rich = ["U: do the thing", "A: did the thing"]   # user intent scores high
    newest = ["A: latest words"]
    spans = [
        dg.Span(source="claude-code", file="old.log",
                start=ts(9, 0), end=ts(9, 30), lines=list(filler)),
        dg.Span(source="claude-code", file="mid.log",
                start=ts(10, 0), end=ts(10, 10), lines=list(rich)),
        dg.Span(source="claude-code", file="new.log",
                start=ts(11, 0), end=ts(11, 5), lines=list(newest)),
    ]
    tiny_cap = len(dg.render_span(spans[1])) + len(dg.render_span(spans[2])) \
        + 10  # old.log cannot fit alongside the others
    kept, labels = dg.truncate_spans(spans, tiny_cap)
    assert [s.file for s in kept] == ["mid.log", "new.log"]
    assert len(labels) == 1
    assert "09:00" in labels[0] and "low signal" in labels[0]

    # the newest span survives even when it is the only span and oversize:
    # nothing droppable, caller falls back to cap_digest on the rendered join
    big = dg.Span(source="x", file="only.log", start=ts(8, 0), end=ts(8, 1),
                  lines=["A: bulk " * 50])
    kept2, labels2 = dg.truncate_spans([big], cap=len(dg.render_span(big)) - 5)
    assert [s.file for s in kept2] == ["only.log"] and labels2 == []


# --------------------------------------------------------------------------
# universal ingestion adapter
# --------------------------------------------------------------------------


LOG_TEXT = """session started

> user: why is the build red

assistant: missing dep, installing

tool: ran pip install -r reqs.txt

plain narration line without a role marker"""


def test_text_log_role_tagging(memd, env):
    p = env.tmp / "opencode-session.log"
    p.write_text(LOG_TEXT)
    span, off = memd.digest_text_log(str(p), 0)
    assert span.source == "text-log"
    assert span.approx and span.start == span.end
    assert span.session_id == "opencode-ses"
    assert "U: why is the build red" in span.lines
    assert "A: missing dep, installing" in span.lines
    assert "T: ran pip install -r reqs.txt" in span.lines
    assert "plain narration line without a role marker" in span.lines
    assert off == os.path.getsize(str(p))


def test_sniff_routes_jsonl_vs_plain_log(memd, env):
    jl = env.tmp / "claude.jsonl"
    write_jsonl(jl, [{"type": "user", "timestamp": ts(10, 0),
                      "message": {"content": "from jsonl"}}])
    span, _ = memd.sniff_and_digest(str(jl), 0)
    assert span.source == "claude-code"
    assert "U: from jsonl" in span.lines

    lg = env.tmp / "tool.log"
    lg.write_text("> user: hello from plain log\n")
    span2, _ = memd.sniff_and_digest(str(lg), 0)
    assert span2.source == "text-log"
    assert "U: hello from plain log" in span2.lines

    # unreadable source degrades to (None, cursor), loudly
    d, cur = memd.sniff_and_digest(str(env.tmp / "nope.log"), 42)
    assert d is None and cur == 42


def test_digest_source_dispatch(memd, env):
    import sqlite3
    db = str(env.tmp / "conv.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, "
                "step_type INTEGER, step_payload BLOB)")
    con.execute("INSERT INTO steps VALUES (1, 14, ?)",
                (b"\x00Please refactor the sync module today\x00",))
    con.commit()
    con.close()
    cfg = {}
    span, idx = memd.digest_source(cfg, db, 0)
    assert span.source == "antigravity" and idx >= 1
    # non-.db sources go through the sniffer
    lg = env.tmp / "plain.log"
    lg.write_text("assistant: text-log path\n")
    span2, _ = memd.digest_source(cfg, str(lg), 0)
    assert span2.source == "text-log"


def test_fmt_chars_is_size_aware(memd):
    assert memd.digest.fmt_chars(11) == "11"
    assert memd.digest.fmt_chars(999) == "999"
    assert memd.digest.fmt_chars(18234) == "18.2k"


def test_text_log_rejects_binary_and_advances_the_cursor(memd, env):
    """Non-text bytes are consumed, not digested: no mojibake in the prompt,
    and the source is not re-read every sweep. The bytes still count as
    scanned, so `memd status` can report the source as unusable."""
    blob = env.tmp / "blob.log"
    blob.write_bytes(b"\x00\xff" * 500)
    span, off = memd.digest.digest_text_log(str(blob), 0)
    assert span is None
    assert off == 1000


def test_text_log_keeps_ordinary_prose_with_accents(memd, env):
    """The printable floor must not reject legitimate UTF-8."""
    lg = env.tmp / "utf8.log"
    lg.write_text("user: café, naïve, 日本語 — still prose\n")
    span, _ = memd.digest.digest_text_log(str(lg), 0)
    assert span is not None
    assert span.lines == ["U: café, naïve, 日本語 — still prose"]


def test_text_log_read_is_capped(memd, env, monkeypatch):
    """An arbitrary log is not read whole into memory; the tail rides the
    next sweep instead."""
    monkeypatch.setattr(memd.digest, "TEXT_LOG_READ_CAP", 64)
    lg = env.tmp / "big.log"
    lg.write_text("user: " + "a" * 500 + "\n")
    span, off = memd.digest.digest_text_log(str(lg), 0)
    assert off == 64
    assert span is not None and len(span.lines) == 1


def test_digest_jsonl_assistant_string_content(memd, env):
    # claude-code writes assistant text as blocks; other writers of the same
    # shape use a plain string, which was silently dropped
    path = str(env.tmp / "s.jsonl")
    write_jsonl(path, [
        {"type": "user", "timestamp": ts(9, 0), "message": {"content": "move the api"}},
        {"type": "assistant", "timestamp": ts(9, 1),
         "message": {"content": "moved it to 9090"}},
    ])
    span, _ = memd.digest_jsonl(path, 0)
    assert "A: moved it to 9090" in span.lines
