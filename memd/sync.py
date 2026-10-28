"""Per-project sync: transcript source discovery, project locking, and the
distill pipeline from digest to validated apply."""

import datetime as dt
import fcntl
import hashlib
import os
import re

from memd.config import (CLAUDE_PROJECTS_DIR, CURSORS_PATH, LOCK_DIR,
                         MEMORY_FILES, META_PATH, load_json, log, store_key,
                         update_json)
from memd.curator import (apply_ops, build_prompt, call_curator,
                          dedupe_mistakes, select_view, validate)
from memd.digest import (ag_max_idx, cap_digest, collapse_repeats, digest_source,
                         project_ag_dbs, redact, render_span, sort_spans,
                         truncate_spans)
from memd.inbox import collect_inbox
from memd.memory import (append_archive, canonical_path, enforce_budget_mistakes,
                         git_commit_memory, read_memory, scaffold,
                         write_memory_file)

# Rolling per-project distill history kept in meta.json. The old single-entry
# meta made the memory system itself amnesiac: a crash-looping backend or a
# week of failed sweeps left no trace. The journal is also what grounds the
# curator's PREVIOUS DISTILLS section.
JOURNAL_CAP = 100


def _journal(project_path, trigger, model=None, digest_chars=0, outcome="ok",
             summary="", extra=None, usage=None):
    """Append one distill event to the project's rolling journal (and merge any
    extra entry fields) under the state lock."""
    def _mutate(m):
        e = m.setdefault(project_path, {})
        if extra:
            e.update(extra)
        hist = e.setdefault("history", [])
        hist.append({
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "trigger": trigger,
            "model": model or "",
            "digest_chars": digest_chars,
            "outcome": outcome,
            "summary": (summary or "")[:200],
        })
        if usage:
            hist[-1]["usage"] = usage
        del hist[:-JOURNAL_CAP]
    update_json(META_PATH, _mutate)


def project_lock(path):
    os.makedirs(LOCK_DIR, exist_ok=True)
    key = store_key(path)
    identity = f"store:{key[0]}:{key[1]}" if key is not None else path
    h = hashlib.sha256(identity.encode()).hexdigest()[:16]
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
    rp = canonical_path(cfg, project_path)
    entry = cfg["projects"].get(project_path) or cfg["projects"].get(rp) or {}
    if entry.get("global"):
        # Inbox-only. $HOME as the global project path would otherwise match
        # every Claude session encoded under home; global truth instead comes
        # from its inbox (the noctalia `remember` tool, manual notes) plus any
        # explicit extra_sources globs.
        for pattern in entry.get("extra_sources", []):
            files.extend(_glob.glob(os.path.expanduser(pattern)))
        return sorted(set(files))
    roots = [rp] + [os.path.realpath(p) for p in entry.get("aliases", [])]
    encs = {encode_claude_dir(r) for r in roots}
    sub_encs = set()
    for p, other in cfg["projects"].items():
        if p == rp:
            continue
        for candidate in [p, *other.get("aliases", [])]:
            candidate = os.path.realpath(candidate)
            for root in roots:
                if candidate.startswith(root.rstrip("/") + "/"):
                    rel = candidate[len(root.rstrip("/")):]
                    sub_encs.update(encode_claude_dir(r.rstrip("/") + rel)
                                    for r in roots)
    for d in _glob.glob(os.path.join(CLAUDE_PROJECTS_DIR, "*")):
        base = os.path.basename(d)
        if any(base == enc or base.startswith(enc + "-") for enc in encs):
            if any(base == s or base.startswith(s + "-") for s in sub_encs):
                continue  # owned by a nested registered project
            files.extend(_glob.glob(os.path.join(d, "*.jsonl")))
    for pattern in entry.get("extra_sources", []):
        files.extend(_glob.glob(os.path.expanduser(pattern)))
    files.extend(project_ag_dbs(cfg, rp))
    return sorted(set(files))


# Unread bytes that yielded no usable content before ingestion is called
# suspect. Well above a stray truncated line, well below a real session.
INGEST_STALL_BYTES = 4096


# Triggers whose small digests may wait for more content: transcripts stay on
# disk and cursors don't move, so a deferred digest replays in full later.
DEFERRABLE_TRIGGERS = ("sweep", "session-end", "pre-compact")


