# Project Memory (memd)

This project's durable memory lives in `.memory/`, curated by
[memd](https://github.com/lowcache/memd). It is your long-term memory across
sessions. Follow this contract exactly.

## Automatic ingestion (Gemini / antigravity-cli specific)

memd digests `antigravity-cli` conversation databases
(`~/.gemini/antigravity-cli/conversations/*.db`) automatically on each
sweep, so routine session content reaches memory without action from you.
The inbox (below) is for facts that must not wait for, or might not
survive, transcript distillation.

## 1. Read before working

Read `.memory/state.md`, `decisions.md`, `mistakes.md`, and `todo.md`
before substantive work — or run `memd brief` (`--topic <keyword>`,
`--max-chars N`) for a budgeted digest when context is tight.

- **state.md** — present-tense system truth (ports, paths, services,
  workarounds). Trust it over your assumptions.
- **decisions.md** — binding constraints. Don't violate a decision without
  flagging the conflict to the user first.
- **mistakes.md** — append-only audit log. Never repeat a listed mistake;
  prevention rules are hard requirements.
- **todo.md** — the backlog. Don't re-plan work already tracked here.

Older material lives in `.memory/archive/YYYY-MM.md` — search there for
history.

## 2. Never write memory files directly

`.memory/{state,decisions,mistakes,todo}.md` are curator-owned. Invariants
(frontmatter, append-only mistakes, shrink guard, size budgets) are enforced
in code, and the curator rewrites these files on every distill — direct
edits get clobbered or rejected. Do not edit them, ever.

## 3. Write through the inbox

```bash
memd note -m "one durable fact, decision, or mistake"   # project memory
memd note --global -m "user/system/cross-project fact"  # global memory
```

Or drop a markdown file in `.memory/inbox/` yourself per Inbox Protocol
v1.0 (`INBOX-PROTOCOL.md` in the memd repo): one fact per note, <4000
chars, atomic publish (stage as dotfile temp in `.memory/`, fsync,
rename/link into `inbox/` under a µs-timestamp+PID name), write-once, no
secrets.

**Worth a note:** decisions and their why, mistakes
(symptom / root cause / prevention), state changes, new or completed todos.
**Not:** chatter, anything git already records, transient debugging noise.

## 4. If `.memory/` is missing

Run `memd init`. Do not create the files by hand.
