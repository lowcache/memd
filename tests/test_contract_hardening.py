"""Edge cases the 1.0 contract docs pinned down: malformed archive_entries is a
curator error (never a raw TypeError), a non-object hook payload is treated as
empty, and git_commit=false also stops the sweep's global-index commit."""

import io
import os

import pytest

MEMORY = {"state.md": "# S\n\n## A\n\nfact.\n", "decisions.md": "",
          "todo.md": "", "mistakes.md": ""}


def _validate(memd, archive):
    return memd.validate({"summary": "s", "ops": [], "archive_entries": archive},
                         dict(MEMORY))


def test_archive_null_or_missing_is_empty(memd):
    assert _validate(memd, None)["archive_entries"] == []
    assert memd.validate({"summary": "s"}, dict(MEMORY))["archive_entries"] == []


def test_archive_string_entry_is_wrapped(memd):
    out = _validate(memd, ["## Old\n\ngone."])
    assert out["archive_entries"] == [{"source": "?", "content": "## Old\n\ngone."}]


@pytest.mark.parametrize("archive", ["text", {"content": "x"}, [1],
                                     [{"source": "state.md"}],
                                     [{"source": "state.md", "content": None}]])
def test_archive_malformed_is_curator_error(memd, archive):
    with pytest.raises(memd.CuratorError, match="archive_entries"):
        _validate(memd, archive)


def test_hook_non_object_payload_is_empty(memd, memd_config, monkeypatch):
    memd_config()
    monkeypatch.setattr("sys.stdin", io.StringIO("[1, 2]"))
    assert memd.cmd_hook(memd.load_config(), "session-end") == 0


def test_sweep_respects_git_commit_for_global_index(memd, memd_config, monkeypatch):
    memd_config(git_commit=False)
    calls = []
    monkeypatch.setattr(memd.sweep, "write_global_index", lambda cfg: True)
    monkeypatch.setattr(memd.sweep, "git_commit_memory",
                        lambda *a, **k: calls.append(a))
    memd.cmd_sweep(memd.load_config())
    assert calls == []


def test_concurrent_save_json_same_path(memd, env):
    # parallel sweep syncs all save ag_index.json; a shared temp name made
    # one writer's rename fail with ENOENT and the distill with it
    import threading
    path = str(env.tmp / "shared.json")
    errors = []

    def writer(n):
        try:
            for i in range(200):
                memd.config.save_json(path, {"w": n, "i": i})
        except OSError as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert not [p for p in os.listdir(env.tmp) if p.endswith(".tmp")]
