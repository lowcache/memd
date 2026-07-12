# Agent Instruction Files — Teaching Models to Consume memd Memory

memd curates the `.memory/` store, but the assistants reading your codebase
only benefit if their instruction files tell them the contract: **read the
four memory files before working, never edit them directly, write back
through the inbox**. This directory ships that contract as drop-in
instruction files for the major agent CLIs and IDEs.

All variants carry the same core contract; the per-tool files add
tool-specific notes (e.g. Claude Code's hook-injected brief, Gemini's
automatic antigravity-cli digestion) and use each tool's native location
and format.

## Which file goes where

| File | Install to (project root) | Consumed by |
| :--- | :--- | :--- |
| `AGENTS.md` | `AGENTS.md` | The [agents.md](https://agents.md) open standard: OpenAI Codex, Cursor, Gemini CLI, Zed, Jules, Amp, and most newer agents |
| `CLAUDE.md` | `CLAUDE.md` | Claude Code |
| `GEMINI.md` | `GEMINI.md` | Gemini CLI / antigravity-cli (if not configured to read `AGENTS.md`) |
| `copilot-instructions.md` | `.github/copilot-instructions.md` | GitHub Copilot (chat + coding agent) |
| `memd.mdc` | `.cursor/rules/memd.mdc` | Cursor project rules (if you prefer rules over `AGENTS.md`) |

## Installation

Copy the file(s) for your tools into place:

```bash
cp contrib/agents/AGENTS.md <project>/AGENTS.md
cp contrib/agents/CLAUDE.md <project>/CLAUDE.md
mkdir -p <project>/.github && cp contrib/agents/copilot-instructions.md <project>/.github/
mkdir -p <project>/.cursor/rules && cp contrib/agents/memd.mdc <project>/.cursor/rules/
```

If the project already has an instruction file, **append** the memd section
instead of overwriting — these files are designed to compose with existing
project instructions.

Tools that support file imports can avoid duplication: Claude Code's
`CLAUDE.md` and Gemini's `GEMINI.md` both support `@AGENTS.md`-style
imports, so a one-line pointer at the canonical `AGENTS.md` also works.

## Keeping it lean

These files are loaded into model context every session — that is the
point, and also the cost. The contract is deliberately compact (~60 lines).
If you trim it further, preserve the three load-bearing rules:

1. Read `state.md` / `decisions.md` / `mistakes.md` / `todo.md` (or
   `memd brief`) before substantive work.
2. Never write `.memory/*.md` directly — the curator owns them.
3. Contribute memory only via `.memory/inbox/` (`memd note` or Inbox
   Protocol v1.0).
