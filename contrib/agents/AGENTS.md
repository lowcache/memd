# Project Memory (memd)

This project's durable memory lives in `.memory/`, curated by
[memd](https://github.com/lowcache/memd). It is your long-term memory across
sessions: what past agents learned, decided, and got wrong. Follow this
contract exactly.

## 1. Read before working

Read these four files before any substantive work:

| File | Contents | How to treat it |
| :--- | :--- | :--- |
| `.memory/state.md` | Current system state: ports, paths, services, active workarounds | Present-tense truth. Trust it over your assumptions; it supersedes stale knowledge from your training or earlier sessions. |
| `.memory/decisions.md` | Active architecture decisions and the constraints they impose | Binding. Do not violate a decision without flagging the conflict to the user first. |
| `.memory/mistakes.md` | Append-only log of past mistakes: symptom, root cause, prevention rule | Check before acting. Never repeat a listed mistake; the prevention rules are hard requirements. |
| `.memory/todo.md` | Open tasks, roadmap, verification loops | The backlog. Don't re-plan or re-litigate work already tracked here. |

Token economy: `memd brief` prints a budgeted digest (~2500 chars) of all
four; `memd brief --topic <keyword>` filters to relevant sections,
`--max-chars N` caps the size. Prefer the brief when context is tight; read
the full files when the work is substantial.

History that outgrew the active files is archived in
`.memory/archive/YYYY-MM.md` — search there when you need past context.

## 2. Never write memory files directly

`.memory/{state,decisions,mistakes,todo}.md` are owned by the memd curator.
Their invariants (YAML frontmatter, append-only `mistakes.md`, shrink guard,
size budgets) are enforced in code, and the curator rewrites the files on
every distill — direct edits race the curator and get clobbered or rejected.
Do not edit, append to, reformat, or "fix" these files, ever.

## 3. Write through the inbox

To record something durable, drop a note in the inbox; the next
`memd sweep`/`memd sync` distills it into the memory files.

Easiest — the CLI handles atomicity for you:

```bash
memd note -m "one durable fact, decision, or mistake"   # project memory
memd note --global -m "user/system/cross-project fact"  # global memory
```

Or write the file yourself per **Inbox Protocol v1.0** (full spec:
`INBOX-PROTOCOL.md` in the memd repo):

- Target `.memory/inbox/` (project) or `~/.memory/inbox/` (global).
- One fact per note, under 4000 chars, markdown, optional YAML frontmatter
  (`date`, `source`, `topic`).
- Publish atomically: stage as a dotfile temp in `.memory/` (not inside
  `inbox/`), fsync, then rename/link into `inbox/` under a
  µs-timestamp+PID name (e.g. `20260711T120001123456-9876.md`). Never
  modify a note after publishing.
- Never put secrets in notes. Redaction exists downstream; do not rely
  on it.

**Worth a note:** decisions made and why, mistakes hit
(symptom / root cause / prevention), state changes (ports, services,
paths, workarounds), completed or newly discovered todos.
**Not worth a note:** conversational chatter, anything the repo/git history
already records, transient debugging noise.

## 4. If `.memory/` is missing

Run `memd init` to scaffold and register the project. Do not create the
files by hand.
