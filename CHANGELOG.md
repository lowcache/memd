# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.2] - 2026-08-26

Hardening pass over the 0.4.0 section-op path: the guards that protected the
full-body protocol did not follow the ops that replaced it. Every fix below is
against behaviour 0.4.0 shipped, and each carries a regression test.

### Fixed

- The session-start brief no longer dies on a project whose distills have only
  failed. 0.4.0's journal writes a meta entry for a failed distill, and that
  entry has no `last_sync`; the brief read the key directly, raising KeyError —
  which the hook's RuntimeError/OSError guard does not catch, so a first
  distill that failed (a missing curator backend, say) left every later session
  in that project starting with no memory and a traceback
- The shrink guard covers section ops, not just `set_body`. `delete_section` on
  every heading reduced a file to its title while each op looked harmless
  alone, and sync recorded the result as a successful distill. The guard now
  runs against what the ops actually produce, so it applies to both protocols
- One invented heading no longer discards a whole distill. `replace_section` or
  `delete_section` naming a heading that does not exist is dropped on its own
  and reported; sibling ops apply as normal. Previously it raised, failing the
  distill, holding the cursors, and replaying the identical digest into the
  identical model on the next sweep — a curator that hallucinated one heading
  reliably could stall a project indefinitely. Skips are logged and counted in
  the distill journal, so the pattern is visible across passes
- Ingestion health is decided by transcript parsing alone. Recording it only
  when there was also no inbox note meant a single note from the user vouched
  for sources that had yielded nothing, hiding the stall in exactly the
  projects someone was actively working in
- The stall byte count no longer sums antigravity step indices with file byte
  offsets, a number that meant nothing against a byte threshold

### Changed

- The text fallback requires text: a source that decodes mostly to replacement
  characters and control bytes (a database, an archive, an image caught by an
  `extra_sources` glob) is skipped with a log line instead of being shipped to
  the curator as mojibake and marked as learned
- An arbitrary log is read at most 4 MB per pass rather than whole; the
  remainder rides the next sweep
- Tool-result placeholders read `R: [18.2k ch]` past a thousand characters, as
  the 0.4.0 notes described; the code was emitting the raw count
- The charter's output schema no longer lists `mistakes.md` as an op target.
  It contradicted the rule three lines above it and invited the exact op the
  validator then rejected outright
- The release script takes its PyPI token from `TWINE_API_KEY`, or from the
  file named by `TWINE_API_KEY_FILE` (default `/run/secrets/twine_api_key`),
  and maps it onto the `__token__` username twine expects of an API token.
  twine reads only `TWINE_USERNAME`/`TWINE_PASSWORD`, so a token supplied under
  any other name — or, as secret managers deliver it, as a file — reached the
  upload step as no credential at all and fell through to an interactive
  prompt: under `--yes`, a release that fails at its last, irreversible step

## [0.4.0] - 2026-08-26

Token economics, timeline honesty, and tool independence: distills cost
markedly less on both the input and output side, submissions carry the
context the curator previously had to guess, and the digest pipeline accepts
transcripts from any CLI, not just claude-code.

### Added

- Timestamped session spans: every digested span carries a header with source
  tool, session id, UTC start/end, and cwd; spans are sorted chronologically
  across all sources before curation. The charter demanded absolute dates the
  digest never contained — now it has them, and multi-agent threads merge in
  real order instead of filename order
- Gap-aware budget triage: when a digest exceeds `digest_cap_chars`, whole
  low-signal spans are dropped (user-intent-weighted scoring) and named in an
  explicit omission marker, replacing cap_digest's silent middle elision that
  erased whatever work happened to sit mid-timeline. Head/tail elision remains
  only as the last resort for a single oversize span
- Section-patch curator protocol: the model returns targeted section ops
  (`replace_section`/`append_section`/`delete_section`, `set_body` as last
  resort) instead of re-emitting full file bodies — output tokens drop by an
  order of magnitude on mature memories, git diffs become surgical, and
  legacy full-body outputs are still accepted and converted
- Distill journal: meta.json keeps a rolling per-project history (trigger,
  model, digest size, outcome, summary) surfaced in `memd status` and fed to
  the curator as PREVIOUS DISTILLS, so failed-distill gaps are visible and the
  do-not-reapply rule finally has evidence instead of relying on model faith
