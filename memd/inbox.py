"""Curator inbox: atomic multi-writer note publishing and the sweep-side
reader."""

import datetime as dt
import os
import tempfile

def _note_frontmatter(text):
    """Split optional leading YAML frontmatter off an inbox note:
    ({key: value}, body), or ({}, text) when absent/unterminated. Kept inline —
    importing memd.memory here would be circular (it imports collect_inbox)."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return {}, text
    meta = {}
    i = 1
    while i < len(lines) and lines[i] != "---":
        if ":" in lines[i]:
            k, _, v = lines[i].partition(":")
            meta[k.strip()] = v.strip()
        i += 1
    if i >= len(lines):  # no closing fence: not frontmatter, read as body
        return {}, text
    return meta, "\n".join(lines[i + 1:])


def collect_inbox(project_path):
    inbox = os.path.join(project_path, ".memory", "inbox")
    notes, paths = [], []
    if os.path.isdir(inbox):
        for fn in sorted(os.listdir(inbox)):
            if fn.startswith(".") or not fn.endswith((".md", ".txt")):
                continue
            p = os.path.join(inbox, fn)
            try:
                data = open(p).read()
            except OSError:
                continue
            # provenance moves into the label; the truncation budget covers the
            # body only, never frontmatter
            meta, data = _note_frontmatter(data)
            src = meta.get("source") or "unattributed"
            date = meta.get("date") or "unknown"
            if len(data) > 4000:
                data = data[:4000] + "\n[... note truncated at 4000 chars ...]"
            notes.append(f"INBOX NOTE ({fn}, source={src}, dated={date}):\n{data}")
            paths.append(p)
    return notes, paths


def write_inbox_note(root, text, source=None):
    """Append a curator inbox note under <root>/.memory/inbox/ with a
    collision-proof name, so independent writers (the `remember` MCP tool,
    Claude Code, manual drops) never clobber each other.

    The note is fully written to a temp file OUTSIDE the inbox, then published
    by an atomic os.link into the inbox. A concurrent sweep (collect_inbox +
    os.remove) therefore sees a complete file or no file — never a half-written
    one. This matches the `remember` MCP tool's atomic-publish discipline; the
    inbox is a shared multi-writer resource and is only as safe as its weakest
    writer, so every writer must publish atomically. The filename carries a
    microsecond timestamp, the writer's pid, and random bytes; os.link fails
    (rather than clobbers) on the astronomically unlikely name collision,
    preserving the no-overwrite guarantee.

    Durability: the note body is fsynced before publish and the inbox dir is
    fsynced after, so a hard crash cannot leave an empty or partial note that a
    later sweep would ingest and delete (matches the `remember` MCP writer).
    Returns the written path."""
    mem = os.path.join(root, ".memory")
    inbox = os.path.join(mem, "inbox")
    os.makedirs(inbox, exist_ok=True)
    body = text if text.endswith("\n") else text + "\n"
    if body.split("\n", 1)[0] == "---":
        pass  # writer-owned frontmatter: provenance is theirs, touch nothing
    else:
        # normalize provenance as frontmatter; the legacy <!-- source -->
        # comment stays out so a note never carries both
        src = source or "unattributed"
        body = (f"---\ndate: {dt.date.today().isoformat()}\n"
                f"source: {src}\n---\n\n" + body)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    # Temp lives in <root>/.memory/ (same filesystem as inbox, so os.link is
    # atomic) but outside inbox/, and is dotfile-prefixed — collect_inbox scans
    # only inbox/ and skips dotfiles, so the temp is never ingested.
    fd, tmp = tempfile.mkstemp(dir=mem, prefix=".inbox-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        for _ in range(8):
            name = f"{stamp}-{os.getpid()}-{os.urandom(4).hex()}.md"
            path = os.path.join(inbox, name)
            try:
                os.link(tmp, path)
            except FileExistsError:
                continue
            dfd = os.open(inbox, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
            return path
        raise RuntimeError(f"could not create a unique inbox note in {inbox}")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
