# memd — agent-driven project memory curator

Maintains `./.memory/{state,decisions,mistakes,todo}.md` (plus `archive/` and
`inbox/`) per project, distilled from AI session transcripts by a headless
`claude -p` curator. Designed for multiple projects, multiple CLIs
(claude-code, antigravity, anything that can write a file), and agent swarms.

## Architecture

```
claude-code session ──hooks──▶ memd hook session-end ──▶ detached memd sync
                    └─SessionStart──▶ memd brief (context injection)
systemd user timer (30 min) ──▶ memd sweep ──▶ sync stale projects,
                                               ingest .memory/inbox/,
                                               prune to archive/,
                                               auto-detect + scaffold new repos
memd sync: transcript JSONL ▶ digest ▶ claude -p (haiku, sonnet for big
session-end runs) ▶ validated JSON edits ▶ .memory/ files ▶ git commit
```

Hard invariants enforced by memd itself, not the model: frontmatter
(`type/project/last_updated/status`), append-only `mistakes.md`, a shrink
guard (rejects distills that lose >60% of a file without archiving it),
size budgets with deterministic overflow to `archive/YYYY-MM.md`, per-project
flock (swarm-safe), and cursors that only advance after a successful apply.

## Cross-platform / swarm interface

**antigravity-cli** is read natively: conversations live in
`~/.gemini/antigravity-cli/conversations/*.db` (SQLite, protobuf step
payloads; legacy `*.pb` files are not parsed). memd extracts text as
printable-string runs (step types: 14=user, 33=assistant, 15=tool call,
17=error) and cursors on `steps.idx`. Antigravity records the *launch*
directory as workspace, so conversations are attributed to the registered
project whose root path their payloads mention most (cached in
`~/.local/state/memd/ag_index.json`; unattributed ones are rescanned as
they grow). All digests pass a credential-redaction filter (`ya29.`, `ghp_`,
`sk-`, JWTs, …) since sessions sometimes read secrets.
[NOTE: the filter is now a named-pattern set (`REDACT_PATTERNS`, 14 entries);
add per-instance patterns via the `REDACT_EXTRA_PATTERNS` config list.]

Anything else talks to memd through `./.memory/inbox/`: drop a markdown
note there and the next sweep or sync ingests and deletes it. That is also
how swarm agents hand observations to the curator without write access to
memory files. The writer/reader contract (atomic publish, fsync, naming,
delete-only-after-apply) is specified in
[INBOX-PROTOCOL.md](INBOX-PROTOCOL.md); the noctalia-claude-plugin
`remember` tool is a conforming writer. Extra transcript sources
(claude-format JSONL) can be added per project in
`~/.config/memd/config.json` under `projects.<path>.extra_sources`.

## Commands

| command | purpose |
|---|---|
| `memd init [path]` | scaffold `.memory/` + `.model/` stub, register project |
| `memd sync [--project P] [--trigger T] [--dry-run]` | distill now |
| `memd sweep` | timer entry: catch up everything, prune, detect new projects |
| `memd brief [path]` | print the session-start memory brief |
| `memd status` | registry, backlog bytes, last distill summaries |
| `memd install-hooks` | idempotently wire hooks into `~/.claude/settings.json` |
| `memd exclude <path>` | never auto-manage a path |

[NOTE: table omits `memd note` (collision-safe inbox note writer) and
`memd sweep --jobs N` (parallel sweep; default 4, config `sweep_jobs`).]

State lives in `$XDG_STATE_HOME/memd/` (cursors, locks, log; default
`~/.local/state/memd/`), config in `$XDG_CONFIG_HOME/memd/config.json`
(models, budgets, quiet period, registry).

## Install

memd is a single stdlib-only Python 3.11+ script — `memd.py` on `$PATH`
(as `memd`) is a complete install. With nix:
[CORRECTION: memd is now a stdlib-only package (`memd/`) with `memd.py` as a
thin shim; a bare `memd.py` copy is no longer a complete install — use the nix
package or ship the `memd/` directory alongside the shim.]

```
nix run github:lowcache/memd -- status        # or nix run .# -- status
nix profile install github:lowcache/memd
```

Or declaratively, with the bundled home-manager module (package +
`memd-sweep` systemd user timer):

```nix
imports = [ memd.homeManagerModules.default ];

services.memd = {
  enable = true;
  installClaudeHooks = true;      # optional: wire ~/.claude settings.json hooks idempotently

  # optional configuration for the periodic sweep timer:
  sweep = {
    enable = true;                # run `memd sweep` periodically (default: true)
    interval = "30min";           # interval between runs (default: "30min")
    onBoot = "5min";              # delay before the first sweep after boot (default: "5min")
    randomizedDelay = "2min";     # randomized delay jitter (default: "2min")
  };
};
```

## Tests

`python -m pytest tests/ -q` (stdlib + pytest, no network, never touches
real user state or a real `claude`). `nix flake check` runs the suite plus
a package smoke check.

## Claude-code independence

claude-code is one transcript source and one trigger, not a dependency:
every command runs standalone, the sweep timer needs no session at all,
antigravity is read natively, and anything else feeds `.memory/inbox/`.
The single hard coupling is the distillation backend, which defaults to
headless `claude -p`. To re-point it, set `curator_cmd` in config.json —
an argv list receiving the prompt on stdin, with `{model}` substituted;
any CLI whose output contains one JSON object (fences/prose tolerated)
works. If the backend is missing, distills fail loudly but lose nothing:
cursors only advance after a successful apply, so backlog replays once a
working backend is configured.
