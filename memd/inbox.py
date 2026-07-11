"""Curator inbox: atomic multi-writer note publishing and the sweep-side
reader."""

import datetime as dt
import os
import tempfile

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
            if len(data) > 4000:
                data = data[:4000] + "\n[... note truncated at 4000 chars ...]"
            notes.append(f"INBOX NOTE ({fn}):\n{data}")
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
    if source:
        body = f"<!-- source: {source} -->\n{body}"
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
