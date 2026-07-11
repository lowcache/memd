"""End-to-end `memd sync` through the real CLI (subprocess) with a fake
curator script: apply path, append-only mistakes, exit codes, cursor
discipline, and failure behavior with a broken backend."""

import json
import os


def test_sync_applies_memory_and_commits(e2e, memd):
    r = e2e.sync()
    assert r.returncode == 0, r.stderr[-500:]

    # state.md applied with managed frontmatter
    state = e2e.read_mem("state.md")
    assert state.startswith("---")
    assert "e2e marker state fact" in state
    meta, _ = memd.split_frontmatter(state)
    assert meta["last_updated"] == memd.today()
    assert meta["type"] == "state"

    # mistakes entry appended
    assert "e2e test entry" in e2e.read_mem("mistakes.md")

    # git audit commit created in the project repo
    assert "Update project memory" in e2e.git_log()

    # cursor advanced past the transcript content
    assert e2e.cursors().get(e2e.transcript, 0) == \
        os.path.getsize(e2e.transcript)


def test_sync_mistakes_append_only(e2e, memd):
    # pre-seed mistakes.md with an existing entry before any sync
    mem_dir = os.path.join(e2e.project, ".memory")
    os.makedirs(mem_dir, exist_ok=True)
    old_entry = "### 2026-01-01 — pre-existing entry\nold symptom/cause."
    memd.write_memory_file(
        os.path.join(mem_dir, "mistakes.md"),
        "# Mistake Audit Log (append-only)\n\n" + old_entry,
        "proj", "mistakes",
    )

    r = e2e.sync()
    assert r.returncode == 0, r.stderr[-500:]

    mist = e2e.read_mem("mistakes.md")
    assert "pre-existing entry" in mist            # nothing lost
    assert "e2e test entry" in mist                # new entry appended
    assert mist.index("pre-existing entry") < mist.index("e2e test entry")


def test_sync_nothing_new_exits_zero(e2e):
    assert e2e.sync().returncode == 0
    # second run: cursor is caught up, inbox empty -> no-op, still exit 0
    r2 = e2e.sync()
    assert r2.returncode == 0, r2.stderr[-500:]
    assert "nothing new" in e2e.log_text()


def test_sync_broken_backend(e2e, memd_config):
    # establish a good baseline sync first, so a cursor exists to protect
    assert e2e.sync().returncode == 0
    before = e2e.cursors()[e2e.transcript]

    # break the backend, then give the transcript new content
    memd_config(curator_cmd=["/nonexistent/curator", "{model}"])
    e2e.append_user_line("more work arrived")

    r = e2e.sync()
    assert r.returncode == 3  # 3 = curator/distill failure
    assert "Traceback" not in r.stderr, r.stderr[-500:]
    # cursor NOT advanced: the backlog must replay once a backend works
    assert e2e.cursors()[e2e.transcript] == before
    # failure is loud in the memd log
    assert "curator backend not found" in e2e.log_text()


def test_sync_broken_backend_cold_start(e2e, memd_config):
    # broken backend with no prior successful sync: no cursor is created
    memd_config(curator_cmd=["/nonexistent/curator", "{model}"])
    r = e2e.sync()
    assert r.returncode == 3  # 3 = curator/distill failure
    assert "Traceback" not in r.stderr
    assert e2e.transcript not in e2e.cursors()
    assert "curator backend not found" in e2e.log_text()


def test_sync_inbox_notes_consumed_only_on_success(e2e, memd, memd_config):
    inbox_root = e2e.project
    os.makedirs(os.path.join(inbox_root, ".memory", "inbox"), exist_ok=True)
    note = memd.write_inbox_note(inbox_root, "remember this fact",
                                 source="test")
    # failed distill must NOT delete the inbox note
    memd_config(curator_cmd=["/nonexistent/curator", "{model}"])
    assert e2e.sync().returncode == 3
    assert os.path.exists(note)
