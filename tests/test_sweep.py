"""cmd_sweep(): parallel project catch-up, failure tallying, stderr progress,
--jobs flag; log() rotation with numbered backups."""

import json
import os
import subprocess
import sys
import time

CANNED = json.dumps({
    "summary": "sweep distill",
    "state_body": "## State\n\nswept marker.",
    "decisions_body": None,
    "todo_body": None,
    "mistakes_new_entries": [],
    "archive_entries": [],
})

MEMD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memd.py"
)


def _mk_project(memd, env, name):
    """Scaffolded project with one pending inbox note (sweep trigger)."""
    root = str(env.tmp / name)
    memd.scaffold(root, name)
    memd.write_inbox_note(root, f"note for {name}", source="test")
    return root


def test_sweep_distills_all_pending_projects(memd, env, fake_curator,
                                             memd_config, capsys):
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False)
    cfg = memd.load_config()
    roots = [_mk_project(memd, env, f"proj-{i}") for i in range(3)]
    for r in roots:
        memd.register(cfg, r)

    assert memd.cmd_sweep(cfg, jobs=3) == 0
    for r in roots:
        with open(os.path.join(r, ".memory", "state.md")) as f:
            assert "swept marker." in f.read()
    err = capsys.readouterr().err
    # progress goes to stderr: 3 distilled projects + the idle global root
    assert err.count("distilled") == 3
    assert "[1/4] syncing" in err and "[4/4] syncing" in err


def test_sweep_counts_failed_distills(memd, env, memd_config, capsys):
    memd_config(curator_cmd=["/nonexistent/curator", "{model}"],
                git_commit=False, auto_scaffold=False)
    cfg = memd.load_config()
    root = _mk_project(memd, env, "broken")
    memd.register(cfg, root)

    assert memd.cmd_sweep(cfg) == 1
    assert "FAILED" in capsys.readouterr().err
    # the pending inbox note survives the failed distill
    inbox = os.listdir(os.path.join(root, ".memory", "inbox"))
    assert any(f.endswith(".md") for f in inbox)


def test_sweep_runs_projects_in_parallel(memd, env, memd_config):
    # 3 projects x 1s curator each: serial would be >= 3s
    d = env.tmp / "curators"
    d.mkdir(exist_ok=True)
    script = d / "slow.sh"
    script.write_text(f"#!/bin/sh\ncat > /dev/null\nsleep 1\necho '{CANNED}'\n")
    script.chmod(0o755)
    memd_config(curator_cmd=[str(script), "{model}"], git_commit=False,
                auto_scaffold=False)
    cfg = memd.load_config()
    for i in range(3):
        memd.register(cfg, _mk_project(memd, env, f"par-{i}"))

    t0 = time.monotonic()
    assert memd.cmd_sweep(cfg, jobs=4) == 0
    elapsed = time.monotonic() - t0
    assert elapsed < 2.5, f"sweep looks serial: {elapsed:.1f}s"


def test_sweep_jobs_default_config(memd):
    assert memd.DEFAULT_CONFIG["sweep_jobs"] == 4


def test_sweep_jobs_flag_cli(memd, env, memd_config):
    memd_config(git_commit=False, auto_scaffold=False)
    r = subprocess.run([sys.executable, MEMD_PATH, "sweep", "--jobs", "2"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]
    assert "[1/1] syncing" in r.stderr  # the (idle) global root


# --------------------------------------------------------------------------
# log rotation: memd.log -> .1 -> .2 -> .3, oldest dropped
# --------------------------------------------------------------------------


def test_log_rotation_keeps_three_backups(memd, env):
    os.makedirs(memd.STATE_DIR, exist_ok=True)
    for n in range(1, 5):
        with open(memd.LOG_PATH, "w") as f:
            f.write(f"round {n}\n" + "x" * 1_100_000)
        memd.log(f"rotate {n}")
    assert os.path.exists(memd.LOG_PATH)
    for i in (1, 2, 3):
        assert os.path.exists(f"{memd.LOG_PATH}.{i}"), f"missing backup .{i}"
    assert not os.path.exists(f"{memd.LOG_PATH}.4")
    with open(f"{memd.LOG_PATH}.1") as f:
        assert f.read().startswith("round 4")  # newest backup
    with open(f"{memd.LOG_PATH}.3") as f:
        assert f.read().startswith("round 2")  # oldest kept; round 1 dropped


def test_log_below_threshold_not_rotated(memd, env):
    memd.log("small entry")
    memd.log("another")
    assert not os.path.exists(f"{memd.LOG_PATH}.1")
