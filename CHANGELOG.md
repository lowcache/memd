# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- contrib/curators/ with the curator backend contract and wrapper scripts for
  running memd against a non-Claude model: ollama (verified) and the gemini
  CLI (unverified, see its header)
- GitHub Actions CI: pytest on Python 3.10 through 3.14, build plus twine
  check, shellcheck on the contrib wrappers, and nix flake check
- Python 3.14 declared supported; the suite passes on it

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
