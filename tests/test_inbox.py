"""write_inbox_note() / collect_inbox(): atomic publish, unique names,
source headers, truncation, dotfile hygiene, and a multi-process stress test
with a concurrent sweeping reader (the sync-style read-then-delete pattern)."""

import multiprocessing
import os
import time


def make_root(env, name="iproj"):
    root = str(env.tmp / name)
    os.makedirs(os.path.join(root, ".memory", "inbox"))
    return root


# --------------------------------------------------------------------------
# unit behavior
# --------------------------------------------------------------------------


def test_write_inbox_note_returns_published_path(memd, env):
    root = make_root(env)
    p = memd.write_inbox_note(root, "hello note")
    assert os.path.isfile(p)
    assert os.path.dirname(p) == os.path.join(root, ".memory", "inbox")
    assert open(p).read() == "hello note\n"


def test_write_inbox_note_source_header(memd, env):
    root = make_root(env)
    p = memd.write_inbox_note(root, "body text", source="unit-test")
    content = open(p).read()
    assert content.startswith("<!-- source: unit-test -->\n")
    assert "body text" in content


def test_write_inbox_note_unique_names_many_calls(memd, env):
    root = make_root(env)
    paths = [memd.write_inbox_note(root, f"note {i}") for i in range(200)]
    assert len(set(paths)) == 200
    inbox = os.path.join(root, ".memory", "inbox")
    files = [f for f in os.listdir(inbox) if f.endswith(".md")]
    assert len(files) == 200


def test_write_inbox_note_no_temp_leftovers(memd, env):
    root = make_root(env)
    for i in range(20):
        memd.write_inbox_note(root, f"note {i}", source="t")
    mem = os.path.join(root, ".memory")
    leftover = [f for f in os.listdir(mem) if f.startswith(".inbox-")]
    assert leftover == []


def test_collect_inbox_truncates_long_notes(memd, env):
    root = make_root(env)
    memd.write_inbox_note(root, "x" * 6000, source="trunc-test")
    notes, paths = memd.collect_inbox(root)
    assert len(notes) == 1
    assert "truncated at 4000 chars" in notes[0]
    assert len(notes[0]) < 6000


def test_collect_inbox_skips_dotfiles_and_foreign_extensions(memd, env):
    root = make_root(env)
    inbox = os.path.join(root, ".memory", "inbox")
    open(os.path.join(inbox, ".hidden.md"), "w").write("dot")
    open(os.path.join(inbox, "note.log"), "w").write("log")
    open(os.path.join(inbox, ".gitkeep"), "w").close()
    open(os.path.join(inbox, "real.md"), "w").write("real note")
    open(os.path.join(inbox, "plain.txt"), "w").write("txt note")
    notes, paths = memd.collect_inbox(root)
    assert len(notes) == 2
    assert {os.path.basename(p) for p in paths} == {"real.md", "plain.txt"}


def test_collect_inbox_missing_dir(memd, env):
    notes, paths = memd.collect_inbox(str(env.tmp / "no-such-project"))
    assert notes == [] and paths == []


# --------------------------------------------------------------------------
# concurrency stress: several writer processes + a sweeping reader
# --------------------------------------------------------------------------

N_WRITERS = 6
NOTES_EACH = 30
BODY = "note body line\n" * 5


def _writer_proc(root, count, source):
    # fork context: the parent's freshly-imported (env-isolated) memd package
    # is inherited via sys.modules.
    import memd as m
    for _ in range(count):
        m.write_inbox_note(root, BODY, source=source)


def test_inbox_concurrent_writers_with_sweeping_reader(memd, env):
    root = make_root(env, "cproj")
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=_writer_proc,
                    args=(root, NOTES_EACH, f"writer-{i}"))
        for i in range(N_WRITERS)
    ]
    for p in procs:
        p.start()

    # Sweeping reader: read-then-delete like sync does. Every note observed
    # must be complete — never empty or partial.
    seen, bad = 0, 0
    deadline = time.time() + 12
    while time.time() < deadline:
        notes, paths = memd.collect_inbox(root)
        for n, pth in zip(notes, paths):
            note_body = n.split("\n", 1)[1]
            if BODY not in note_body:
                bad += 1
            seen += 1
            os.remove(pth)
        if not any(p.is_alive() for p in procs) and not paths:
            break
    for p in procs:
        p.join(timeout=10)
    assert not any(p.is_alive() for p in procs), "writers did not finish in time"

    notes, paths = memd.collect_inbox(root)
    seen += len(paths)
    assert bad == 0, f"{bad} partial/empty notes ingested"
    assert seen == N_WRITERS * NOTES_EACH, \
        f"lost notes: saw {seen} of {N_WRITERS * NOTES_EACH}"
    for pth in paths:
        os.remove(pth)
    leftover = [f for f in os.listdir(os.path.join(root, ".memory"))
                if f.startswith(".inbox-")]
    assert leftover == [], f"temp files not cleaned: {leftover}"