def defer_expired(spans, max_hours):
    """True when the oldest pending span is at least max_hours old, or when no
    span carries a usable timestamp (unknowable age: never hold it)."""
    starts = [s.start[:19] for s in spans if s.start]
    if not starts:
        return True
    try:
        oldest = dt.datetime.fromisoformat(min(starts))
    except ValueError:
        return True
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return now - oldest >= dt.timedelta(hours=max_hours)


def record_ingest_health(project_path, stalled_bytes):
    """Track sources that carry unread content which yields nothing.

    memd reads other tools' private transcript formats. If one of them changes,
    parsing does not fail loudly — it just stops recognising entries, and since
    cursors only advance after a successful apply, the bytes pile up unread
    while memd looks healthy and quietly remembers nothing. Recording the
    condition here is what lets `memd status` say so out loud."""
    def _mutate(m):
        entry = m.setdefault(project_path, {})
        if stalled_bytes >= INGEST_STALL_BYTES:
            entry["ingest_stalled_bytes"] = stalled_bytes
            entry.setdefault("ingest_stalled_since",
                             dt.datetime.now().isoformat(timespec="seconds"))
        else:
            entry.pop("ingest_stalled_bytes", None)
            entry.pop("ingest_stalled_since", None)
    update_json(META_PATH, _mutate)


