This document covers the memd command line as the stable 1.0 contract.

The root `--version` option prints the installed version and exits successfully.
`--help` prints help and exits successfully for the root parser or a command.
All commands read the JSON configuration described in [configuration.md](configuration.md).

## `memd init`

Provision memory and register a project, baselining existing transcripts.

```text
memd init [path] [--name NAME] [--global]
```

`path` defaults to `.`. `--name` defaults to unset; a new registration uses the
path basename when no name is supplied. `--global` defaults to false and uses
the configured global root instead of `path`. Explicit project initialization
also requests agent instruction files and shared ignore configuration; existing
agent files are preserved.

Prints the registered path, with created-file and baselined-transcript counts
when nonzero. Global initialization prints the ready memory path, or
`no global_root configured`. Returns 0 for these outcomes, including a disabled
global root. Returns 2 when the project path is the global root; use `--global`.

## `memd sync`

Distill new transcript content and inbox notes into project memory.

```text
memd sync [--project PATH] [--transcript PATH] [--trigger NAME] [--dry-run]
```

`--project` defaults to `.`. `--transcript` defaults to unset, meaning discover
project sources; when supplied, it selects that single transcript. `--trigger`
defaults to `manual`; sweep, session-end and pre-compact triggers permit small
digests to be deferred. `--dry-run` defaults to false; it prints selection,
size and prompt-preview information without invoking the curator or applying
its output. A deferred dry run prints its deferral; a no-content run is silent.
Dry runs can still scaffold missing memory and record ingestion health.

Normal success, deferral and no-op runs print nothing to stdout. A handled
distill failure prints a log-path diagnostic to stderr. Returns 0 on success,
dry run, deferral or no-op, and 3 on distill failure. Failure preserves backlog
cursors and inbox notes for replay.

## `memd sweep`

Catch up registered projects, discover new projects, and maintain global memory.

```text
memd sweep [--jobs N]
```

`--jobs` defaults to unset, using configured `sweep_jobs` (default 4). A zero
value also uses the configuration; negative effective values become one worker.
Sweep respects the transcript quiet-period gate, processes inbox notes, enforces
idle mistakes-log budgets, and refreshes the generated global project index.

Prints each project's progress and outcome to stderr in completion order.
Returns 0 when no distill or worker failed, and 1 when any did.

## `memd status`

Show the curator backend, project registry and ingestion status.

```text
memd status
```

No command-specific arguments. Ensures the global root is registered, then
prints the backend and models, project paths, last distills, summaries, recent
journal outcomes, available usage totals, over-budget warnings, source and
inbox counts, and any ingestion-stall warning. If the registry is empty it
prints that no projects are registered. Returns 0.

## `memd install-hooks`

Install missing lifecycle hooks in `~/.claude/settings.json`.

```text
memd install-hooks
```

No command-specific arguments. Preserves other settings and recognizes hooks
by their memd event suffix to avoid duplicates. Prints the settings path when
hooks were installed, or `hooks already installed`. Returns 0.

## `memd brief`

Print the session-start memory brief for a path.

```text
memd brief [path] [--max-chars N] [--topic KEYWORD]
```

`path` defaults to `.`; it is used directly after real-path resolution.
`--max-chars` defaults to unset and optionally caps the total brief text;
the normal content budget still applies. The cap sheds the index and trailing
whole content sections, then truncates fixed text if needed. `--topic` defaults
to unset and filters content sections by case-insensitive heading/body match.
With no matching sections, the brief contains only the reference note.

Prints the brief or `(no .memory directory here)`. The print operation adds a
newline outside the capped text. Returns 0.

## `memd recall`

Search memory sections, including archived entries, ranked by relevance.

```text
memd recall QUERY [QUERY ...] [--project PATH] [--limit N] [--no-archive]
```

`query` is required and contains one or more words, joined with spaces.
`--project` defaults to unset, selecting the registered project containing the
current working directory. An explicit path uses its containing registered
project, or that path itself. `--limit` defaults to 5. `--no-archive` defaults
to false and restricts search to active files when supplied.

Prints each hit's file, heading, and body excerpt of at most 600 body characters
plus an omission marker. Prints `no matches` when none exist. Returns 0 for
matches or no matches; returns 2 with a stderr diagnostic when no project
contains the current directory and no explicit project was supplied.

## `memd hook`

Handle a lifecycle event with a JSON payload read from stdin.

```text
memd hook {session-start,session-end,pre-compact}
```

`event` is required and has no default. The payload's `cwd` defaults to the
current directory; `transcript_path` is optional. Stdin that is not a JSON
object (malformed JSON, or any other JSON value) is treated as an empty
payload and processing continues.

Every event ensures the global root. `session-start` resolves the project,
optionally provisions a detected git root, and falls back to global memory.
If a brief exists, stdout contains one JSON object with `hookSpecificOutput`,
`hookEventName` set to `SessionStart`, and the brief in `additionalContext`.
Otherwise stdout is empty.

`session-end` and `pre-compact` launch a detached project sync with the matching
trigger and optional transcript path. They print nothing to stdout. With no
registered project they return without launching sync; the global fallback is
for the start brief only. Detached output goes to the memd log.

Returns 0 on normal dispatch, including a non-object payload handled as
empty. Caught `RuntimeError` or `OSError` is logged and returns 1; this does
not report a later detached child's distill outcome.

## `memd note`

Publish a collision-safe markdown note to a curator inbox.

```text
memd note [--project PATH] [--global] [-m TEXT] [--source LABEL]
```

`--project` defaults to `.`; selects its registered project, git root, or the
path itself. `--global` defaults to false and selects global memory instead.
`-m` / `--message` defaults to unset, reading stdin; supplied text takes
precedence. `--source` defaults to unset and optionally labels the writer.

Prints the written note path. Returns 0 on success; returns 2 with a stderr
diagnostic for an empty stripped note, or a global request with no global root.

## `memd exclude`

Exclude an absolute resolved path from automatic discovery.

```text
memd exclude path
```

`path` is required and has no default. Adds its resolved path to the exclusion
list, removes its registry entry if present, and saves configuration. Prints
`excluded PATH`. Returns 0.

## Exit codes

These are command outcome codes; parser errors and unexpected exceptions apply
separately to every command.

| Command | Codes | Meaning |
| --- | --- | --- |
| `memd init` | `0`, `2` | Success, including disabled global setup; global-root project path requires the global option. |
| `memd sync` | `0`, `3` | Success, dry run, deferred or no-op; distill failure. |
| `memd sweep` | `0`, `1` | No failed distills; at least one failed distill or worker. |
| `memd status` | `0` | Status printed. |
| `memd install-hooks` | `0` | Hooks installed or already present. |
| `memd brief` | `0` | Brief or missing-directory notice printed. |
| `memd recall` | `0`, `2` | Search completed, including no matches; no project and no explicit project option. |
| `memd hook` | `0`, `1` | Normal dispatch; caught RuntimeError/OSError. A non-object stdin payload is treated as empty. |
| `memd note` | `0`, `2` | Note written; empty note or no configured global root for a global note. |
| `memd exclude` | `0` | Exclusion saved. |

Argparse usage errors exit 2 for every command.
An unexpected internal error exits 1 with a traceback.
