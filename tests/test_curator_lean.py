"""Lean curator invocation and per-distill usage telemetry."""

import json
import os
import shlex

import pytest


CURATOR_OBJ = {"summary": "lean test", "ops": [],
               "mistakes_new_entries": [], "archive_entries": []}
TOKEN_USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 100,
               "cache_read_input_tokens": 50, "output_tokens": 7}
EXPECTED_USAGE = {"input_tokens": 152, "cache_read_tokens": 50,
                  "output_tokens": 7, "cost_usd": 0.01}


def _envelope(result=None):
    return json.dumps({"result": json.dumps(CURATOR_OBJ) if result is None else result,
                       "usage": TOKEN_USAGE, "total_cost_usd": 0.01})


@pytest.fixture
def fake_claude(memd, env):
    def _make(fallback=False, retry=False):
        args = env.tmp / "args"
        stdin = env.tmp / "stdin"
        first = env.tmp / "first-args"
        marker = env.tmp / "called"
        payload = env.tmp / "envelope"
        bad_payload = env.tmp / "bad-envelope"
        payload.write_text(_envelope())
        bad_payload.write_text(_envelope("no JSON here"))
        script = env.tmp / "fake-claude.sh"
        q = lambda path: shlex.quote(str(path))
        body = ("#!/bin/sh\n"
                f"printf '%s\\0' \"$@\" > {q(args)}\n"
                f"cat > {q(stdin)}\n")
        if fallback:
            body += ("for arg in \"$@\"; do\n"
                     "  if [ \"$arg\" = '--strict-mcp-config' ]; then\n"
                     f"    cp {q(args)} {q(first)}\n"
                     "    echo \"error: unknown option '--strict-mcp-config'\" >&2\n"
                     "    exit 1\n"
                     "  fi\n"
                     "done\n")
        if retry:
            body += (f"if [ ! -e {q(marker)} ]; then\n"
                     f"  touch {q(marker)}\n"
                     f"  cat {q(bad_payload)}\n"
                     "  exit 0\n"
                     "fi\n")
        body += f"cat {q(payload)}\n"
        script.write_text(body)
        script.chmod(0o755)
        cfg = dict(memd.DEFAULT_CONFIG)
        cfg["curator_cmd"] = []
        cfg["claude_bin"] = str(script)
        return cfg, args, stdin, first
    return _make


def _args(path):
    return path.read_bytes().decode().split("\0")[:-1]


def test_default_backend_uses_lean_flags(memd, fake_claude):
    cfg, args, stdin, _ = fake_claude()
    assert memd.call_curator(cfg, "project prompt", "haiku") == CURATOR_OBJ
    argv = _args(args)
    assert argv[argv.index("--system-prompt") + 1] == memd.curator.CHARTER
    flags = memd.curator.LEAN_CLAUDE_FLAGS
    start = argv.index("--tools")
    assert argv[start:start + len(flags)] == flags
    assert memd.curator.CHARTER.splitlines()[0] not in stdin.read_text()
    assert stdin.read_text() == "project prompt"


def test_usage_parsed(memd, fake_claude):
    cfg, _, _, _ = fake_claude()
    usage = {}
    memd.call_curator(cfg, "prompt", "haiku", usage=usage)
    assert usage == EXPECTED_USAGE


def test_custom_curator_stdin_starts_with_charter(memd, env):
    stdin = env.tmp / "custom-stdin"
    script = env.tmp / "custom-curator.sh"
    script.write_text("#!/bin/sh\n"
                      f"cat > {shlex.quote(str(stdin))}\n"
                      f"printf '%s\\n' {shlex.quote(json.dumps(CURATOR_OBJ))}\n")
    script.chmod(0o755)
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg["curator_cmd"] = [str(script)]
    assert memd.call_curator(cfg, "prompt", "haiku") == CURATOR_OBJ
    assert stdin.read_text() == memd.curator.CHARTER + "\n\nprompt"


def test_old_cli_fallback(memd, fake_claude):
    cfg, args, stdin, first = fake_claude(fallback=True)
    assert memd.call_curator(cfg, "prompt", "haiku") == CURATOR_OBJ
    assert "--strict-mcp-config" in _args(first)
    assert _args(args) == ["-p", "--model", "haiku", "--output-format", "json",
                           "--max-turns", "1"]
    assert "--system-prompt" not in _args(args)
    assert stdin.read_text() == memd.curator.CHARTER + "\n\nprompt"
    with open(memd.LOG_PATH) as f:
        assert "rejected lean curator flags" in f.read()


def test_usage_accumulates_across_retry(memd, fake_claude, monkeypatch):
    monkeypatch.setattr(memd.curator, "CURATOR_RETRY_DELAYS", (0, 0))
    cfg, _, _, _ = fake_claude(retry=True)
    usage = {}
    assert memd.call_curator(cfg, "prompt", "haiku", usage=usage) == CURATOR_OBJ
    assert usage == {"input_tokens": 304, "cache_read_tokens": 100,
                     "output_tokens": 14, "cost_usd": 0.02}


def test_build_prompt_excludes_charter(memd):
    memory = {name: "" for name in memd.MEMORY_FILES}
    prompt = memd.build_prompt(memd.DEFAULT_CONFIG, "/project", "project",
                               memory, "digest", [])
    assert prompt.startswith("PROJECT: project  (root: /project)")
    assert memd.curator.CHARTER.splitlines()[0] not in prompt


def _project(memd, env, name, turns):
    root = str(env.tmp / name)
    memd.scaffold(root, name)
    src = os.path.join(root, "session.jsonl")
    with open(src, "w") as f:
        for i in range(turns):
            f.write(json.dumps({"type": "user",
                                "message": {"content": f"turn {i} " + "z" * 200}})
                    + "\n")
    cfg = memd.load_config()
    cfg["projects"][root] = {"name": name, "extra_sources": [src]}
    memd.save_config(cfg)
    return root


@pytest.mark.parametrize("enveloped", [True, False])
def test_journal_usage(memd, env, memd_config, fake_curator, enveloped):
    payload = _envelope() if enveloped else json.dumps(CURATOR_OBJ)
    memd_config(curator_cmd=[fake_curator(payload), "{model}"],
                git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "journal", turns=2)
    assert memd.sync_project(memd.load_config(), root, trigger="manual") is True
    entry = memd.load_json(memd.META_PATH, {})[root]["history"][-1]
    if enveloped:
        assert entry["usage"] == EXPECTED_USAGE
    else:
        assert "usage" not in entry


def test_status_prints_curator_cost(memd, env, memd_config, capsys):
    memd_config(git_commit=False, auto_scaffold=False)
    root = _project(memd, env, "status", turns=0)
    memd.save_json(memd.META_PATH, {root: {"history": [
        {"usage": EXPECTED_USAGE}, {}, {"usage": EXPECTED_USAGE}]}})
    memd.cmd_status(memd.load_config())
    assert ("curator cost : $0.02, 304 input tokens over 2 logged distill(s)"
            in capsys.readouterr().out)
