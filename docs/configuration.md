This document covers memd configuration as the stable 1.0 contract.

## Location and loading

Configuration is JSON at `$XDG_CONFIG_HOME/memd/config.json`, defaulting to
`~/.config/memd/config.json` when `XDG_CONFIG_HOME` is unset or empty. Missing
keys take defaults. An unreadable or invalid JSON file falls back to defaults.
`budgets` is merged per file: a partial object keeps the other default budgets.
Other supplied top-level values replace their defaults.

## Keys and defaults

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `claude_bin` | string | `"claude"` | Default headless curator executable. If lookup fails, memd also checks `~/.local/bin/claude`. |
| `curator_cmd` | array of strings | `[]` | Override backend argv. Each argument substitutes `{model}` with the selected model. Receives the charter and prompt on stdin. Empty uses `claude_bin`. |
| `antigravity_dir` | string | `"~/.gemini/antigravity-cli"` | Base directory for Antigravity conversation databases under `conversations/`; user-home notation is expanded. |
| `model_small` | string | `"sonnet"` | Model for digests at or below `escalate_chars`. |
| `model_large` | string | `"sonnet"` | Model for digests above `escalate_chars`, for every trigger. |
| `escalate_chars` | integer | `15000` | Character threshold applied to the processed transcript digest, excluding inbox notes. |
| `digest_cap_chars` | integer | `60000` | Digest size target before repeat collapsing and redaction. Drops whole low-signal spans; oversized joined text gets head/tail elision. The truncation marker adds characters beyond this target. |
| `min_digest_chars` | integer | `3000` | Defer smaller digests for sweep, session-end and pre-compact when no inbox notes exist and the age limit has not expired. Zero disables size-based deferral. |
| `max_defer_hours` | integer | `24` | Distill a small digest when its oldest pending span reaches this age. Missing or unusable timestamps prevent deferral. |
| `curator_view_chars` | integer | `20000` | Combined state, decisions and todo body threshold for partial views. Also budgets selected headings and section bodies; the first candidate and archive candidates can exceed it. |
| `capture_sidechains` | boolean | `true` | Include transcript sidechain turns as indented `SUB>` lines. |
| `quiet_seconds` | integer | `600` | Sweep attempts sync when at least one pending transcript is older than this cooldown, or inbox notes exist. Once triggered, sync reads all pending sources, including newer ones. |
| `sweep_jobs` | integer | `4` | Sweep worker threads. The CLI jobs option overrides this; the effective count is at least one. |
| `auto_scaffold` | boolean | `true` | Allow session-start and sweep discovery to provision and register detected git projects. |
| `git_commit` | boolean | `true` | Commit memory after successful distillation and the sweep's global project-index refresh; also enables repository setup when provisioning. |
| `memory_own_repo` | boolean | `true` | With `git_commit`, provision projects with a standalone `.memory` repository, migrate parent tracking, and share the main worktree's store by symlink when safe. Existing unsaved memory prevents replacement. Global repository setup depends on `git_commit` alone. |
| `budgets` | object | see below | Active memory body character budgets, with per-file defaults retained when omitted. |
| `state.md` | integer | `10000` | Curator-managed state budget. |
| `decisions.md` | integer | `12000` | Curator-managed decisions budget. |
| `todo.md` | integer | `10000` | Curator-managed tasks budget. |
| `mistakes.md` | integer | `22000` | Deterministic audit-log overflow budget. |
| `REDACT_EXTRA_PATTERNS` | array of strings | `[]` | Extra regular expressions redacted from digests and inbox notes. Loaded lazily once per process; invalid regexes are logged and skipped. |
| `exclude` | array of strings | `[]` | Absolute paths excluded from automatic project discovery. Does not disable an explicit sync. |
| `projects` | object | `{}` | Registry mapping project paths to names and extra source globs. Global entries also carry a managed `global` flag. |
| `global_root` | string | `"~"` | Global fallback root; its memory lives in `.memory/`. Defaults to the expanded home directory. An empty value disables global setup. |
| `global_brief_chars` | integer | `800` | Global state excerpt cap in a project brief. Zero omits the excerpt but keeps the pointer and global todos; negative omits the whole global slice. A topic filter also omits the excerpt. |
| `brief_chars` | integer | `2500` | Budget for content sections: open todos, recent decisions, state sections, then the newest mistake summary. Keep whole sections in that priority order; the first section that cannot fit ends selection. Fixed notes, metadata, global slice and memory index are outside this budget. |
| `brief_decisions_days` | integer | `30` | Decision-section recency window for briefs, using the newest ISO date in the heading or body. Undated sections remain eligible and sort after dated sections. |

The `~` defaults above represent expanded home paths. `global_root` is passed
through real-path resolution, without explicit user-home expansion; use an
absolute path when overriding it in JSON.

## Budget behavior

For `state.md`, `decisions.md` and `todo.md`, memd never chooses and archives
sections itself. The curator prompt receives an `OVER BUDGET` notice and up to
three oldest dated section headings as archive candidates. Partial views include
those candidates in full. The curator must supply archive entries and edits;
`memd status` flags these over-budget files. A budget is not a hard write limit.

For `mistakes.md`, successful sync and idle sweep upkeep move the oldest `### `
sections from the beginning of the log into `.memory/archive/YYYY-MM.md` until
the remaining body fits or no entries remain. The preamble is retained. If the
preamble alone exceeds the budget, enforcement cannot reduce it. Overflow
archiving uses the reason `size budget overflow`; new entries remain append-only
from the curator's perspective.

## Per-project settings

`projects.<path>.name` names the project in prompts and memory metadata.

`projects.<path>.aliases` lists other paths that reach the same `.memory` store,
such as bind mounts and linked worktrees. memd manages aliases: entries sharing a
store merge when the config loads, with the shortest path becoming the entry;
registering another path to that store adds an alias. Sessions and transcripts
under any alias belong to the project, with one lock per store.

`projects.<path>.extra_sources` is an array of transcript file globs. Globs expand
`~`; relative globs resolve against the process working directory, so absolute
globs avoid ambiguity. They supplement automatic sources for normal projects.
Global entries use only these explicit sources and inbox notes.

## Example config.json

This example selects the bundled local Ollama wrapper. Replace its absolute path
and model names with the installed wrapper and available local models.

```json
{
  "curator_cmd": ["/absolute/path/to/memd/contrib/curators/ollama-curator.sh", "{model}"],
  "model_small": "llama3.1:latest",
  "model_large": "llama3.1:latest",
  "budgets": {"state.md": 12000}
}
```
