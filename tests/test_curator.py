"""call_curator(): output parsing variants and failure modes, all via fake
shell scripts wired through the curator_cmd config contract."""

import json

import pytest

CURATOR_OBJ = {
    "summary": "unit parse",
    "state_body": "## S\n\nnew fact.",
    "decisions_body": None,
    "todo_body": None,
    "mistakes_new_entries": [],
    "archive_entries": [],
}


@pytest.fixture
def cfg_for(memd, fake_curator):
    def _make(stdout_text, exit_code=0):
        cfg = dict(memd.DEFAULT_CONFIG)
        cfg["curator_cmd"] = [fake_curator(stdout_text, exit_code), "{model}"]
        return cfg
    return _make


def test_claude_envelope(memd, cfg_for):
    # claude -p --output-format json shape: {"result": "<text with JSON>"}
    stdout = json.dumps({"result": json.dumps(CURATOR_OBJ)})
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_bare_json_object(memd, cfg_for):
    # custom curator_cmd contract: bare curator JSON object on stdout
    stdout = json.dumps(CURATOR_OBJ)
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_json_in_fences(memd, cfg_for):
    stdout = "```json\n" + json.dumps(CURATOR_OBJ, indent=2) + "\n```\n"
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_json_embedded_in_prose(memd, cfg_for):
    stdout = ("Sure, here is the updated memory as requested.\n\n"
              + json.dumps(CURATOR_OBJ)
              + "\n\nLet me know if anything else is needed.\n")
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_envelope_with_fenced_result(memd, cfg_for):
    stdout = json.dumps(
        {"result": "```json\n" + json.dumps(CURATOR_OBJ) + "\n```"})
    assert memd.call_curator(cfg_for(stdout), "prompt", "haiku") == CURATOR_OBJ


def test_nonzero_exit_raises(memd, cfg_for, no_retry_delay):
    cfg = cfg_for(json.dumps(CURATOR_OBJ), exit_code=3)
    # CuratorError subclasses RuntimeError: old-style callers keep working
    with pytest.raises(RuntimeError, match="rc=3"):
        memd.call_curator(cfg, "prompt", "haiku")


def test_no_json_raises(memd, cfg_for, no_retry_delay):
    with pytest.raises(memd.CuratorError, match="no JSON"):
        memd.call_curator(cfg_for("nothing to see here, no braces at all"),
                          "prompt", "haiku")


def test_missing_binary_raises(memd):
    # no no_retry_delay fixture on purpose: a ConfigError must fail fast
    # without retry sleeps, or this test hangs for the full backoff
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg["curator_cmd"] = ["/nonexistent/curator-xyz", "{model}"]
    with pytest.raises(memd.ConfigError, match="curator backend not found"):
        memd.call_curator(cfg, "prompt", "haiku")


# --------------------------------------------------------------------------
# retry / backoff and the error hierarchy (Task D)
# --------------------------------------------------------------------------


@pytest.fixture
def no_retry_delay(memd, monkeypatch):
    monkeypatch.setattr(memd.curator, "CURATOR_RETRY_DELAYS", (0, 0))


def test_error_hierarchy(memd):
    assert issubclass(memd.MemdError, RuntimeError)
    for exc in (memd.ConfigError, memd.DigestError, memd.CuratorError):
        assert issubclass(exc, memd.MemdError)


def test_retry_delays_default(memd):
    assert memd.curator.CURATOR_RETRY_DELAYS == (5, 15)


def test_retry_recovers_from_transient_failure(memd, env, no_retry_delay):
    # backend fails on the first call, succeeds on the second
    d = env.tmp / "curators"
    d.mkdir(exist_ok=True)
    marker = d / "tried-once"
    script = d / "flaky.sh"
    script.write_text(
        "#!/bin/sh\ncat > /dev/null\n"
        f"if [ -e {marker} ]; then echo '{json.dumps(CURATOR_OBJ)}'\n"
        f"else touch {marker}; exit 1\nfi\n")
    script.chmod(0o755)
    cfg = dict(memd.DEFAULT_CONFIG)
    cfg["curator_cmd"] = [str(script), "{model}"]
    assert memd.call_curator(cfg, "prompt", "haiku") == CURATOR_OBJ
    with open(memd.LOG_PATH) as f:
        assert "failed, retrying in" in f.read()


def test_retry_exhaustion_raises_last_error(memd, cfg_for, no_retry_delay):
    cfg = cfg_for("no json at all", exit_code=0)
    with pytest.raises(memd.CuratorError):
        memd.call_curator(cfg, "prompt", "haiku")
    with open(memd.LOG_PATH) as f:
        # first two attempts logged as retries, third raised
        assert f.read().count("failed, retrying in") == 2
