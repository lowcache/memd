"""Shared fixtures for the memd test suite.

The memd package bakes XDG-derived paths (CONFIG_PATH, STATE_DIR,
CURSORS_PATH, ...) into module-level constants at import time. Every test
therefore gets a fresh package import through the `memd` fixture — sys.modules
is purged first — AFTER `env` has pointed HOME / XDG_CONFIG_HOME /
XDG_STATE_HOME at per-test tmp dirs. CLI-level tests reuse the same (already
patched) os.environ via subprocess inheritance of the memd.py shim.

No test may touch the real ~/.config/memd, ~/.local/state/memd, ~/.claude,
~/.memory, or invoke a real `claude` binary: curator calls always go through
the `curator_cmd` config key pointing at a fake shell script.
"""

import importlib
import json
import os
import shlex
import subprocess
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMD_PATH = os.path.join(REPO_ROOT, "memd.py")


# --------------------------------------------------------------------------
# isolation
# --------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Per-test isolated HOME + XDG dirs, exported into os.environ so both the
    in-process module load and any subprocess CLI invocation are isolated."""
    home = tmp_path / "home"
    xdg_config = tmp_path / "xdg-config"
    xdg_state = tmp_path / "xdg-state"
    for d in (home, xdg_config, xdg_state):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg_state))
    # keep git away from any system-wide config (gpgsign hooks etc.)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    return types.SimpleNamespace(
        home=home, xdg_config=xdg_config, xdg_state=xdg_state, tmp=tmp_path
    )


@pytest.fixture
def memd(env):
    """Fresh memd package import with the isolated env baked into its
    module-level path constants. The package (memd/) shadows the memd.py shim
    on sys.path, so `import memd` resolves to the package."""
    for name in [n for n in sys.modules
                 if n == "memd" or n.startswith("memd.")]:
        del sys.modules[name]
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    return importlib.import_module("memd")


# --------------------------------------------------------------------------
# building blocks
# --------------------------------------------------------------------------


def init_git_repo(path):
    for argv in (
        ["init", "-q"],
        ["config", "user.email", "test@test"],
        ["config", "user.name", "test"],
    ):
        subprocess.run(["git", "-C", str(path)] + argv, check=True,
                       capture_output=True)


@pytest.fixture
def git_project(env):
    """A scratch git repo posing as a project root."""
    proj = env.tmp / "proj"
    proj.mkdir()
    init_git_repo(proj)
    return str(proj)


# Canned curator response used by the end-to-end sync tests.
CANNED_RESPONSE = {
    "summary": "test distill",
    "state_body": "## System State\n\ne2e marker state fact.",
    "decisions_body": None,
    "todo_body": None,
    "mistakes_new_entries": [
        "### 2026-07-04 — e2e test entry\nsymptom/cause/prevention."
    ],
    "archive_entries": [],
}


@pytest.fixture
def fake_curator(env):
    """Factory: build a fake curator script that swallows stdin and emits a
    canned stdout (arbitrary bytes, any exit code). Returns the script path."""
    counter = {"n": 0}

    def _make(stdout_text, exit_code=0):
        counter["n"] += 1
        d = env.tmp / "curators"
        d.mkdir(exist_ok=True)
        payload = d / f"payload-{counter['n']}.out"
        payload.write_text(stdout_text)
        script = d / f"fake-curator-{counter['n']}.sh"
        script.write_text(
            "#!/bin/sh\n"
            "cat > /dev/null\n"
            f"cat {shlex.quote(str(payload))}\n"
            f"exit {exit_code}\n"
        )
        script.chmod(0o755)
        return str(script)

    return _make


@pytest.fixture
def memd_config(env):
    """Factory: write the isolated memd config.json. Defaults keep memd away
    from anything real: fake global root, no auto-scaffold."""

    def _write(**overrides):
        cfg = {
            "global_root": str(env.tmp / "ghome"),
            "auto_scaffold": False,
        }
        cfg.update(overrides)
        cfgdir = env.xdg_config / "memd"
        cfgdir.mkdir(parents=True, exist_ok=True)
        (cfgdir / "config.json").write_text(json.dumps(cfg))
        return cfg

    return _write


# --------------------------------------------------------------------------
# end-to-end sync harness
# --------------------------------------------------------------------------


class E2E:
    def __init__(self, env, project, transcript):
        self.env = env
        self.project = project
        self.transcript = transcript

    def sync(self):
        return subprocess.run(
            [sys.executable, MEMD_PATH, "sync",
             "--project", self.project, "--transcript", self.transcript],
            capture_output=True, text=True, timeout=60,
        )

    def append_user_line(self, text):
        with open(self.transcript, "a") as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": text},
                "cwd": self.project,
            }) + "\n")

    def cursors(self):
        p = self.env.xdg_state / "memd" / "cursors.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def log_text(self):
        p = self.env.xdg_state / "memd" / "memd.log"
        return p.read_text() if p.exists() else ""

    def read_mem(self, fname):
        p = os.path.join(self.project, ".memory", fname)
        with open(p) as f:
            return f.read()

    def git_log(self):
        return subprocess.run(
            ["git", "-C", self.project, "log", "--oneline"],
            capture_output=True, text=True,
        ).stdout


@pytest.fixture
def e2e(env, git_project, fake_curator, memd_config):
    """Project repo + fake curator + config + seeded transcript, ready for
    `memd sync` via subprocess."""
    curator = fake_curator(json.dumps(CANNED_RESPONSE))
    memd_config(curator_cmd=[curator, "{model}"])
    transcript = env.tmp / "session.jsonl"
    with open(transcript, "w") as f:
        f.write(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "hello e2e"},
            "cwd": git_project,
        }) + "\n")
        f.write(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "did the thing"}]},
        }) + "\n")
    return E2E(env, git_project, str(transcript))