- Universal ingestion adapter: non-claude, non-antigravity transcript sources
  are content-sniffed and best-effort digested as text with heuristic role
  tagging, so any CLI's logs ride `extra_sources` — unknown formats now work
  instead of stalling. The ingest-stall signal survives for what it can still
  mean: claude-shaped JSONL that stops yielding lines
- Subagent capture: sidechain entries ride along as indented `SUB>` lines
  (config `capture_sidechains`, default on). Delegated work no longer
  vanishes from memory between "task delegated" and its outcome
- Inbox provenance: notes without frontmatter get `date:`/`source:` normalized
  onto them at write time, and the reader lifts both into the curator label;
  frontmatter does not count against the note size budget
- Self-sufficient brief: session-start briefs end with a compact per-file
  index (last_updated, size, headings) and the protocol text now asks agents
  to open memory files selectively rather than blanket re-reading all four
  files whose contents the brief largely embeds

### Changed

- Tool play-by-play is compressed in digests: results become size-aware
  placeholders (`R: [18.2k ch] first chars…`) and tool calls keep only
  salient arguments, so file-heavy sessions stop drowning user intent and
  forcing early budget truncation
- mistakes.md is sent to the curator as heading list + newest entry (capped)
  instead of the full 22K-char append-only log it may only append to anyway

### Fixed

- `set_body` ops targeting mistakes.md are ignored at apply time: the audit
  log stays append-only through `mistakes_new_entries` regardless of what the
  model returns

## [0.3.3] - 2026-07-30

### Changed

- `model_small` defaults to `sonnet` rather than `haiku`, so both tiers ship
  the same capable model. Curation quality is the product, and someone trying
  memd for the first time cannot distinguish a weak default model from a weak
  tool — they stop using it without ever finding the config key. Set
  `model_small` to `haiku` to trade quality back for cost
- README badges are live rather than hardcoded: CI status from GitHub Actions,
  and version plus supported Python versions from PyPI. A hand-written version
  badge silently goes stale, and needed a release gate to compensate

### Fixed

- Digests escalate to `model_large` based on size alone. Escalation was gated on
  the trigger being session-end or manual, so a sweep never escalated — and a
  sweep carries the largest backlogs, which is where a small model does worst.
  Invisible while both tiers share a model; wrong for anyone who tiers them
- The release script reported "tag is free on origin" when the remote could not
  be reached at all, since any non-zero `git ls-remote` exit was read as
  absence. An unreachable remote now warns instead of passing a gate whose
  purpose is to prevent a duplicate release

### Added

- `memd status` reports stalled ingestion: session content read from which
  nothing usable was extracted, with the volume and how long it has been the
  case. memd parses transcript formats owned by other tools, and parsing is
  forgiving by design, so a format change does not raise — it just stops memd
  learning while it still looks healthy. That condition is now visible instead
  of silent. Read cursors still only advance after a successful distill, so the
  backlog replays once parsing works

## [0.3.2] - 2026-07-30

### Added

- SECURITY.md: data flow, credential-redaction limits, trust model, and the
  prompt-injection/persistent-context threat model with mitigations
- `memd status` prints the configured curator backend and models, so where
  session content is sent is visible in the tool rather than only in the docs

### Changed

- Automatic discovery no longer writes anything into the project root. It
  creates the .memory store and registers the project; instruction files and
  the tracked .gitignore entry are now written only by an explicit `memd init`.
  scaffold() creates the memory store only, and write_agent_files() is separate
- `memd init` writes AGENTS.md always, and CLAUDE.md or GEMINI.md only when
  that tool is detected, instead of all three unconditionally
- `memd install-hooks` writes the resolved absolute path to the memd
  executable rather than a bare `memd`, so hooks work for venv and pipx
  installs. Existing bare-command hooks are recognised and not duplicated
- The session brief labels memory as reference notes distilled from earlier
  sessions rather than instructions, as defence in depth against a directive
  reaching a memory file and persisting across sessions

### Fixed

- An explicit `memd init` after automatic discovery now records the ignore rule
  in the shared .gitignore instead of returning early because .memory already
  had its own repo

## [0.3.1] - 2026-07-30

### Added

- contrib/curators/ with the curator backend contract and wrapper scripts for
  running memd against a non-Claude model: ollama (verified) and the gemini
  CLI (unverified, see its header)
- GitHub Actions CI: pytest on Python 3.10 through 3.14, build plus twine
  check, shellcheck on the contrib wrappers, and nix flake check
