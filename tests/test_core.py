"""XDG isolation, redact(), frontmatter round-trip, find_project()."""

import os

import pytest

# --------------------------------------------------------------------------
# XDG isolation
# --------------------------------------------------------------------------


def test_xdg_isolation(env, memd):
    assert memd.CONFIG_PATH == str(env.xdg_config / "memd" / "config.json")
    assert memd.STATE_DIR == str(env.xdg_state / "memd")
    assert memd.CURSORS_PATH == str(env.xdg_state / "memd" / "cursors.json")
    assert memd.LOG_PATH.startswith(str(env.xdg_state))
    # HOME is patched too, so claude-code paths can never hit the real home
    assert memd.HOME == str(env.home)
    assert memd.CLAUDE_PROJECTS_DIR.startswith(str(env.home))
    assert memd.CLAUDE_SETTINGS.startswith(str(env.home))


# --------------------------------------------------------------------------
# redact()
# --------------------------------------------------------------------------

SECRET_SAMPLES = {
    "ghp": "token ghp_" + "A1" * 15,
    "gho": "token gho_" + "A1" * 15,
    "github_pat": "github_pat_" + "a" * 30,
    "sk": "sk-ant-" + "x" * 30,
    "glpat": "glpat-" + "x" * 20,
    "npm": "npm_" + "a1" * 18,
    "aws": "AKIA" + "A" * 16,
    "slack": "xoxb-123456789012-abcdef",
    "jwt": "eyJ" + "a" * 25 + "." + "b" * 15 + "." + "c" * 15,
    "json_token": '"access_token": "supersecretvalue"',
    "ya29": "ya29." + "Zz" * 15,
}


@pytest.mark.parametrize("name", sorted(SECRET_SAMPLES))
def test_redact_positive(memd, name):
    sample = SECRET_SAMPLES[name]
    r = memd.redact(sample)
    assert "REDACTED" in r
    # no long token fragment survives
    assert not any(tok in r for tok in sample.split() if len(tok) > 12), r


def test_redact_pem_block(memd):
    pem = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nlines\n"
           "-----END RSA PRIVATE KEY-----")
    r = memd.redact(pem)
    assert "[REDACTED KEY]" in r
    assert "MIIEow" not in r


def test_redact_leaves_clean_text(memd):
    clean = ("normal prose, a sha c9291f4, path /home/x/.config, "
             "skate-board, ghost_writer")
    assert memd.redact(clean) == clean


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------


def test_split_render_roundtrip(memd):
    meta = {"type": "state", "project": "my proj", "last_updated": "2026-07-04",
            "status": "active", "extra": "1"}
    body = "# Heading\n\nline one.\nline two.\n"
    text = memd.render_frontmatter(meta) + body
    got_meta, got_body = memd.split_frontmatter(text)
    assert got_meta == meta
    assert got_body.strip() == body.strip()


def test_render_frontmatter_key_order(memd):
    meta = {"status": "active", "zzz": "9", "type": "todo",
            "last_updated": "2026-07-04", "project": "p"}
    fm = memd.render_frontmatter(meta)
    lines = fm.strip().splitlines()[1:-1]  # drop --- fences
    keys = [ln.split(":")[0] for ln in lines]
    assert keys == ["type", "project", "last_updated", "status", "zzz"]


def test_split_frontmatter_tolerates_plain_text(memd):
    text = "# No frontmatter here\n\njust a body.\n"
    meta, body = memd.split_frontmatter(text)
    assert meta == {}
    assert body == text


def test_write_memory_file_roundtrip(memd, tmp_path):
    p = tmp_path / "state.md"
    memd.write_memory_file(str(p), "# State\n\nfact one.", "projname", "state")
    text = p.read_text()
    assert text.startswith("---\n")
    meta, body = memd.split_frontmatter(text)
    assert meta == {
        "type": "state",
        "project": "projname",
        "status": "active",
        "last_updated": memd.today(),
    }
    assert body.strip() == "# State\n\nfact one."


def test_write_memory_file_preserves_existing_meta(memd, tmp_path):
    p = tmp_path / "state.md"
    memd.write_memory_file(str(p), "old body", "projname", "state")
    # rewrite with different name/type: existing meta wins (setdefault)
    memd.write_memory_file(str(p), "new body", "othername", "todo")
    meta, body = memd.split_frontmatter(p.read_text())
    assert meta["type"] == "state"
    assert meta["project"] == "projname"
    assert meta["last_updated"] == memd.today()
    assert body.strip() == "new body"


# --------------------------------------------------------------------------
# find_project()
# --------------------------------------------------------------------------


@pytest.fixture
def registry(memd, env):
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg["projects"] = {}
    proj = os.path.realpath(str(env.tmp / "projA"))
    nested = os.path.join(proj, "vendor")
    os.makedirs(os.path.join(nested, "deep"))
    gr = os.path.realpath(str(env.tmp / "ghome"))
    os.makedirs(gr)
    cfg["projects"][proj] = {"name": "a", "extra_sources": []}
    cfg["projects"][nested] = {"name": "a-vendor", "extra_sources": []}
    cfg["projects"][gr] = {"name": "global", "extra_sources": [], "global": True}
    return cfg, proj, nested, gr


def test_find_project_exact(memd, registry):
    cfg, proj, _, _ = registry
    assert memd.find_project(cfg, proj) == proj


def test_find_project_subdirectory(memd, registry):
    cfg, proj, _, _ = registry
    assert memd.find_project(cfg, os.path.join(proj, "src", "lib")) == proj


def test_find_project_longest_prefix_wins(memd, registry):
    cfg, _, nested, _ = registry
    assert memd.find_project(cfg, os.path.join(nested, "deep")) == nested


def test_find_project_unregistered_is_none(memd, registry, env):
    cfg, _, _, _ = registry
    assert memd.find_project(cfg, str(env.tmp / "elsewhere")) is None


def test_find_project_skips_global_root(memd, registry):
    cfg, _, _, gr = registry
    assert memd.find_project(cfg, gr) is None
    assert memd.find_project(cfg, os.path.join(gr, "sub")) is None
