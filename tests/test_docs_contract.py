"""The three 1.0 contract documents stay in step with the code: every config key,
CLI command, flag, exit code and curator op is documented, and nothing that
doesn't exist is."""

import json
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(REPO, "docs")
HOME_DERIVED = {"antigravity_dir", "global_root"}

# command -> every exit code it can return (argparse usage errors, 2, and
# unhandled exceptions, 1, apply to all and are documented once, separately).
EXIT_CODES = {
    "init": {0, 2}, "sync": {0, 3}, "sweep": {0, 1}, "status": {0},
    "install-hooks": {0}, "brief": {0}, "recall": {0, 2}, "hook": {0, 1},
    "note": {0, 2}, "exclude": {0},
}


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def _rows(text):
    """{first-column backticked name: whole row} for every markdown table row."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if m:
            out[m.group(1)] = line
    return out


# --- configuration.md -------------------------------------------------------

def test_every_config_key_has_a_row_with_its_default(memd):
    rows = _rows(_read("docs", "configuration.md"))
    for key, default in memd.DEFAULT_CONFIG.items():
        assert key in rows, f"config key {key} undocumented"
        if key in HOME_DERIVED or key == "budgets":
            continue
        assert f"`{json.dumps(default)}`" in rows[key], (
            f"{key}: default `{json.dumps(default)}` missing from its row")


def test_budget_defaults_documented(memd):
    rows = _rows(_read("docs", "configuration.md"))
    for fname, chars in memd.DEFAULT_CONFIG["budgets"].items():
        assert fname in rows, f"budget {fname} undocumented"
        assert f"`{chars}`" in rows[fname]


def test_no_invented_config_keys(memd):
    known = set(memd.DEFAULT_CONFIG) | set(memd.DEFAULT_CONFIG["budgets"])
    extra = set(_rows(_read("docs", "configuration.md"))) - known
    assert not extra, f"documented keys that do not exist: {sorted(extra)}"


# --- cli.md -----------------------------------------------------------------

def _cli_source():
    return _read("memd", "cli.py")


def test_every_command_has_a_section():
    doc = _read("docs", "cli.md")
    cmds = re.findall(r'add_parser\(\s*"([\w-]+)"', _cli_source())
    assert set(cmds) == set(EXIT_CODES), "EXIT_CODES out of date with cli.py"
    for cmd in cmds:
        assert re.search(rf"(?m)^## `memd {re.escape(cmd)}", doc), cmd


def test_every_flag_documented_and_none_invented():
    doc = _read("docs", "cli.md")
    real = set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', _cli_source()))
    real |= set(re.findall(r'add_argument\(\s*"-\w",\s*"(--[\w-]+)"', _cli_source()))
    for flag in real:
        assert f"`{flag}" in doc, f"flag {flag} undocumented"
    shown = set(re.findall(r"`[^`]*?(--[a-z][\w-]*)", doc))
    invented = shown - real - {"--help", "--version"}
    assert not invented, f"documented flags that do not exist: {sorted(invented)}"


def test_exit_code_table():
    rows = _rows(_read("docs", "cli.md"))
    for cmd, codes in EXIT_CODES.items():
        row = rows.get(f"memd {cmd}")
        assert row, f"no exit-code row for memd {cmd}"
        cells = set(int(c) for c in re.findall(r"`(\d)`", row))
        assert cells == codes, f"memd {cmd}: documented {cells}, actual {codes}"


# --- curator-protocol.md ----------------------------------------------------

def test_protocol_names_every_op_file_and_key(memd):
    doc = _read("docs", "curator-protocol.md")
    for name in memd.curator.OP_NAMES:
        assert f"`{name}`" in doc, name
    for f in memd.curator.VIEW_FILES:
        assert f"`{f}`" in doc, f
    for key in ("summary", "ops", "mistakes_new_entries", "archive_entries",
                "op", "file", "heading", "body"):
        assert f"`{key}`" in doc, key


@pytest.mark.parametrize("fact", [
    "removed in 0.6.0", "`{model}`", "600 seconds", "40%", "800 characters",
    "2,500 characters", "14 days", "PARTIAL VIEW", "OVER BUDGET", "append-only",
])
def test_protocol_states_the_rules(fact):
    assert fact in _read("docs", "curator-protocol.md"), fact


def test_protocol_constants_match_code(memd):
    c = memd.curator
    assert (c.SHRINK_FLOOR, c.SHRINK_GUARD_MIN) == (0.4, 800)
    assert (c.OVERSIZED_SECTION_CHARS, c.VIEW_RECENT_DAYS) == (2500, 14)
    assert c.CURATOR_RETRY_DELAYS == (5, 15)


# --- single source of truth -------------------------------------------------

def test_readme_links_docs_instead_of_duplicating():
    readme = _read("README.md")
    for doc in ("docs/configuration.md", "docs/cli.md", "docs/curator-protocol.md"):
        assert f"({doc}" in readme, f"README does not link {doc}"
    assert "| `claude_bin` |" not in readme, "README still carries the config table"
    assert "| `memd sync`" not in readme, "README still carries the CLI table"


def test_contrib_readme_points_at_protocol():
    assert "../../docs/curator-protocol.md" in _read("contrib", "curators", "README.md")
