"""validate() shrink guard / shape checks and enforce_budget_mistakes()."""

import datetime as dt
import os

import pytest


def memory(state="", decisions="", mistakes="", todo=""):
    return {"state.md": state, "decisions.md": decisions,
            "mistakes.md": mistakes, "todo.md": todo}


# --------------------------------------------------------------------------
# validate()
# --------------------------------------------------------------------------


def test_validate_passthrough(memd):
    result = {"summary": "s", "state_body": "x" * 900,
              "mistakes_new_entries": ["### e\nok"]}
    assert memd.validate(result, memory(state="S" * 1000)) is result


def test_validate_shrink_guard_trips(memd):
    # old body > 800 chars, new + archived < 40% of old -> reject
    result = {"state_body": "x" * 100, "archive_entries": []}
    with pytest.raises(RuntimeError, match="shrink guard"):
        memd.validate(result, memory(state="S" * 1000))


def test_validate_shrink_guard_archive_compensates(memd):
    result = {
        "state_body": "x" * 100,
        "archive_entries": [{"source": "state.md", "content": "y" * 400}],
    }
    assert memd.validate(result, memory(state="S" * 1000)) is result


def test_validate_shrink_guard_ignores_small_files(memd):
    # old body <= 800 chars: guard never trips even on a full wipe-and-rewrite
    result = {"state_body": "x"}
    assert memd.validate(result, memory(state="S" * 800)) is result


def test_validate_shrink_guard_other_bodies(memd):
    result = {"decisions_body": "x" * 10, "todo_body": None}
    with pytest.raises(RuntimeError, match="decisions_body"):
        memd.validate(result, memory(decisions="D" * 2000))


def test_validate_null_bodies_are_fine(memd):
    result = {"state_body": None, "decisions_body": None, "todo_body": None}
    assert memd.validate(result, memory(state="S" * 5000)) is result


def test_validate_rejects_non_dict(memd):
    with pytest.raises(RuntimeError, match="not an object"):
        memd.validate(["not", "a", "dict"], memory())


def test_validate_rejects_non_string_body(memd):
    with pytest.raises(RuntimeError, match="state_body is not a string"):
        memd.validate({"state_body": 42}, memory())


def test_validate_rejects_non_string_mistake_entry(memd):
    result = {"mistakes_new_entries": ["### ok\nfine", {"oops": 1}]}
    with pytest.raises(RuntimeError, match="non-string"):
        memd.validate(result, memory())


# --------------------------------------------------------------------------
# enforce_budget_mistakes()
# --------------------------------------------------------------------------


HEAD = "# Mistake Audit Log (append-only)\n\n"


def build_mistakes(memd, proj, n_entries=5):
    os.makedirs(os.path.join(proj, ".memory", "archive"), exist_ok=True)
    sections = [
        f"### 2026-01-0{i} — entry {i}\n" + ("lorem ipsum filler line\n" * 8)
        for i in range(1, n_entries + 1)
    ]
    body = HEAD + "".join(sections)
    p = os.path.join(proj, ".memory", "mistakes.md")
    memd.write_memory_file(p, body, "proj", "mistakes")
    return p


def test_budget_overflow_moves_oldest_to_archive(memd, env):
    proj = str(env.tmp / "bproj")
    p = build_mistakes(memd, proj)
    memd.enforce_budget_mistakes(proj, "proj", budget=600)

    text = open(p).read()
    _, body = memd.split_frontmatter(text)
    assert len(body) <= 600 + 2  # head + kept sections fit the budget
    # oldest entries pruned, newest kept
    assert "entry 1" not in text
    assert "entry 5" in text
    assert body.lstrip().startswith("# Mistake Audit Log")  # head preserved

    arch = os.path.join(proj, ".memory", "archive",
                        dt.date.today().strftime("%Y-%m") + ".md")
    assert os.path.exists(arch)
    atext = open(arch).read()
    assert "entry 1" in atext
    assert "size budget overflow" in atext
    assert "## from mistakes.md" in atext
    # nothing was lost: every entry lives in exactly one of the two files
    for i in range(1, 6):
        assert (f"entry {i}" in text) != (f"entry {i}" in atext)


def test_budget_under_budget_untouched(memd, env):
    proj = str(env.tmp / "bproj2")
    p = build_mistakes(memd, proj)
    before = open(p).read()
    memd.enforce_budget_mistakes(proj, "proj", budget=100_000)
    assert open(p).read() == before
    arch = os.path.join(proj, ".memory", "archive",
                        dt.date.today().strftime("%Y-%m") + ".md")
    assert not os.path.exists(arch)


def test_budget_missing_file_is_noop(memd, env):
    proj = str(env.tmp / "bproj3")
    os.makedirs(os.path.join(proj, ".memory"))
    memd.enforce_budget_mistakes(proj, "proj", budget=10)  # must not raise
