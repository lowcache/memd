"""Budgeted, section-aware session-start brief: section-boundary truncation,
recency ordering, --topic filtering, and superset compatibility with the
pre-budget brief."""

import datetime
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMD_PATH = os.path.join(REPO_ROOT, "memd.py")


def days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def write_mem(memd, proj, fname, body):
    memd.write_memory_file(os.path.join(proj, ".memory", fname), body,
                           "proj", fname.split(".")[0])


@pytest.fixture
def project(env, memd):
    proj = str(env.tmp / "proj")
    memd.scaffold(proj, "proj")
    return proj


@pytest.fixture
def cfg(memd, env):
    """Default config with a dead global root so the global slice stays out
    of the way unless a test builds one."""
    c = dict(memd.DEFAULT_CONFIG)
    c["global_root"] = str(env.tmp / "nowhere")
    return c


# --------------------------------------------------------------------------
# section splitting
# --------------------------------------------------------------------------


def test_split_sections_h2_h3_only(memd):
    body = ("# File Title\n\npreamble.\n\n## Alpha\n\nalpha body.\n\n"
            "### Beta\n\nbeta body.\n\n#### not-a-boundary\ndeep.\n")
    got = memd.split_sections(body)
    assert [h for h, _ in got] == ["## Alpha", "### Beta"]
    # h1 title and preamble dropped; h4 stays inside the h3 section
    assert "not-a-boundary" in got[1][1]


# --------------------------------------------------------------------------
# budget truncation
# --------------------------------------------------------------------------


def test_budget_truncates_at_section_boundary(memd, project, cfg):
    write_mem(memd, project, "state.md",
              "# System State\n\n## Alpha\n" + "a" * 200
              + "\n\n## Beta\n" + "b" * 200
              + "\n\n## Gamma\n" + "c" * 200)
    cfg["brief_chars"] = 250
    brief = memd.make_brief(cfg, project)
    # first section fully present, later ones dropped whole (no partial body)
    assert "## Alpha\n" + "a" * 200 in brief
    assert "## Beta" not in brief and "b" * 5 not in brief
    assert "## Gamma" not in brief and "c" * 5 not in brief
    assert "2 more sections omitted — use --max-chars" in brief


def test_default_budget_keeps_everything_small(memd, project, cfg):
    write_mem(memd, project, "state.md",
              "# System State\n\n## Alpha\nshort.\n\n## Beta\nalso short.")
    brief = memd.make_brief(cfg, project)
    assert "## Alpha" in brief and "## Beta" in brief
    assert "omitted" not in brief


# --------------------------------------------------------------------------
# priority & recency ordering
# --------------------------------------------------------------------------


def test_todos_come_before_other_sections(memd, project, cfg):
    write_mem(memd, project, "todo.md", "# Open Tasks\n\n- [ ] first task")
    write_mem(memd, project, "state.md", "# System State\n\n## Zone\nfact.")
    brief = memd.make_brief(cfg, project)
    assert brief.index("Open todo items:") < brief.index("## Zone")


def test_decisions_recency_ordering(memd, project, cfg):
    write_mem(memd, project, "decisions.md",
              "# Architecture Decisions\n\n"
              f"## Old choice\n\nDecided: {days_ago(10)}\n\nolder body.\n\n"
              f"## New choice\n\nDecided: {days_ago(1)}\n\nnewer body.")
    brief = memd.make_brief(cfg, project)
    assert brief.index("## New choice") < brief.index("## Old choice")


def test_decisions_outside_window_excluded(memd, project, cfg):
    write_mem(memd, project, "decisions.md",
              "# Architecture Decisions\n\n"
              f"## Ancient choice\n\nDecided: {days_ago(60)}\n\nstale.\n\n"
              f"## Fresh choice\n\nDecided: {days_ago(2)}\n\ncurrent.")
    brief = memd.make_brief(cfg, project)
    assert "## Fresh choice" in brief
    assert "## Ancient choice" not in brief


def test_undated_decision_survives_without_crash(memd, project, cfg):
    write_mem(memd, project, "decisions.md",
              "# Architecture Decisions\n\n## Undated choice\n\nno timestamp.")
    brief = memd.make_brief(cfg, project)
    assert "## Undated choice" in brief