def sync_project(cfg, project_path, trigger="manual", transcript=None, dry_run=False):
    """Distill new session content into a project's memory.

    Returns True on a successful apply, None on a no-op (locked by another
    sync, nothing new, or too little to distill yet), False on a failed
    distill."""
    project_path = os.path.realpath(project_path)
    project_path = canonical_path(cfg, project_path)
    entry = cfg["projects"].get(project_path)
    name = entry["name"] if entry else os.path.basename(project_path)
    if not os.path.isdir(os.path.join(project_path, ".memory")):
        scaffold(project_path, name)

    lock = project_lock(project_path)
    if lock is None:
        log(f"sync skipped (locked): {project_path}")
        return None

    usage = {}
    try:
        cursors = load_json(CURSORS_PATH, {})
        sources = [transcript] if transcript else transcript_files(cfg, project_path)
        spans, new_cursors = [], {}
        for src in sources:
            if not src or not os.path.exists(src):
                continue
            if not source_pending(src, cursors):
                continue
            span, new_off = digest_source(cfg, src, cursors.get(src, 0))
            if span is not None and span.lines:
                spans.append(span)
            new_cursors[src] = new_off
        inbox_notes, inbox_paths = collect_inbox(project_path)

        # Bytes we read past the cursor this pass. Non-zero with no digest means
        # the sources hold content memd could not recognise. Byte-cursor sources
        # only: an antigravity cursor is a step index, and summing indices with
        # byte offsets produced a number that meant nothing against a byte
        # threshold. CEILING: antigravity stalls are therefore invisible; they
        # need their own row-count signal.
        scanned = sum(off - cursors.get(src, 0)
                      for src, off in new_cursors.items()
                      if not src.endswith(".db"))

        # Ingestion health is a statement about transcript PARSING, so it is
        # decided by the spans alone. Gating it on inbox_notes too meant one
        # note from the user vouched for sources that yielded nothing, hiding
        # the stall in exactly the projects someone was actively working in.
        record_ingest_health(project_path, 0 if spans else scanned)
        if not spans and not inbox_notes:
            log(f"sync {project_path}: nothing new ({trigger})"
                + (f" [{scanned} unread bytes yielded nothing]" if scanned else ""))
            return None

        # Chronological assembly: order spans across ALL sources by their own
        # timestamps (filename order is meaningless), then budget-triage whole
        # low-signal spans — dropping the middle of the timeline silently was
        # how cap_digest hid work. Whatever survives is rendered with its
        # header; dropped spans leave explicit omission markers so the curator
        # knows something happened even when it can't know what.
        spans = sort_spans(spans)
        kept, omitted_labels = truncate_spans(spans, cfg["digest_cap_chars"])
        joined = "\n\n".join([render_span(s) for s in kept] + omitted_labels)
        if len(joined) > cfg["digest_cap_chars"]:
            # one oversize span survived triage: last-resort head/tail elision
            joined = cap_digest(joined, cfg["digest_cap_chars"])
        digest = redact(collapse_repeats(joined))
        inbox_notes = [redact(n) for n in inbox_notes]
        if (trigger in DEFERRABLE_TRIGGERS and not inbox_notes
                and len(digest) < cfg["min_digest_chars"]
                and not defer_expired(spans, cfg["max_defer_hours"])):
            # a distill's fixed cost (charter + all memory) dwarfs a tiny digest
            log(f"sync {project_path}: deferred, {len(digest)}c digest < "
                f"min_digest_chars ({trigger})")
            if dry_run:
                print(f"[dry-run] project={project_path} deferred: "
                      f"digest={len(digest)} chars < min_digest_chars")
            return None
        # Escalate on size alone, whatever triggered the distill. Restricting
        # this to session-end/manual meant a sweep never escalated — and a sweep
        # carries the biggest backlogs (days of unread transcripts, up to
        # digest_cap_chars), which is exactly where the small model does worst.
        model = cfg["model_small"]
        if len(digest) > cfg["escalate_chars"]:
            model = cfg["model_large"]

        memory = read_memory(project_path)
        view = select_view(cfg, memory, digest, inbox_notes)
        if view is not None:
            log(f"partial view {project_path}: "
                f"{sum(len(v) for v in view.values())} section(s) shown")
        # What earlier distills already applied, oldest first: evidence for the
        # charter's do-not-reapply rule instead of leaving it to model faith.
        recent = [
            f"{h['ts']} [{h['trigger']}/{h['model']}] {h.get('summary', '')}"
            for h in load_json(META_PATH, {}).get(project_path, {})
            .get("history", [])
            if h.get("outcome") == "ok"
        ][-5:]
        prompt = build_prompt(cfg, project_path, name, memory, digest,
                              inbox_notes, recent_distills=recent or None, view=view)

        if dry_run:
            print(f"[dry-run] project={project_path} trigger={trigger} model={model}")
            print(f"[dry-run] digest={len(digest)} chars from {len(spans)} span(s), "
                  f"{len(inbox_notes)} inbox note(s), prompt={len(prompt)} chars")
            print(prompt[:1500])
            return True

        log(f"distill start {project_path} trigger={trigger} model={model} "
            f"digest={len(digest)}c inbox={len(inbox_notes)}")
        result = validate(call_curator(cfg, prompt, model, usage=usage), memory,
                          view=view)

        mem = os.path.join(project_path, ".memory")
        skipped = []
        applied = apply_ops(result.get("ops", []),
                            {f: memory[f] for f in MEMORY_FILES},
                            skipped=skipped)
        for reason in skipped:
            log(f"op skipped {project_path}: {reason}")
        for fname, new_body in applied.items():
            if new_body is None or not new_body.strip() or new_body == memory[fname]:
                continue
            if fname == "mistakes.md":
                # append-only invariant: only set_body ops can reach this file,
                # and they must not rewrite the audit log (dedupe_mistakes and
                # enforce_budget_mistakes own its shape)
                log(f"ignored set_body on mistakes.md (append-only): {project_path}")
                continue
            write_memory_file(os.path.join(mem, fname), new_body, name,
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

        summary = str(result.get("summary", ""))[:300]
        # The journal carries the skip count (a curator inventing headings every
        # pass is a pattern only visible across distills); meta's user-facing
        # summary stays the curator's own sentence.
        _journal(project_path, trigger, model=model, digest_chars=len(digest),
                 outcome="ok", usage=usage or None,
                 summary=(f"[{len(skipped)} op(s) skipped] {summary}"
                          if skipped else summary),
                 extra={
                     "last_sync": dt.datetime.now().isoformat(timespec="seconds"),
                     "trigger": trigger,
                     "model": model,
                     "summary": summary,
                 })

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
        try:
            _journal(project_path, trigger, outcome="fail",
                     summary=str(e)[:200], usage=usage or None)
        except (RuntimeError, OSError) as je:
            log(f"journal write failed: {je}")
        return False
    finally:
        lock.close()
