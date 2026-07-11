# The Inbox Protocol (v1.0)

The contract for handing observations to the memd curator without write
access to memory files. Any process that can write a file — an MCP tool, a
swarm agent, a CI job, a human with an editor — can feed memory through an
**inbox**: drop a markdown note, and the next sweep or sync ingests it into
the distill and deletes it.

This is the canonical copy (memd owns the reader). Known conforming
writers: memd's `write_inbox_note()` / `memd note`, and the
noctalia-claude-plugin `remember` MCP tool (`shim/noctalia-mcp.py`).

## Locations

| Inbox | Path | Feeds |
|---|---|---|
| Project | `<project-root>/.memory/inbox/` | that project's `.memory/` files |
| Global | `<global_root>/.memory/inbox/` (default `~/.memory/inbox/`) | the system-wide, cross-project store |

Route a note by choosing the inbox. Notes about the user, the system, or
cross-project truths go global; everything else goes in the project inbox.

## Note format

A UTF-8 markdown (`.md`) or text (`.txt`) file. Content is passed to the
curator model opaquely — structure is convention, not requirement. The
recommended shape:

```markdown
---
date: 2026-07-04
source: <tool>/<writer>        # who wrote this
topic: <kebab-slug>            # optional
routed: global                 # optional, documents intent
---

The durable fact, decision, mistake, or observation.
```

Keep notes under **4000 characters**: the reader truncates longer notes at
4000 with an explicit `[... note truncated ...]` marker. One fact per note
beats one omnibus note.

## Writer requirements (normative)

The inbox is a shared multi-writer resource read by an unlocked
read-then-delete sweep. It is only as safe as its weakest writer, so every
writer MUST publish atomically and durably:

1. **Stage outside the inbox.** Write the full body to a temp file in the
   *parent* `.memory/` directory (same filesystem — required for an atomic
   publish), dotfile-prefixed (e.g. `.remember-*.tmp`, `.inbox-*.tmp`).
   The reader scans only `inbox/` and skips dotfiles, so staged files are
   never ingested.
2. **fsync the file** before publishing (`flush` + `fsync(fd)`).
3. **Publish atomically** into the inbox via `rename(2)`/`link(2)`
   (`os.replace` or `os.link`). The reader must only ever observe a whole
   note or no note. Never create-then-write in place.
4. **fsync the inbox directory** after publishing, so a crash cannot leave
   a flushed file whose directory entry never landed, or an entry pointing
   at unflushed data.
5. **Collision-proof, sortable names**: microsecond-resolution timestamp +
   writer PID (+ optional entropy/slug), `.md` extension, e.g.
   `20260704T120001123456-7842-a1b2c3d4.md`. On a name collision the
   publish must fail, not clobber.
6. **Write-once.** Never modify or reuse a published note; write a new one.
   The reader may delete a note at any moment after it appears.

## Reader semantics (what memd guarantees)

- `memd sweep` / `memd sync` list the inbox (sorted), read whole files,
  and feed them to the curator distill alongside the session digest.
- Notes are deleted **only after a successful distill apply**. A crash or
  failed distill leaves notes in place to be re-ingested next cycle —
  duplicate ingestion is possible and tolerated (the curator dedupes);
  silent loss is not.
- Non-`.md`/`.txt` files and dotfiles are ignored and never deleted.
- Inbox content passes the same credential-redaction filter as transcripts
  before reaching the model. Do not rely on this: don't put secrets in
  notes.

## Versioning

Breaking changes to writer requirements or reader semantics bump the major
version here, and both memd and the noctalia-claude-plugin reference the
version they implement. v1.0 corresponds to memd ≥ 0.2.0 and
noctalia-claude-plugin ≥ 1.0.0.
