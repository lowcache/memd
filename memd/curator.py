"""The distill brain: curator charter, prompt assembly, LLM invocation, and
output validation."""

import json
import os
import re
import shutil
import subprocess
import time

from memd.config import CONFIG_PATH, HOME, STATE_DIR, log, today
from memd.errors import ConfigError, CuratorError
from memd.memory import SKELETONS, split_frontmatter

# --------------------------------------------------------------------------
# curator charter — the system contract for the distillation model
# --------------------------------------------------------------------------

CHARTER = """\
You are a project-memory curator. You receive (1) the current contents of a
project's .memory/ files and (2) a digest of recent AI work sessions on that
project (possibly from several agents, CLIs, or a swarm). You produce updated
memory files. You are not a participant in the project; you are its archivist.

FILE PURPOSES
- state.md: single source of truth for CURRENT live status: configs, directory
  maps, services, ports, active workarounds, hardware facts. Present tense.
  Replace stale facts; never accumulate history here.
- decisions.md: active canonical architecture decisions and preferences.
  Each entry: what was decided, why, and what it rules out. Only decisions
  that constrain future work. Superseded decisions move to archive.
- mistakes.md: append-only audit log of configuration/implementation mistakes:
  symptom, root cause, exact prevention rule. You may only ADD entries.
- todo.md: open tasks, roadmap, pending verification loops. Completed or
  abandoned items move to archive, they do not linger.

SIGNAL VS NOISE — keep only what a FUTURE session needs:
KEEP: decisions made and their rationale; state changes (services, files,
ports, versions, paths); discovered constraints and gotchas; root causes of
failures and their fixes; open threads and explicitly deferred work; exact
commands/flags that were hard to derive; scope agreements with the user.
DISCARD: conversational chatter, tangents, abandoned exploration that taught
nothing, restated file contents, tool-call play-by-play, anything derivable
by reading the repo itself, pleasantries, duplicate statements of known facts.
When in doubt: would omitting this cause a future agent to repeat work or
repeat a mistake? If no, discard.

PRUNING DISCIPLINE
- Close or archive an open item ONLY when the digest explicitly states it is
  done, verified, or abandoned. Never infer redundancy.
- The session digest may describe memory-curation work that earlier distills
  already applied. The current files are the result of those distills: if the
  digest and the files disagree about what was already pruned or closed,
  trust the files and do not re-apply or extend old prunes.
- An item the user explicitly said to keep open is untouchable until they
  say otherwise. Err on the side of keeping items open.

STYLE
- Succinct and thorough. Dense declarative prose or tight bullets.
- No meta-commentary ("in this session we..."), no praise, no hedging.
- Use absolute dates (YYYY-MM-DD), never "today"/"recently".
- Stay grounded in the project's stated scope; do not invent goals.
- Preserve existing structure and headings where they still serve.

MECHANICS
- Do NOT emit YAML frontmatter; the tool manages it.
- Do NOT rewrite mistakes.md; only supply new entries.
- If a file needs no change, return null for it.
- If files exceed their budgets (given below), move the least-current
  sections into archive_entries rather than deleting them.
- Multiple agents may have worked in parallel; merge their threads, dedupe.

SELF-CHECK (before you output)
- Every todo you closed or archived: does the digest EXPLICITLY say it was
  completed, verified, or abandoned? If not, keep it open.
- Every new fact in state.md: is it stated in the digest or an inbox note,
  not inferred or assumed?
- No YAML frontmatter, no markdown fences, no prose around the JSON.

OUTPUT — exactly one JSON object, no markdown fences, no prose around it:
{
  "summary": "<one line: what changed in memory>",
  "state_body": "<full new body without frontmatter, or null>",
  "decisions_body": "<full new body, or null>",
  "todo_body": "<full new body, or null>",
  "mistakes_new_entries": ["### YYYY-MM-DD — <title>\\n<symptom/cause/prevention>", ...],
  "archive_entries": [{"source": "<file it came from>", "content": "<verbatim section>"}, ...]
}

EXAMPLE (illustrative; real inputs are far longer)
Digest: "U: move api to port 9090, 8080 clashes with unifi
A: changed config/server.yaml 8080->9090, restarted, health check passes"
Output: {"summary": "api port 8080->9090 (unifi clash)", "state_body":
"<full state.md body with the port fact replaced>", "decisions_body": null,
"todo_body": null, "mistakes_new_entries": [], "archive_entries": []}
"""
# --------------------------------------------------------------------------
# the distill: prompt -> claude -p -> validated apply
# --------------------------------------------------------------------------

