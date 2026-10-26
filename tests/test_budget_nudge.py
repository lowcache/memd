"""Over-budget files are nudged, never auto-archived: the curator prompt gets an
OVER BUDGET banner and the oldest dated sections shown in full as archive
candidates (even in a partial view); memd status flags the file."""

import os

UNDATED = "## Undated principle\n\nAlways pin inputs.\n\n"
DECISIONS = "# Architecture Decisions\n\n" + UNDATED + "".join(
    f"## Decision {i} (2026-0{1 + i // 10}-{10 + i % 10})\n\n"
    f"Body {i} q{i}q: " + "lorem ipsum dolor sit amet " * 16 + "\n\n"
    for i in range(30))
STATE = "# System State\n\n## Services\n\n- api: localhost:8080\n"
TODO = "# Open Tasks\n\n## Release\n\n- draft notes\n"


def _memory():
    return {"state.md": STATE, "decisions.md": DECISIONS, "todo.md": TODO,
            "mistakes.md": ""}


def _cfg(memd, **kw):
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg.update(kw)
    return cfg


def test_fixture_is_over_budget(memd):
    assert len(DECISIONS) > memd.DEFAULT_CONFIG["budgets"]["decisions.md"]


def test_over_budget_reports_only_view_files_over_budget(memd):
    out = memd.curator.over_budget(_cfg(memd), _memory())
    assert out == {"decisions.md": (len(DECISIONS), 12000)}


def test_archive_candidates_oldest_dated_first(memd):
    heads = memd.curator.archive_candidates(DECISIONS, k=3)
    assert heads == ["## Decision 0 (2026-01-10)", "## Decision 1 (2026-01-11)",
                     "## Decision 2 (2026-01-12)"]
    assert "## Undated principle" not in memd.curator.archive_candidates(
        DECISIONS, k=50)


def test_full_view_prompt_has_banner_and_candidates(memd):
    cfg = _cfg(memd, curator_view_chars=10 ** 9)
    p = memd.build_prompt(cfg, "/p", "p", _memory(), "U: hi", [])
    assert "===== OVER BUDGET =====" in p
    assert f"decisions.md: {len(DECISIONS):,} chars, budget 12,000" in p
    assert '"## Decision 0 (2026-01-10)"' in p
    banner = p[p.index("===== OVER BUDGET ====="):]
    assert banner.index("OVER BUDGET") < banner.index("===== CURRENT state.md")


def test_no_banner_when_within_budget(memd):
    mem = _memory()
    mem["decisions.md"] = "# D\n\n## One (2026-01-01)\n\nsmall.\n"
    p = memd.build_prompt(_cfg(memd), "/p", "p", mem, "U: hi", [])
    assert "OVER BUDGET" not in p


def test_partial_view_always_shows_candidates(memd):
    cfg = _cfg(memd, curator_view_chars=1500)
    mem = _memory()
    view = memd.curator.select_view(cfg, mem, "U: the api port moved", [])
    for h in ("## Decision 0 (2026-01-10)", "## Decision 1 (2026-01-11)",
              "## Decision 2 (2026-01-12)"):
        assert h in view["decisions.md"]
    p = memd.build_prompt(cfg, "/p", "p", mem, "U: the api port moved", [],
                          view=view)
    assert "q0q" in p and "q2q" in p      # candidate bodies shown
    assert "q20q" not in p                # unrelated, newer body hidden
    assert "===== OVER BUDGET =====" in p


def test_charter_rules(memd):
    assert "OVER BUDGET" in memd.CHARTER
    assert "Never archive something only because it is old" in memd.CHARTER


def test_status_flags_over_budget(memd, env, memd_config, capsys):
    memd_config(auto_scaffold=False)
    root = str(env.tmp / "proj")
    memd.scaffold(root, "proj")
    memd.write_memory_file(os.path.join(root, ".memory", "decisions.md"),
                           DECISIONS, "proj", "decisions")
    cfg = memd.load_config()
    memd.register(cfg, root)
    memd.cmd_status(memd.load_config())
    out = capsys.readouterr().out
    assert (f"  ! over budget: decisions.md {len(DECISIONS) / 1000:.1f}k/12.0k"
            in out)


def test_status_quiet_within_budget(memd, env, memd_config, capsys):
    memd_config(auto_scaffold=False)
    root = str(env.tmp / "proj")
    memd.scaffold(root, "proj")
    cfg = memd.load_config()
    memd.register(cfg, root)
    memd.cmd_status(memd.load_config())
    assert "over budget" not in capsys.readouterr().out
