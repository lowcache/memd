"""eval/run.py: scenario replay through the real sync pipeline, probe scoring,
usage aggregation, exit contract. Always driven by a fake curator here."""

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RUNNER = os.path.join(REPO_ROOT, "eval", "run.py")

CURATOR_OUT = {
    "summary": "fake eval distill",
    "ops": [{"op": "append_section", "file": "state.md",
             "heading": "## Ports", "body": "- api: 9090"}],
    "mistakes_new_entries": [],
    "archive_entries": [],
}

SEED_DECISIONS = ("# Architecture Decisions\n\n## Keep A (2026-01-01)\n\nalpha.\n\n"
                  "## Keep B (2026-01-02)\n\nbeta.\n")


def _fake_curator(env):
    d = env.tmp / "curators"
    d.mkdir(exist_ok=True)
    payload = d / "envelope.json"
    payload.write_text(json.dumps({
        "result": json.dumps(CURATOR_OUT),
        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "output_tokens": 5},
        "total_cost_usd": 0.5,
    }))
    script = d / "fake.sh"
    script.write_text(f"#!/bin/sh\ncat > /dev/null\ncat {payload}\n")
    script.chmod(0o755)
    return str(script)


def _scenarios(env):
    d = env.tmp / "scenarios"
    d.mkdir()
    (d / "alpha.json").write_text(json.dumps({
        "name": "alpha",
        "description": "passes",
        "seed": {"decisions.md": SEED_DECISIONS},
        "sessions": [{"date": "2026-09-01T10:00:00Z", "turns": [
            {"role": "user", "text": "move the api to 9090"},
            {"role": "assistant", "text": "done, api on 9090"}]}],
        "probes": [
            {"file": "state.md", "must": "9090"},
            {"file": "state.md", "must_not": "8080"},
            {"file": "decisions.md", "unchanged": True},
            {"file": "decisions.md", "preserve_headings": True},
            {"file": "*", "must": "(?i)ports"},
        ],
    }))
    (d / "beta.json").write_text(json.dumps({
        "name": "beta",
        "description": "fails one probe",
        "sessions": [
            {"date": "2026-09-02T10:00:00Z", "turns": [
                {"role": "user", "text": "first session"}]},
            {"date": "2026-09-03T10:00:00Z", "turns": [
                {"role": "user", "text": "second session"}]},
        ],
        "probes": [{"file": "todo.md", "must": "nonexistent-xyz"}],
    }))
    return str(d)


def _run(env, *extra):
    out = env.tmp / "results.json"
    r = subprocess.run(
        [sys.executable, RUNNER, "--scenarios-dir", _scenarios_dir(env),
         "--curator-cmd", json.dumps([_fake_curator(env), "{model}"]),
         "--out", str(out), *extra],
        capture_output=True, text=True, timeout=120)
    data = json.loads(out.read_text()) if out.exists() else None
    return r, data


def _scenarios_dir(env):
    d = env.tmp / "scenarios"
    return str(d) if d.exists() else _scenarios(env)


def test_runner_scores_and_fails_on_any_failed_probe(env):
    r, data = _run(env)
    assert r.returncode == 1, r.stderr[-2000:]
    by = {s["name"]: s for s in data["scenarios"]}
    assert set(by) == {"alpha", "beta"}
    assert by["alpha"]["passed"] is True
    assert [p["ok"] for p in by["alpha"]["probes"]] == [True] * 5
    assert by["beta"]["passed"] is False
    assert by["beta"]["probes"][0]["ok"] is False


def test_runner_aggregates_usage_and_distills(env):
    _, data = _run(env)
    by = {s["name"]: s for s in data["scenarios"]}
    assert by["alpha"]["distills"] == 1
    assert by["beta"]["distills"] == 2
    assert by["alpha"]["usage"]["input_tokens"] == 10
    assert by["alpha"]["usage"]["output_tokens"] == 5
    assert abs(by["beta"]["usage"]["cost_usd"] - 1.0) < 1e-9
    assert abs(data["totals"]["cost_usd"] - 1.5) < 1e-9
    assert data["totals"]["probes_passed"] == 5
    assert data["totals"]["probes_total"] == 6


def test_scenario_filter_and_exit_zero(env):
    r, data = _run(env, "--scenario", "alpha")
    assert r.returncode == 0, r.stderr[-2000:]
    assert [s["name"] for s in data["scenarios"]] == ["alpha"]
    assert "alpha" in r.stdout


def test_config_overrides_reach_the_pipeline(env):
    # min_digest_chars is forced to 0 by the runner; an override must still
    # apply on top of the runner's defaults
    r, data = _run(env, "--scenario", "alpha",
                   "--config", json.dumps({"curator_view_chars": 123}))
    assert r.returncode == 0, r.stderr[-2000:]
    assert data["config_overrides"] == {"curator_view_chars": 123}


def test_runner_never_touches_real_state(env):
    _run(env)
    # the runner builds its own throwaway XDG tree; the caller's stays empty
    assert not os.listdir(env.xdg_state)
    assert not os.listdir(env.xdg_config)


def test_shipped_scenarios_are_well_formed():
    d = os.path.join(REPO_ROOT, "eval", "scenarios")
    names = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    assert len(names) >= 5
    kinds = {"must", "must_not", "unchanged", "preserve_headings"}
    for n in names:
        s = json.load(open(os.path.join(d, n)))
        assert s["name"] + ".json" == n
        assert s["sessions"] and s["probes"]
        for p in s["probes"]:
            assert p["file"] in ("state.md", "decisions.md", "todo.md",
                                 "mistakes.md", "archive", "*")
            assert len(kinds & set(p)) == 1, p