def test_mistakes_summary_not_full_body(memd, project, cfg):
    write_mem(memd, project, "mistakes.md",
              "# Mistake Audit Log (append-only)\n\n"
              f"### {days_ago(20)} — old slip\n\nold details.\n\n"
              f"### {days_ago(3)} — new slip\n\nnew details.")
    brief = memd.make_brief(cfg, project)
    assert "Mistakes log: 2 entries; most recent:" in brief
    assert "new slip" in brief and "new details." in brief
    assert "old details." not in brief


# --------------------------------------------------------------------------
# --topic filtering
# --------------------------------------------------------------------------


def test_topic_filters_sections(memd, project, cfg):
    write_mem(memd, project, "state.md",
              "# System State\n\n## Quantum work\nentangled fact.\n\n"
              "## Plumbing\nboring fact.")
    brief = memd.make_brief(cfg, project, topic="QUANTUM")  # case-insensitive
    assert "## Quantum work" in brief
    assert "## Plumbing" not in brief and "boring fact" not in brief


def test_topic_no_match_returns_brief_note_only(memd, project, cfg):
    write_mem(memd, project, "state.md", "# System State\n\n## Zone\nfact.")
    brief = memd.make_brief(cfg, project, topic="nonexistentkeyword")
    assert brief == memd.BRIEF_NOTE


# --------------------------------------------------------------------------
# superset compatibility with the pre-budget brief
# --------------------------------------------------------------------------


def test_superset_of_pre_budget_brief(memd, env, project, cfg):
    write_mem(memd, project, "state.md", "# System State\n\n## Zone\nfact.")
    write_mem(memd, project, "todo.md",
              "# Open Tasks\n\n- [ ] task one\n- [ ] task two")
    with open(os.path.join(project, ".memory", "inbox", "note.md"), "w") as f:
        f.write("a note\n")
    memd.save_json(memd.META_PATH, {os.path.realpath(project): {
        "last_sync": "2026-07-10 12:00", "trigger": "manual",
        "summary": "did things"}})
    ghome = str(env.tmp / "ghome")
    memd.scaffold(ghome, "global", model_stub=False)
    write_mem(memd, ghome, "todo.md", "# Open Tasks\n\n- [ ] global task")
    cfg["global_root"] = ghome
    gmem = os.path.join(ghome, ".memory")
    _, gstate = memd.split_frontmatter(open(os.path.join(gmem, "state.md")).read())

    brief = memd.make_brief(cfg, project)
    # every part the pre-budget brief emitted, verbatim
    for part in [
        memd.BRIEF_NOTE,
        "Last memory distill: 2026-07-10 12:00 (manual) — did things",
        f"Project: proj | state.md updated {memd.today()} | status active",
        "Open todo items:\n- task one\n- task two",
        "1 unprocessed curator inbox note(s).",
        f"Global memory (cross-project) lives at {gmem} — read its state.md / "
        "decisions.md there when a task touches user, system, or cross-project "
        "facts; leave global notes in its inbox/.",
        "Global state.md (excerpt):\n" + gstate.strip(),
        "Global open todo items:\n- global task",
    ]:
        assert part in brief, part
    # and the new enrichment on top
    assert "## Zone\nfact." in brief


# --------------------------------------------------------------------------
# CLI flags
# --------------------------------------------------------------------------


def run_brief(*args):
    return subprocess.run(
        [sys.executable, MEMD_PATH, "brief"] + list(args),
        capture_output=True, text=True, timeout=60,
    )


def test_cli_max_chars_hard_cap(memd, project, memd_config, env):
    write_mem(memd, project, "state.md",
              "# System State\n\n## Alpha\n" + "a" * 400
              + "\n\n## Beta\n" + "b" * 400)
    memd_config(global_root=str(env.tmp / "nowhere"))
    r = run_brief(project, "--max-chars", "500")
    assert r.returncode == 0
    assert len(r.stdout.rstrip("\n")) <= 500
    assert memd.BRIEF_NOTE in r.stdout


def test_cli_topic_no_match_is_brief_note(memd, project, memd_config, env):
    write_mem(memd, project, "state.md", "# System State\n\n## Zone\nfact.")
    memd_config(global_root=str(env.tmp / "nowhere"))
    r = run_brief(project, "--topic", "nonexistentkeyword")
    assert r.returncode == 0
    assert r.stdout.strip() == memd.BRIEF_NOTE
