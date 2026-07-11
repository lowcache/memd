"""Per-project sync: transcript source discovery, project locking, and the
distill pipeline from digest to validated apply."""

import datetime as dt
import fcntl
import hashlib
import os
import re

from memd.config import (CLAUDE_PROJECTS_DIR, CURSORS_PATH, LOCK_DIR,
                         META_PATH, load_json, log, update_json)
from memd.curator import build_prompt, call_curator, dedupe_mistakes, validate
from memd.digest import (ag_max_idx, cap_digest, collapse_repeats, digest_source,
                         project_ag_dbs, redact)
from memd.inbox import collect_inbox
from memd.memory import (append_archive, enforce_budget_mistakes,
                         git_commit_memory, read_memory, scaffold,
                         write_memory_file)

def project_lock(path):
    os.makedirs(LOCK_DIR, exist_ok=True)
    h = hashlib.sha256(path.encode()).hexdigest()[:16]
    fd = open(os.path.join(LOCK_DIR, h + ".lock"), "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        fd.close()
        log(f"project locked, skipping: {path}")
        return None


def encode_claude_dir(path):
    """Mirror claude-code's project-dir encoding (non-alnum -> '-')."""
    return re.sub(r"[^A-Za-z0-9-]", "-", path)


def source_pending(src, cursors):
    """Does a transcript source have content beyond its cursor?"""
    try:
        if src.endswith(".db"):
            return ag_max_idx(src) > cursors.get(src, 0)
        return os.path.getsize(src) > cursors.get(src, 0)
    except OSError:
        return False


def transcript_files(cfg, project_path):
    """All transcript sources for a project: claude dirs + extra globs.

    Honors nested registered projects: a claude dir owned by a longer-prefix
    registered project (e.g. .nix-config/dots under .nix-config) belongs to that
    sub-project, not this one — mirroring find_project / project_ag_dbs which
    already attribute by longest matching prefix.
    """
    import glob as _glob
    files = []
    rp = os.path.realpath(project_path)
    entry = cfg["projects"].get(project_path) or cfg["projects"].get(rp) or {}
    if entry.get("global"):
        # Inbox-only. $HOME as the global project path would otherwise match
        # every Claude session encoded under home; global truth instead comes
        # from its inbox (the noctalia `remember` tool, manual notes) plus any
        # explicit extra_sources globs.
        for pattern in entry.get("extra_sources", []):
            files.extend(_glob.glob(os.path.expanduser(pattern)))
        return sorted(set(files))
    enc = encode_claude_dir(rp)
    sub_encs = [
        encode_claude_dir(os.path.realpath(p))
        for p in cfg["projects"]
        if os.path.realpath(p) != rp
        and os.path.realpath(p).startswith(rp.rstrip("/") + "/")
    ]
    for d in _glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*")):
        base = os.path.basename(d)
        if base == enc or base.startswith(enc + "-"):
            if any(base == s or base.startswith(s + "-") for s in sub_encs):
                continue  # owned by a nested registered project
            files.extend(_glob.glob(os.path.join(d, "*.jsonl")))
    for pattern in cfg["projects"].get(project_path, {}).get("extra_sources", []):
        files.extend(_glob.glob(os.path.expanduser(pattern)))
    files.extend(project_ag_dbs(cfg, project_path))
    return sorted(set(files))


def sync_project(cfg, project_path, trigger="manual", transcript=None, dry_run=False):
    """Distill new session content into a project's memory.

    Returns True on a successful apply, None on a no-op (locked by another
    sync, or nothing new to distill), False on a failed distill."""
    project_path = os.path.realpath(project_path)
    entry = cfg["projects"].get(project_path)
    name = entry["name"] if entry else os.path.basename(project_path)
    if not os.path.isdir(os.path.join(project_path, ".memory")):
        scaffold(project_path, name)

    lock = project_lock(project_path)
    if lock is None:
        log(f"sync skipped (locked): {project_path}")
        return None

    try:
        cursors = load_json(CURSORS_PATH, {})
        sources = [transcript] if transcript else transcript_files(cfg, project_path)
        digests, new_cursors = [], {}
        for src in sources:
            if not src or not os.path.exists(src):
                continue
            if not source_pending(src, cursors):
                continue
            d, new_off = digest_source(src, cursors.get(src, 0))
            if d.strip():
                digests.append(d)
            new_cursors[src] = new_off
        inbox_notes, inbox_paths = collect_inbox(project_path)

        if not digests and not inbox_notes:
            log(f"sync {project_path}: nothing new ({trigger})")
            return None

        digest = redact(cap_digest(
            collapse_repeats("\n\n--- next session span ---\n\n".join(digests)),
            cfg["digest_cap_chars"]))
        inbox_notes = [redact(n) for n in inbox_notes]
        model = cfg["model_small"]
        if trigger in ("session-end", "manual") and len(digest) > cfg["escalate_chars"]:
            model = cfg["model_large"]

        memory = read_memory(project_path)
        prompt = build_prompt(cfg, project_path, name, memory, digest, inbox_notes)

        if dry_run:
            print(f"[dry-run] project={project_path} trigger={trigger} model={model}")
            print(f"[dry-run] digest={len(digest)} chars from {len(digests)} span(s), "
                  f"{len(inbox_notes)} inbox note(s), prompt={len(prompt)} chars")
            print(prompt[:1500])
            return True

        log(f"distill start {project_path} trigger={trigger} model={model} "
            f"digest={len(digest)}c inbox={len(inbox_notes)}")
        result = validate(call_curator(cfg, prompt, model), memory)

        mem = os.path.join(project_path, ".memory")
        for key, fname in (("state_body", "state.md"),
                           ("decisions_body", "decisions.md"),
                           ("todo_body", "todo.md")):
            body = result.get(key)
            if isinstance(body, str) and body.strip() and body.strip() != memory[fname]:
                write_memory_file(os.path.join(mem, fname), body, name,
                                  fname.split(".")[0])
        new_mistakes = dedupe_mistakes(result.get("mistakes_new_entries", []),
                                       memory["mistakes.md"])
        if new_mistakes:
            mpath = os.path.join(mem, "mistakes.md")
            body = memory["mistakes.md"]
            body = (body + "\n\n" if body else "") + "\n\n".join(
                e.strip() for e in new_mistakes)
            write_memory_file(mpath, body, name, "mistakes")
        append_archive(project_path, result.get("archive_entries", []),
                       f"curator distill, trigger={trigger}")
        enforce_budget_mistakes(project_path, name, cfg["budgets"]["mistakes.md"])

        # advance cursors and clear inbox only after a successful apply. Merge
        # under the state lock against the current on-disk file (not the snapshot
        # read at the top of this sync) so a concurrent sync of another project
        # doesn't clobber our advance — or get clobbered by it.
        update_json(CURSORS_PATH, lambda c: c.update(new_cursors))
        for p in inbox_paths:
            try:
                os.remove(p)
            except OSError:
                pass

        meta_entry = {
            "last_sync": dt.datetime.now().isoformat(timespec="seconds"),
            "trigger": trigger,
            "model": model,
            "summary": str(result.get("summary", ""))[:300],
        }
        update_json(META_PATH, lambda m: m.__setitem__(project_path, meta_entry))

        if cfg["git_commit"]:
            git_commit_memory(
                project_path,
                f"Update project memory ({trigger} distill)\n\n"
                f"{result.get('summary', '')}".strip(),
            )
        log(f"distill done {project_path}: {result.get('summary', '')[:160]}")
        return True
    except (RuntimeError, OSError) as e:
        log(f"distill FAILED {project_path} ({trigger}): {e}")
        return False
    finally:
        lock.close()
