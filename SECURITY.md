# Security Policy

memd distills AI coding-session transcripts into four markdown files under a
project's `.memory/`, using an LLM ("the curator"). Those files are then fed
back into future sessions as starting context. This document outlines the
security policy, data flow, trust model, and residual risks of using memd.

## Reporting a Vulnerability

Report security vulnerabilities using GitHub Security Advisories at
https://github.com/lowcache/memd under the "Security" tab by selecting "Report a
vulnerability". If advisories are unavailable, report via a private GitHub
issue.

There are no guaranteed response times. memd is a single-maintainer hobby
project.

## What memd sends, and where

With the default backend, the contents of your coding sessions are sent to a
third-party LLM API. You can configure a local backend to keep all data on your
machine.

memd processes data through the following steps:

1. memd reads session transcripts from the local machine, including
   `claude-code` JSONL session logs under `~/.claude/projects/` and
   `antigravity-cli` SQLite conversation databases. You can register extra
   plain-text or JSONL sources per project via the `extra_sources` config key.
2. It reads notes placed in `.memory/inbox/` (per project) and in the global
   inbox (default `~/.memory/inbox/`).
3. That material is concatenated into a digest, capped at `digest_cap_chars`
   (default 60000 characters).
4. The digest passes through credential redaction.
5. The redacted digest, along with the current contents of the four memory
   files, is sent as a prompt to the configured curator backend. By default,
   this is the `claude` CLI, which sends the prompt to Anthropic's API. If
   `curator_cmd` is set, the prompt goes wherever that command sends it. This
   includes nowhere off-machine if you use a local model such as ollama.
6. The curator's response is validated against built-in guardrails and written
   to the four memory files, which are then git-committed.
7. At the start of a later session, `memd brief` composes a summary from those
   files and injects it into the assistant's context.

To keep everything on the machine, set `curator_cmd` to a local backend. See
`contrib/curators/` for configuration examples.

## Credential redaction

Before anything reaches the curator, memd scrubs the digest and inbox notes
using 13 built-in regex patterns. These patterns cover:

- Google OAuth tokens (`ya29.`)
- GitHub PATs (classic and fine-grained)
- Anthropic keys (`sk-ant-`)
- OpenAI keys (`sk-`/`sk-proj-`)
- AWS access key IDs (`AKIA`)
- Slack tokens (`xox`)
- GitLab PATs (`glpat-`)
- npm tokens (`npm_`)
- JWTs (`eyJ`)
- JSON token fields
- Bearer authorization headers
- Uppercase env-style credential assignments
- PEM private-key blocks

You can add custom regex patterns using `REDACT_EXTRA_PATTERNS`.

Regex redaction is best-effort pattern matching. It catches credentials with
recognisable shapes, but it is not a guarantee. It cannot catch a secret with no
distinctive format, such as a bare password, a hex string, or a cloud key
without a prefix (for example, Azure keys).

## Trust model

memd is supported on POSIX systems only, relying on `flock`-based locking, with
Linux as the tested platform. It requires `git` and Python 3.10+.

memd does not trust the curator model. Guardrails are enforced in Python rather
than by prompting:

- Every memory file must carry valid YAML frontmatter or the write is rejected.
- `mistakes.md` is append-only; the curator may add entries, but never edit or
  delete them.
- A distill that removes more than 60% of a file is rejected unless the removed
  sections were moved to the archive.
- Per-file size budgets are enforced, with overflow moved to
  `archive/YYYY-MM.md`.
- Concurrency is restricted via per-project `flock` locking.
- Read cursors advance only after a successful write, so a failed distill is
  retried rather than skipped.

These guardrails protect the structure and integrity of the memory store. They
validate structure, not the meaning of the text. The memory files' bodies remain
free-form prose written by the curator.

## Prompt injection and persistent context

Because the memory files' bodies are free-form prose written by the curator,
content you work on can reach the curator. If a repository, a web page an agent
fetched, or a pasted document contains text crafted to influence a model, that
text can appear in the transcript and therefore in the digest.

A curator that is successfully influenced can write attacker-chosen prose into
`state.md`, `decisions.md`, or `todo.md`. Because those files are injected into
later sessions' context, this influence can persist across sessions rather than
ending with the session in which it arrived.

This risk is inherent to any tool that summarises untrusted input into durable
context. memd's guardrails bound the damage by preserving structure, enforcing
an immutable `mistakes.md`, capping file sizes, and keeping all output as
git-versioned, human-readable markdown, but they do not eliminate the risk.

memd applies one mitigation itself: the brief injected at session start labels
the memory it carries as reference notes distilled from earlier sessions, and
states that a directive appearing in them describes what was previously decided
rather than authorising an action. This makes a model less likely to act on an
imperative that reached a memory file. It is defence in depth, not a fix.

You can apply the following mitigations:

- Read the memory files and inspect them with `git log`, as they are plain
  markdown under git.
- Rely on `.memory/` having its own git history, making any bad distill
  inspectable and revertable.
- Treat memory files as project notes rather than instructions to obey.
- Use `memd exclude <path>` for repositories you do not trust.
- Set `"auto_scaffold": false` in your configuration to require explicit opt-in
  per project.

## What memd does not do

- memd makes no network calls of its own. The only network egress is from the
  curator backend it shells out to.
- It includes no telemetry, no analytics, and no phone-home functionality.
- It does not read files in your project other than those under `.memory/`. Its
  only inputs are session transcripts, inbox notes, and configured
  `extra_sources`.
- It never executes commands from a transcript or from curator output.

## Supported versions

| Version | Supported | Notes                                                 |
| ------- | --------- | ----------------------------------------------------- |
| 0.3.x   | Yes       | 0.3.1+ recommended; 0.3.0 has a git commit target bug |
| < 0.3.0 | No        | Unsupported                                           |

In version 0.3.0 and earlier, automatically discovered projects had their memory
committed into the project's own git history rather than the `.memory/`
repository. This was fixed in version 0.3.1. If you use version 0.3.0, you
should upgrade to 0.3.1 or later.
