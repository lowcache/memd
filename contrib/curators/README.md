# memd Curator Backends

## 1. Why

memd's default backend is the `claude` CLI, but the `curator_cmd` configuration
key allows any LLM CLI to serve as the distillation engine. The contract
between memd and a curator backend is simple: memd writes the prompt to standard
input and expects a single JSON object on standard output.

## 2. The Contract

1. If `curator_cmd` is non-empty, memd substitutes the literal token `{model}`
   in every argument with the configured model name (`model_small` or
   `model_large`), then executes that command line.
2. The full prompt is written to the child process's standard input as text.
3. memd waits up to 600 seconds, then reads standard output.
4. A non-zero exit code is treated as a failure and retried twice (with 5s
   and 15s backoff intervals).
5. Standard output is parsed in order:
   - If output parses as JSON and is an object with a string `result` key,
     the value of `result` becomes the text.
   - Else if output parses as JSON and is an object, that object is used
     directly as the curator result.
   - Else the raw output is treated as text, leading/trailing markdown code
     fences are stripped, the first `{` is located, and one JSON object is
     decoded from there. Trailing prose after the JSON object is tolerated.
6. Therefore, a valid backend is any command that reads a prompt on standard
   input and writes a single JSON object to standard output.

## 3. ollama (local models)

Status: VERIFIED on 2026-07-30 — ran ollama run llama3.1:latest --format json with stdin prompt and verified clean JSON object on stdout

Configuration snippet (`~/.config/memd/config.json`):

```json
{
  "curator_cmd": [
    "/absolute/path/to/contrib/curators/ollama-curator.sh",
    "{model}"
  ],
  "model_small": "llama3.1:latest",
  "model_large": "llama3.1:latest"
}
```

Use a model name exactly as `ollama list` reports it, including the tag.

Observed behavior and caveats:
- `ollama run` emits ANSI spinner characters on standard error even when
  standard output is piped or not a terminal. The wrapper buffers standard
  error and replays it, with the escape sequences stripped, only when the call
  fails — so spinners stay out of the way but a real error is still readable in
  the failure memd reports.
- `ollama run` silently downloads a model that is not present locally. Since
  memd calls the backend from an unattended sweep, the wrapper refuses to run a
  model that `ollama list` does not show, rather than letting a typo in
  `model_small` start a multi-gigabyte pull. Pull models yourself with
  `ollama pull <model>`.
- When invoked without `--format json`, the tested model (`llama3.1:latest`)
  wrapped its JSON response in markdown code fences (` ```json ... ``` `).
  memd tolerates fences, but `--format json` (which the wrapper passes) forces
  clean JSON without fences or surrounding prose.
- Smaller local models sometimes emit near-JSON — an invalid `\` escape inside
  a string, or output truncated mid-object. memd treats that as a retryable
  failure and re-samples with backoff; it is not fatal, but a model that does
  it often will burn wall-clock time on every distill.

## 4. gemini CLI

Status: UNVERIFIED — requires interactive authentication which failed in headless mode with error: "Error authenticating: IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, please migrate to the Antigravity suite of products: https://antigravity.google"

Configuration snippet (`~/.config/memd/config.json`) (untested):

```json
{
  "curator_cmd": [
    "/absolute/path/to/contrib/curators/gemini-curator.sh",
    "{model}"
  ],
  "model_small": "gemini-2.5-flash",
  "model_large": "gemini-2.5-pro"
}
```

Observed behavior and caveats:
- `gemini --help` indicates that non-interactive (headless) mode is enabled via
  the `-p/--prompt` flag, and that any piped standard input is appended to the
  prompt string passed to `-p`.
- In headless testing (`gemini -p "" -m <model>`), execution failed during
  authentication with an unsupported client tier error. Valid authentication
  and a supported account tier are required.

## 5. Writing Your Own

A minimal shell script backend must satisfy the following requirements:
- Be executable (`chmod +x`) and use a POSIX shell (`#!/bin/sh`).
- Accept the model name as the first argument (`$1`); exit non-zero (e.g., 2)
  if `$1` is empty or missing.
- Read the full prompt from standard input and pass it to the underlying LLM
  CLI.
- Emit exactly one valid JSON object to standard output.
- Exit 0 on success, and non-zero on failure so that memd's retry mechanism
  can trigger.

One-line smoke test to verify a custom backend before wiring it into memd:

```sh
printf 'Output exactly one JSON object, no markdown fences, no prose around it, with\nkeys: summary (string), state_body (string or null), decisions_body (string or\nnull), todo_body (string or null), mistakes_new_entries (array), archive_entries\n(array). The project state is: the API moved from port 8080 to port 9090.\n' | /path/to/your-curator.sh YOUR_MODEL_NAME
```

## 6. Model-Size Guidance

memd sends prompts that can approach `digest_cap_chars` (default 60,000
characters) plus the current memory files.

- A 60,000-character input transcript plus existing memory files corresponds
  roughly to 15,000 to 20,000 tokens of input context.
- Any model configured as `model_small` or `model_large` should support an
  effective context window of at least 32,000 tokens (and ideally 64,000+
  tokens) to process full session distillations without truncation or context
  limit errors.
- Models with smaller context limits (such as 4,096 or 8,192 tokens) are not
  suitable for summarizing long sessions.

Capability matters as much as context length. The curator has to return one
well-formed JSON object with the exact keys memd asks for, and hold to the
pruning rules in the charter. An 8B-class local model does this unreliably: in
testing, `llama3.1` (8B) needed several minutes per distill on a full-size
prompt and produced invalid JSON often enough to trigger memd's retries. It
works, but treat small local models as the economy option, not the default. If
distills are slow or keep failing, check the log before assuming memd is at
fault — the cause is usually the model, and the backlog is preserved either way.
