"""0.6.0: the pre-0.4 full-body curator protocol (state_body / decisions_body /
todo_body) is gone. Non-null legacy keys fail loudly; ops are the only way in."""

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MEMORY = {"state.md": "# S\n\n## A\n\nfact.\n", "decisions.md": "",
          "todo.md": "", "mistakes.md": ""}


@pytest.mark.parametrize("key", ["state_body", "decisions_body", "todo_body"])
def test_legacy_body_key_is_rejected(memd, key):
    with pytest.raises(memd.CuratorError, match="removed in 0.6.0"):
        memd.validate({"summary": "s", key: "## X\n\nbody"}, dict(MEMORY))


def test_null_legacy_keys_are_ignored(memd):
    out = memd.validate({"summary": "s", "state_body": None,
                         "decisions_body": None, "todo_body": None,
                         "ops": [{"op": "append_section", "file": "state.md",
                                  "heading": "## B", "body": "new"}]},
                        dict(MEMORY))
    assert [o["op"] for o in out["ops"]] == ["append_section"]


def test_output_carries_no_legacy_mirrors(memd):
    out = memd.validate({"summary": "s", "ops": [
        {"op": "set_body", "file": "state.md", "body": "# S\n\n## A\n\nnew."}]},
        dict(MEMORY))
    for key in ("state_body", "decisions_body", "todo_body"):
        assert key not in out
    assert out["ops"][0]["op"] == "set_body"


def test_legacy_constant_is_gone(memd):
    assert not hasattr(memd.curator, "LEGACY_BODY_KEYS")


def test_contrib_curators_ask_for_ops():
    d = os.path.join(REPO_ROOT, "contrib", "curators")
    for name in ("ollama-curator.sh", "gemini-curator.sh", "README.md"):
        text = open(os.path.join(d, name)).read()
        assert "state_body" not in text, name
        assert "decisions_body" not in text, name
    readme = open(os.path.join(d, "README.md")).read()
    assert '"ops"' in readme
