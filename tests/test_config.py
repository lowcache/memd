"""load_config() / save_config(): default fill-in, top-level overrides,
budgets deep-merge, unknown-key passthrough, and round-trip persistence."""

import json
import os


def write_cfg(memd, data):
    os.makedirs(os.path.dirname(memd.CONFIG_PATH), exist_ok=True)
    with open(memd.CONFIG_PATH, "w") as f:
        json.dump(data, f)


def test_load_config_defaults_when_file_missing(memd):
    assert memd.load_config() == memd.DEFAULT_CONFIG


def test_load_config_defaults_when_file_corrupt(memd):
    os.makedirs(os.path.dirname(memd.CONFIG_PATH), exist_ok=True)
    with open(memd.CONFIG_PATH, "w") as f:
        f.write("{not json")
    assert memd.load_config() == memd.DEFAULT_CONFIG


def test_load_config_top_level_override(memd):
    write_cfg(memd, {"model_small": "custom-model", "quiet_seconds": 5})
    cfg = memd.load_config()
    assert cfg["model_small"] == "custom-model"
    assert cfg["quiet_seconds"] == 5
    # untouched keys keep their defaults
    assert cfg["model_large"] == memd.DEFAULT_CONFIG["model_large"]


def test_load_config_budgets_deep_merge(memd):
    write_cfg(memd, {"budgets": {"state.md": 123}})
    budgets = memd.load_config()["budgets"]
    assert budgets["state.md"] == 123
    # other budget entries survive a partial override
    assert budgets["mistakes.md"] == memd.DEFAULT_CONFIG["budgets"]["mistakes.md"]


def test_load_config_unknown_keys_pass_through(memd):
    write_cfg(memd, {"future_option": True})
    assert memd.load_config()["future_option"] is True


def test_load_config_does_not_mutate_defaults(memd):
    write_cfg(memd, {"model_small": "custom-model"})
    memd.load_config()
    assert memd.DEFAULT_CONFIG["model_small"] == "haiku"


def test_default_redact_extra_patterns_is_empty_list(memd):
    assert memd.DEFAULT_CONFIG["REDACT_EXTRA_PATTERNS"] == []
    assert memd.load_config()["REDACT_EXTRA_PATTERNS"] == []


def test_save_config_round_trip(memd):
    cfg = memd.load_config()
    cfg["model_small"] = "roundtrip-model"
    cfg["budgets"]["todo.md"] = 4321
    memd.save_config(cfg)
    got = memd.load_config()
    assert got["model_small"] == "roundtrip-model"
    assert got["budgets"]["todo.md"] == 4321


def test_save_config_creates_config_dir(memd, env):
    assert not os.path.exists(memd.CONFIG_PATH)
    memd.save_config(memd.load_config())
    assert os.path.exists(memd.CONFIG_PATH)
