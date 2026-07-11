"""update_json(): the locked read-modify-write used for shared registries
(cursors.json, meta.json, ag_index.json). Covers merge-with-disk semantics,
failed-mutate rollback, and lost-update prevention under concurrent
multi-process writers (the reason the flock + re-read-inside-lock exists).

Cursor rollback after a failed distill is covered end-to-end in
test_sync_e2e.py::test_sync_broken_backend.
"""

import json
import multiprocessing
import os

import pytest


# --------------------------------------------------------------------------
# unit behavior
# --------------------------------------------------------------------------


def test_update_json_creates_file_and_applies_mutation(memd, env):
    path = str(env.tmp / "reg.json")
    out = memd.update_json(path, lambda d: d.update(k="v"))
    assert out == {"k": "v"}
    with open(path) as f:
        assert json.load(f) == {"k": "v"}


def test_update_json_merges_with_on_disk_state(memd, env):
    path = str(env.tmp / "reg.json")
    with open(path, "w") as f:
        json.dump({"a": 1}, f)
    out = memd.update_json(path, lambda d: d.update(b=2))
    assert out == {"a": 1, "b": 2}


def test_update_json_rereads_disk_not_stale_snapshot(memd, env):
    # A value written between two update_json calls must survive the second
    # call: the merge base is the on-disk state read inside the lock.
    path = str(env.tmp / "reg.json")
    memd.update_json(path, lambda d: d.update(first=1))
    with open(path, "w") as f:
        json.dump({"first": 1, "outside": "kept"}, f)
    out = memd.update_json(path, lambda d: d.update(second=2))
    assert out == {"first": 1, "outside": "kept", "second": 2}


def test_update_json_failed_mutate_leaves_file_untouched(memd, env):
    path = str(env.tmp / "reg.json")
    with open(path, "w") as f:
        json.dump({"a": 1}, f)

    def boom(d):
        d["a"] = 999
        raise ValueError("apply failed")

    with pytest.raises(ValueError):
        memd.update_json(path, boom)
    with open(path) as f:
        assert json.load(f) == {"a": 1}


def test_update_json_creates_state_lock(memd, env):
    memd.update_json(str(env.tmp / "reg.json"), lambda d: None)
    assert os.path.exists(memd.STATE_LOCK_PATH)


def test_project_lock_contention_returns_none_and_logs(memd, env):
    fd = memd.project_lock("/some/project")
    assert fd is not None
    try:
        # flock is per open-file-description: a second open of the same lock
        # file in this same process still contends
        assert memd.project_lock("/some/project") is None
        with open(memd.LOG_PATH) as f:
            assert "project locked, skipping: /some/project" in f.read()
    finally:
        fd.close()


# --------------------------------------------------------------------------
# concurrency: parallel increments must not lose updates
# --------------------------------------------------------------------------

N_PROCS = 6
INCS_EACH = 25


def _inc_proc(reg_path, count):
    # fork context: the parent's freshly-imported (env-isolated) memd package
    # is inherited via sys.modules.
    import memd as m
    for _ in range(count):
        m.update_json(reg_path, lambda d: d.update(n=d.get("n", 0) + 1))


def test_update_json_concurrent_writers_lose_nothing(memd, env):
    # Without the flock + re-read-inside-lock, parallel read-modify-write
    # cycles would clobber each other and the final count would fall short.
    reg_path = str(env.tmp / "reg.json")
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=_inc_proc, args=(reg_path, INCS_EACH))
        for _ in range(N_PROCS)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    assert not any(p.is_alive() for p in procs), "writers did not finish"

    with open(reg_path) as f:
        assert json.load(f)["n"] == N_PROCS * INCS_EACH