def build_prompt(cfg, project_path, name, memory, digest, inbox_notes):
    budgets = cfg["budgets"]
    parts = [
        CHARTER,
        f"\nPROJECT: {name}  (root: {project_path})",
        f"DATE: {today()}",
        "BUDGETS (chars of body): "
        + ", ".join(f"{k}={v}" for k, v in budgets.items()),
        "\n===== CURRENT state.md =====\n" + (memory["state.md"] or "(empty)"),
        "\n===== CURRENT decisions.md =====\n" + (memory["decisions.md"] or "(empty)"),
        "\n===== CURRENT mistakes.md (append-only; for reference) =====\n"
        + (memory["mistakes.md"] or "(empty)"),
        "\n===== CURRENT todo.md =====\n" + (memory["todo.md"] or "(empty)"),
    ]
    if inbox_notes:
        parts.append("\n===== CURATOR INBOX =====\n" + "\n\n".join(inbox_notes))
    parts.append(
        "\n===== SESSION DIGEST (U=user, A=assistant, T=tool call, R=result) =====\n"
        + (digest or "(no new transcript content)")
    )
    parts.append("\nProduce the JSON object now.")
    return "\n".join(parts)


# Backoff between call_curator attempts (2 retries after the first try).
CURATOR_RETRY_DELAYS = (5, 15)


def call_curator(cfg, prompt, model):
    """Invoke the curator, retrying transient failures (timeout, nonzero
    exit, unparseable output) with backoff. A missing backend raises
    ConfigError and fails fast — retrying cannot install it."""
    for attempt, delay in enumerate(CURATOR_RETRY_DELAYS, start=1):
        try:
            return _call_curator_once(cfg, prompt, model)
        except CuratorError as e:
            log(f"curator attempt {attempt}/{len(CURATOR_RETRY_DELAYS) + 1} "
                f"failed, retrying in {delay}s: {e}")
            time.sleep(delay)
    return _call_curator_once(cfg, prompt, model)


def _call_curator_once(cfg, prompt, model):
    cmd = cfg.get("curator_cmd") or []
    if cmd:
        cmd = [arg.replace("{model}", model) for arg in cmd]
    else:
        bin_ = cfg["claude_bin"]
        if not shutil.which(bin_):
            for cand in (os.path.join(HOME, ".local", "bin", "claude"),):
                if os.path.exists(cand):
                    bin_ = cand
                    break
        cmd = [bin_, "-p", "--model", model, "--output-format", "json", "--max-turns", "1"]
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # allow nested invocation from inside a session
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=600, cwd=STATE_DIR, env=env,
        )
    except subprocess.TimeoutExpired:
        raise CuratorError("curator call timed out")
    except FileNotFoundError:
        raise ConfigError(
            f"curator backend not found: {cmd[0]!r} — install it or set "
            f"curator_cmd in {CONFIG_PATH}; backlog is preserved and will "
            "replay once a backend works")
    except OSError as e:
        raise CuratorError(f"curator invocation failed: {e}")
    if proc.returncode != 0:
        raise CuratorError(f"claude -p failed rc={proc.returncode}: {proc.stderr[:400]}")
    # Output is either a claude -p envelope ({"result": "<text>"}), a bare
    # curator JSON object (custom curator_cmd contract: output contains one
    # JSON object), or text with the JSON embedded (fences/prose tolerated).
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        text = proc.stdout
    else:
        if isinstance(envelope, dict) and isinstance(envelope.get("result"), str):
            text = envelope["result"]
        elif isinstance(envelope, dict):
            return envelope
        else:
            text = proc.stdout
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        raise CuratorError(f"no JSON in curator output: {text[:300]}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def validate(result, memory):
    """Reject obviously destructive or malformed curator output."""
    if not isinstance(result, dict):
        raise CuratorError("curator output is not an object")
    for key in ("state_body", "decisions_body", "todo_body"):
        v = result.get(key)
        if v is None:
            continue
        if not isinstance(v, str):
            raise CuratorError(f"{key} is not a string")

        v2 = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", v.strip())
        if v2 != v.strip():
            log(f"stripped fences from {key}")

        meta, body2 = split_frontmatter(v2)
        if meta:
            v2 = body2
            log(f"stripped curator-emitted frontmatter from {key}")

        fname = key.replace("_body", "") + ".md"
        if v2.strip() == SKELETONS[fname]:
            result[key] = None
            log(f"curator returned skeleton for {key}; treated as no-change")
            continue

        result[key] = v2

        old = memory[fname]
        archived = sum(
            len(a.get("content", "")) for a in result.get("archive_entries", [])
            if isinstance(a, dict)
        )
        if len(old) > 800 and len(v2) + archived < len(old) * 0.4:
            raise CuratorError(f"shrink guard tripped on {key} "
                               f"({len(old)} -> {len(v2)} chars, {archived} archived)")
    for e in result.get("mistakes_new_entries", []):
        if not isinstance(e, str):
            raise CuratorError("mistakes_new_entries contains a non-string")
    return result


def dedupe_mistakes(entries, existing_body):
    """Drop entries whose '### ' heading line already appears verbatim in
    mistakes.md (inbox is duplicate-tolerated; the audit log must not be)."""
    out = []
    for e in entries:
        e = (e or "").strip()
        if not e:
            continue
        head = e.splitlines()[0].strip()
        if head.startswith("### ") and head in existing_body:
            log(f"duplicate mistakes entry skipped: {head[:80]}")
            continue
        out.append(e)
    return out
