"""digest_jsonl(): claude-format transcript digestion and cursor semantics."""

import json
import os


def write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


ENTRIES = [
    {"type": "user",
     "message": {"role": "user", "content": "please fix the bug"}},
    # injected reminder-style user text is skipped
    {"type": "user",
     "message": {"role": "user",
                 "content": "<system-reminder>ignore me</system-reminder>"}},
    {"type": "assistant",
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "working on it"},
         {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
     ]}},
    {"type": "user",
     "message": {"role": "user", "content": [
         {"type": "tool_result", "content": "file1 file2"},
         {"type": "text", "text": "followup question"},
         {"type": "text", "text": "<local-command>skipped</local-command>"},
     ]}},
    # sidechain and meta entries are ignored entirely
    {"type": "assistant", "isSidechain": True,
     "message": {"content": [{"type": "text", "text": "SIDECHAIN-NOISE"}]}},
    {"type": "user", "isMeta": True,
     "message": {"content": "META-NOISE"}},
    # non-JSON garbage lines are tolerated (written manually below)
]


def test_digest_jsonl_content(memd, env):
    path = str(env.tmp / "t.jsonl")
    write_jsonl(path, ENTRIES)
    with open(path, "a") as f:
        f.write("not json at all\n")

    digest, new_offset = memd.digest_jsonl(path, 0)
    lines = digest.splitlines()

    assert "U: please fix the bug" in lines
    assert "A: working on it" in lines
    assert "U: followup question" in lines
    assert any(ln.startswith("T: Bash") and '"command": "ls -la"' in ln
               for ln in lines)
    assert "R: file1 file2" in lines
    # user lines appear before assistant reply order-wise
    assert lines.index("U: please fix the bug") < lines.index("A: working on it")

    assert "system-reminder" not in digest
    assert "local-command" not in digest
    assert "SIDECHAIN-NOISE" not in digest
    assert "META-NOISE" not in digest

    assert new_offset == os.path.getsize(path)


def test_digest_jsonl_offset_resume(memd, env):
    path = str(env.tmp / "t.jsonl")
    write_jsonl(path, ENTRIES)
    _, off1 = memd.digest_jsonl(path, 0)

    # repeated call from the returned offset yields nothing new
    d2, off2 = memd.digest_jsonl(path, off1)
    assert d2 == ""
    assert off2 == off1

    # appended content is picked up from the cursor, without old lines
    with open(path, "a") as f:
        f.write(json.dumps({"type": "user",
                            "message": {"content": "brand new ask"}}) + "\n")
    d3, off3 = memd.digest_jsonl(path, off1)
    assert d3 == "U: brand new ask"
    assert off3 == os.path.getsize(path)


def test_digest_jsonl_missing_file(memd, env):
    d, off = memd.digest_jsonl(str(env.tmp / "nope.jsonl"), 7)
    assert d == ""
    assert off == 7


def test_digest_jsonl_unreadable_file_logs(memd, env):
    p = env.tmp / "locked.jsonl"
    p.write_text('{"type": "user"}\n')
    p.chmod(0)
    d, off = memd.digest_jsonl(str(p), 0)
    # cursor unchanged (content replays next sweep), but the failure is loud
    assert (d, off) == ("", 0)
    with open(memd.LOG_PATH) as f:
        assert "digest error" in f.read()
