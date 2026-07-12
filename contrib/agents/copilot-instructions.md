# Project Memory (memd)

This project's durable memory lives in `.memory/`, curated by
[memd](https://github.com/lowcache/memd). It is your long-term memory across
sessions. Follow this contract exactly.

## Read before working

Read `.memory/state.md`, `decisions.md`, `mistakes.md`, and `todo.md`
before substantive work (or run `memd brief` in the terminal for a budgeted
digest):

- **state.md** — present-tense system truth (ports, paths, services,
  workarounds). Trust it over your assumptions.
- **decisions.md** — binding architecture constraints. Don't violate a
  decision without flagging the conflict to the user first.
- **mistakes.md** — append-only audit log. Never repeat a listed mistake;
  prevention rules are hard requirements.
- **todo.md** — the backlog. Don't re-plan work already tracked here.

Older material lives in `.memory/archive/YYYY-MM.md`.

## Never write memory files directly

`.memory/{state,decisions,mistakes,todo}.md` are curator-owned. Invariants
(frontmatter, append-only mistakes, shrink guard, size budgets) are enforced
in code, and the curator rewrites these files on every distill — direct
edits get clobbered or rejected. Do not edit them, ever.

## Write through the inbox

To record a durable fact, decision, or mistake, run in the terminal:

```bash
memd note -m "one durable fact, decision, or mistake"   # project memory
memd note --global -m "user/system/cross-project fact"  # global memory
```

Or drop a markdown file in `.memory/inbox/` per Inbox Protocol v1.0
(`INBOX-PROTOCOL.md` in the memd repo): one fact per note, <4000 chars,
atomic publish (stage as dotfile temp in `.memory/`, fsync, rename/link
into `inbox/` under a µs-timestamp+PID name), write-once, no secrets.

**Worth a note:** decisions and their why, mistakes
(symptom / root cause / prevention), state changes, new or completed todos.
**Not:** chatter, anything git already records, transient debugging noise.

## If `.memory/` is missing

Run `memd init`. Do not create the files by hand.