- Python 3.14 declared supported; the suite passes on it

### Changed

- Scaffolding writes agent instruction files to the project root as AGENTS.md,
  CLAUDE.md, and GEMINI.md, replacing the single Claude-specific stub that went
  into a .model/ directory. An existing file of any of those names is never
  overwritten. A scaffolded project no longer presumes a particular assistant
- Automatic discovery records the .memory/ ignore rule in the clone-local
  .git/info/exclude instead of the tracked .gitignore, so memd does not modify
  a tracked file in a repo the user never asked it to touch. An explicit
  `memd init` still writes .gitignore, where the rule is shared with
  collaborators

### Fixed

- Auto-discovered projects (claude-code session-start hook, sweep detection)
  now get their own standalone .memory repository and parent .gitignore entry,
  the same as memd init. Previously only memd init provisioned the memory
  repo, so auto-scaffolded projects had their memory committed into the
  project's own git history
- Malformed curator output no longer crashes memd. A JSONDecodeError from the
  final decode escaped uncaught and surfaced as a traceback; it is now a
  retryable CuratorError, which matters for smaller models that emit near-JSON
- memd sync now reports distill failures on stderr with a pointer to the log,
  instead of exiting 3 silently

### Changed

- Curator output is decoded with strict=False, accepting raw control characters
  inside strings. An unescaped newline is the most common way a local model
  breaks its JSON, and memd already tolerated fences and surrounding prose
- The shipped systemd unit uses %h/.local/bin/memd, which pip --user and pipx
  installs need; systemd's search path excludes ~/.local/bin

## [0.3.0] - 2026-07-23

### Added

- Decoupled memory history into standalone per-project .memory git
  repositories via memory_own_repo config (default enabled)
- Shared single physical .memory store across linked git worktrees via
  symlink to prevent divergence
- Migration helper that untracks .memory from the parent repository and adds
  it to .gitignore on memd init
- Budgeted memd brief command with --max-chars and --topic flags plus
  brief_chars (default 2500) and brief_decisions_days (default 30) config keys
- pip packaging via pyproject.toml with setuptools>=77, dynamic versioning,
  and full PyPI metadata, published as memd
- Standalone systemd user service and timer under contrib/ for non-Nix
  installations
- Drop-in agent instruction files under contrib/agents/ for six tools:
  AGENTS.md, CLAUDE.md, GEMINI.md, copilot-instructions.md, memd.mdc, and a
  placement guide
- Inbox Protocol v1.0 documentation in INBOX-PROTOCOL.md

### Changed

- Split former single-file implementation into an 11-module memd/ package
  with a thin executable shim while preserving unchanged behaviour
- Unified error handling through memd/errors.py (MemdError -> ConfigError,
  DigestError, CuratorError)
- Parallelized project sweeps via a thread pool with per-project file
  locking controlled by sweep_jobs config (default 4) and --jobs override
- Defined exit-code contract where memd sync returns 3 on curator or distill
  failure and memd sweep returns 1 if any project failed
- Configured log file rotation at 1 MB while keeping 3 backup files
- Expanded test suite to 166 tests verified on Python 3.10 through 3.13

### Fixed

- Prevented worktree symlink from replacing a pre-existing .memory directory
  holding unsaved work via safety guard
- Fixed global inbox concurrency with multi-writer atomic publish using temp
  files, fsync, and link/replace plus reader deletion only after apply
- Hardened curation quality with shrink guard, frontmatter validation,
  duplicate mistakes-entry suppression, and skeleton output no-change handling

### Security

- Hardened credential redaction with 13 built-in patterns scrubbing
  transcripts and inbox notes before reaching curator or memory files plus
  REDACT_EXTRA_PATTERNS config key for custom regexes
- Purged tracked .memory/ and .model/ directories from git repository
  history with git filter-repo on 2026-07-23 while confirming published PyPI
  artifacts were never affected and contain only memd/ sources and tests

## [0.2.0]

### Added

- Released initial public agent-driven project memory curator featuring four
  curated memory files (state.md, decisions.md, mistakes.md, todo.md) per
  project, headless LLM curator invocation, claude-code lifecycle hooks,
  antigravity-cli SQLite conversation parsing, a curator inbox, size budgets
  with overflow to archive/YYYY-MM.md, and a Nix flake with home-manager module
