"""Model tier selection: large digests escalate to model_large regardless of
what triggered the distill."""

import json
import os

CANNED = json.dumps({
    "summary": "escalation test",
    "ops": [{"op": "set_body", "file": "state.md", "body": "## S\n\nfact."}],
    "mistakes_new_entries": [],
    "archive_entries": [],
})


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


def _model_used(memd, root):
    return memd.load_json(memd.META_PATH, {})[root]["model"]


def test_sweep_escalates_on_large_digest(memd, env, memd_config, fake_curator):
    """Regression: escalation used to be gated on trigger in (session-end,
    manual), so a sweep never escalated — even though a sweep carries the
    largest backlogs, which is exactly where the small model does worst."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False,
                model_small="small-model", model_large="large-model",
                escalate_chars=100)
    root = _project(memd, env, "big-sweep", turns=20)

    assert memd.sync_project(memd.load_config(), root, trigger="sweep") is True
    assert _model_used(memd, root) == "large-model"


def test_session_end_escalates_on_large_digest(memd, env, memd_config,
                                               fake_curator):
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False,
                model_small="small-model", model_large="large-model",
                escalate_chars=100)
    root = _project(memd, env, "big-session", turns=20)

    assert memd.sync_project(memd.load_config(), root,
                             trigger="session-end") is True
    assert _model_used(memd, root) == "large-model"


def test_small_digest_stays_on_small_model(memd, env, memd_config,
                                           fake_curator):
    """Escalation is about size, so a small digest must not pay for the large
    model whatever the trigger."""
    memd_config(curator_cmd=[fake_curator(CANNED), "{model}"],
                git_commit=False, auto_scaffold=False,
                model_small="small-model", model_large="large-model",
                escalate_chars=100000)
    root = _project(memd, env, "small", turns=2)

    assert memd.sync_project(memd.load_config(), root, trigger="sweep") is True
    assert _model_used(memd, root) == "small-model"


def test_default_config_ships_a_capable_curator(memd):
    """Both tiers default to the same capable model on purpose: a first-time
    user cannot tell a weak default model from a weak tool."""
    assert memd.DEFAULT_CONFIG["model_small"] == "sonnet"
    assert memd.DEFAULT_CONFIG["model_large"] == "sonnet"
