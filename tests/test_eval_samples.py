"""eval/run.py --samples / --jobs: each scenario replays N times in separate
projects; a scenario passes only if every sample passes; per-probe pass counts,
summed usage, and the curator models are recorded."""

import json
import subprocess
import sys

from test_eval_harness import RUNNER, _fake_curator, _scenarios_dir


def _flaky_curator(env):
    """Passes alpha's probes on odd-numbered calls only."""
    d = env.tmp / "curators"
    d.mkdir(exist_ok=True)
    good = {"summary": "s", "ops": [{"op": "append_section", "file": "state.md",
                                     "heading": "## Ports", "body": "- api: 9090"}],
            "mistakes_new_entries": [], "archive_entries": []}
    bad = dict(good, ops=[])
    for name, obj in (("good.json", good), ("bad.json", bad)):
        (d / name).write_text(json.dumps({"result": json.dumps(obj)}))
    counter = d / "n"
    counter.write_text("0")
    script = d / "flaky.sh"
    script.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f"n=$(( $(cat {counter}) + 1 )); echo $n > {counter}\n"
        f"if [ $((n % 2)) = 1 ]; then cat {d / 'good.json'}; "
        f"else cat {d / 'bad.json'}; fi\n")
    script.chmod(0o755)
    return str(script)


def _run(env, curator, *extra):
    out = env.tmp / "results.json"
    r = subprocess.run(
        [sys.executable, RUNNER, "--scenarios-dir", _scenarios_dir(env),
         "--curator-cmd", json.dumps([curator, "{model}"]),
         "--out", str(out), *extra],
        capture_output=True, text=True, timeout=300)
    data = json.loads(out.read_text()) if out.exists() else None
    return r, data


def test_samples_count_and_sum(env):
    r, data = _run(env, _fake_curator(env), "--scenario", "alpha", "--samples", "3")
    assert r.returncode == 0, r.stderr[-2000:]
    (alpha,) = data["scenarios"]
    assert alpha["samples_total"] == 3 and alpha["passed_samples"] == 3
    assert alpha["passed"] is True
    assert len(alpha["samples"]) == 3
    assert all(s["passed"] for s in alpha["samples"])
    assert [p["passes"] for p in alpha["probes"]] == [3] * 5
    assert all(p["ok"] for p in alpha["probes"])
    assert alpha["distills"] == 3
    assert alpha["usage"]["input_tokens"] == 30
    assert abs(alpha["usage"]["cost_usd"] - 1.5) < 1e-9
    t = data["totals"]
    assert (t["samples_passed"], t["samples_total"]) == (3, 3)
    assert (t["probes_passed"], t["probes_total"]) == (15, 15)
    assert "3/3 samples" in r.stdout


def test_flaky_scenario_fails_with_counts(env):
    r, data = _run(env, _flaky_curator(env), "--scenario", "alpha", "--samples", "3")
    assert r.returncode == 1
    (alpha,) = data["scenarios"]
    assert alpha["passed_samples"] == 2 and alpha["passed"] is False
    by = {p.get("must") or p.get("must_not") or next(iter(p)): p
          for p in alpha["probes"]}
    assert by["9090"]["passes"] == 2 and by["9090"]["ok"] is False
    assert data["totals"]["scenarios_passed"] == 0
    assert "2/3 samples" in r.stdout


def test_jobs_give_the_same_result(env):
    r, data = _run(env, _fake_curator(env), "--samples", "2", "--jobs", "4")
    assert r.returncode == 1, r.stderr[-2000:]       # beta always fails
    by = {s["name"]: s for s in data["scenarios"]}
    assert [s["name"] for s in data["scenarios"]] == ["alpha", "beta"]
    assert by["alpha"]["passed_samples"] == 2
    assert by["beta"]["passed_samples"] == 0
    assert by["beta"]["distills"] == 4
    assert data["totals"]["samples_total"] == 4


def test_output_records_models(env):
    _, data = _run(env, _fake_curator(env), "--scenario", "alpha",
                   "--config", json.dumps({"model_small": "haiku"}))
    assert data["models"] == {"model_small": "haiku", "model_large": "sonnet"}


def test_default_is_one_sample(env):
    _, data = _run(env, _fake_curator(env), "--scenario", "alpha")
    (alpha,) = data["scenarios"]
    assert alpha["samples_total"] == 1 and len(alpha["samples"]) == 1


def test_scenario_config_and_inbox_reach_the_curator(env):
    d = env.tmp / "sc2"
    d.mkdir()
    (d / "gamma.json").write_text(json.dumps({
        "name": "gamma", "description": "inbox + per-scenario config",
        "config": {"curator_view_chars": 1},
        "seed": {"decisions.md": "# D\n\n## Keep A (2026-01-01)\n\nalpha.\n"},
        "sessions": [{"date": "2026-09-01T10:00:00Z",
                      "inbox": ["restore from b2 takes 14 minutes"]}],
        "probes": [{"file": "decisions.md", "preserve_headings": True}],
    }))
    cur = env.tmp / "curators"
    cur.mkdir(exist_ok=True)
    seen = cur / "prompt.txt"
    (cur / "out.json").write_text(json.dumps({"result": json.dumps(
        {"summary": "s", "ops": [], "mistakes_new_entries": [],
         "archive_entries": []})}))
    script = cur / "capture.sh"
    script.write_text(f"#!/bin/sh\ncat > {seen}\ncat {cur / 'out.json'}\n")
    script.chmod(0o755)
    out = env.tmp / "r.json"
    r = subprocess.run(
        [sys.executable, RUNNER, "--scenarios-dir", str(d),
         "--curator-cmd", json.dumps([str(script), "{model}"]), "--out", str(out)],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr[-2000:]
    prompt = seen.read_text()
    assert "restore from b2 takes 14 minutes" in prompt
    assert "(PARTIAL VIEW:" in prompt
