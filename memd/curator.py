"""The distill brain: curator charter, prompt assembly, LLM invocation, and
output validation."""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time

from memd.config import CONFIG_PATH, HOME, STATE_DIR, log, today
from memd.errors import ConfigError, CuratorError
from memd.memory import (SKELETONS, SECTION_RE, split_frontmatter,
                         split_sections, section_date)
from memd.recall import rank, terms_from_text

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
  trust the files and do not re-apply or extend old prunes; when present,
  the PREVIOUS DISTILLS section below is the evidence base for what those
  distills already did.
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
- Do NOT modify mistakes.md with ops; only supply new entries via
  mistakes_new_entries.
- If nothing changed, return an empty ops list.
- When an OVER BUDGET section appears, shrink those files toward their
  budgets: archive sections that are superseded, completed, or no longer
  constrain future work (archive_entries + delete_section), or merge
  overlapping sections. Never archive something only because it is old.
- Multiple agents may have worked in parallel; merge their threads, dedupe.

PARTIAL VIEW (large memories only)
- A file marked PARTIAL VIEW lists every section heading but shows bodies
  only for sections relevant to this digest; the others read
  "[body not shown: N chars]".
- replace_section/delete_section ONLY sections whose body is shown; ops on
  hidden sections are discarded. set_body is not allowed on a partial file.
- append_section works as usual: check the heading list first so you do not
  duplicate a hidden section.
- A shown section marked [oversized section: ...]: when you replace it, split
  its content into several focused ### subsections inside the new body.

DIGEST FORMAT (when present)
- Session spans carry "--- session span | ... ---" headers with UTC times;
  date findings from those headers, never guesswork.
- Lines beginning "  SUB> " are subagent work: secondary signal — merge
  their findings, do not narrate them as separate actors.
- "[... omitted: ...]" markers denote spans deliberately dropped for
  budget; their existence is signal, do not invent their content.

SELF-CHECK (before you output)
- Every todo you closed or archived: does the digest EXPLICITLY say it was
  completed, verified, or abandoned? If not, keep it open.
- Every new fact in state.md: is it stated in the digest or an inbox note,
  not inferred or assumed?
- Every heading in a replace/delete op copied EXACTLY from the file?
- No YAML frontmatter, no markdown fences, no prose around the JSON.

OUTPUT — exactly one JSON object, no markdown fences, no prose around it:
{
  "summary": "<one line: what changed in memory>",
  "ops": [{"op": "replace_section" | "append_section" | "delete_section" | "set_body",
           "file": "state.md" | "decisions.md" | "todo.md",
           "heading": "<exact current h2/h3 heading>",
           "body": "<new section body, or the full body for set_body>"}],
  "mistakes_new_entries": ["### YYYY-MM-DD — <title>\\n<symptom/cause/prevention>", ...],
  "archive_entries": [{"source": "<file it came from>", "content": "<verbatim section>"}, ...]
}
OP RULES
- Prefer the MINIMAL set of ops; an empty ops list means no changes.
- "heading" is required for every op except set_body; "body" is required for
  every op except delete_section.
- replace_section/delete_section must quote the heading EXACTLY as it
  appears in the file, including the "## "/"### " prefix and inline text.
- append_section fails if that heading already exists; use replace_section.
- When a replacement changes what a section is about (a reversed decision,
  a renamed component), begin its body with the new heading line: that
  renames the section, so no stale heading survives above new content.
- set_body (full-file replacement) is a last resort for wholesale
  restructuring, never for a single-section change.

EXAMPLE (illustrative; real inputs are far longer)
Digest: "U: move api to port 9090, 8080 clashes with unifi
A: changed config/server.yaml 8080->9090, restarted, health check passes"
Output: {"summary": "api port 8080->9090 (unifi clash)", "ops":
[{"op": "replace_section", "file": "state.md", "heading": "## Services",
"body": "- api: localhost:9090 (moved from 8080; unifi owns 8080)"}],
"mistakes_new_entries": [], "archive_entries": []}
"""
# --------------------------------------------------------------------------
# the distill: prompt -> claude -p -> validated apply
# --------------------------------------------------------------------------

# Cap for the slimmed mistakes block in prompts. The full audit log used to be
# re-sent on every distill; only its "### " heading lines (what duplicate
# detection matches) plus one style exemplar carry signal, so a long log must
# not crowd state/decisions/todo out of every prompt.
MISTAKES_BLOCK_CAP = 3000


def _mistakes_block(body):
    """Headings-only view of mistakes.md plus the single newest entry as a
    style exemplar, capped at MISTAKES_BLOCK_CAP chars. The log is append-only
    and archived in the repo, so historical bodies are pure token cost here;
    shedding oldest headings first keeps the most recent ones visible."""
    body = body or ""
    headings = [ln.rstrip() for ln in body.splitlines() if ln.startswith("### ")]
    secs = re.split(r"(?m)^(?=### )", body)
    newest = secs[-1].strip() if len(secs) > 1 else ""
    header = ("===== CURRENT mistakes.md (headings-only; full log is "
              "append-only and archived in the repo) =====")
    if not headings and not newest:
        return header + "\n(empty)"
    exemplar = ("\n\nNEWEST ENTRY (style exemplar):\n" + newest) if newest else ""

    def render(kept, note):
        return header + "\n" + note + "\n".join(kept) + exemplar

    note, omitted = "", 0
    while headings and len(render(headings, note)) > MISTAKES_BLOCK_CAP:
        headings.pop(0)
        omitted += 1
        note = f"\n[... omitted: {omitted} older heading(s) over budget ...]\n"
    block = render(headings, note)
    if len(block) > MISTAKES_BLOCK_CAP:
        # newest entry alone blows the cap: hard-ellipsis rather than lie
        block = block[:MISTAKES_BLOCK_CAP].rstrip() + "\n[... truncated ...]"
    return block


def _partial_file(fname, body, shown):
    preamble, sections = _split_sections_raw(body or "")
    k = sum(heading in shown for heading, _ in sections)
    header = (f"\n===== CURRENT {fname} (PARTIAL VIEW: {k} of {len(sections)} "
              "sections shown in full; the rest are listed by heading only) =====")
    rendered = []
    for heading, section_body in sections:
        if heading in shown:
            marker = (f"\n[oversized section: {len(section_body)} chars — if you "
                      "replace it, split it into focused ### subsections]"
                      if len(section_body) > OVERSIZED_SECTION_CHARS else "")
            rendered.append(heading + marker + "\n\n" + section_body.strip() + "\n")
        else:
            rendered.append(heading + "\n"
                            + f"[body not shown: {len(section_body)} chars]\n")
    return header + "\n" + (preamble + "\n".join(rendered) if body else "(empty)")


# Partial curator view (large memory): every heading, relevant bodies only.
VIEW_FILES = ("state.md", "decisions.md", "todo.md")
OVERSIZED_SECTION_CHARS = 2500
VIEW_RECENT_DAYS = 14
ARCHIVE_CANDIDATES = 3      # oldest dated sections shown per over-budget file


def over_budget(cfg, memory):
    """{file: (chars, budget)} for state/decisions/todo bodies over budget."""
    out = {}
    for f in VIEW_FILES:
        n = len(memory.get(f) or "")
        if n > cfg["budgets"][f]:
            out[f] = (n, cfg["budgets"][f])
    return out


def archive_candidates(body, k=ARCHIVE_CANDIDATES):
    """Headings of the k oldest dated sections (undated never qualify)."""
    dated = []
    for i, (heading, text) in enumerate(split_sections(body or "")):
        date = section_date(heading + "\n" + text)
        if date:
            dated.append((date, i, heading))
    return [heading for _, _, heading in sorted(dated)[:k]]


def select_view(cfg, memory, digest, inbox_notes):
    """None when state/decisions/todo fit curator_view_chars (full view); else
    {file: set of headings whose bodies the curator is shown}."""
    budget = cfg["curator_view_chars"]
    if sum(len(memory.get(f) or "") for f in VIEW_FILES) <= budget:
        return None
    secs = [{"file": f, "heading": heading, "body": body}
            for f in VIEW_FILES
            for heading, body in split_sections(memory.get(f) or "")]
    query = " ".join(terms_from_text(digest + "\n" + "\n".join(inbox_notes)))
    candidates = [index for index, _ in rank(secs, query)]
    listed = set(candidates)
    cutoff = (dt.date.today() - dt.timedelta(days=VIEW_RECENT_DAYS)).isoformat()
    recent = [(index, section_date(sec["heading"] + "\n" + sec["body"]))
              for index, sec in enumerate(secs) if index not in listed]
    candidates.extend(index for index, date in
                      sorted((item for item in recent if item[1] and item[1] >= cutoff),
                             key=lambda item: item[1], reverse=True))
    view = {f: set() for f in VIEW_FILES}
    used, taken = 0, False
    for index in candidates:
        sec = secs[index]
        cost = len(sec["heading"]) + len(sec["body"])
        if used + cost <= budget or not taken:
            view[sec["file"]].add(sec["heading"])
            used += cost
            taken = True
    for f in over_budget(cfg, memory):
        view[f].update(archive_candidates(memory.get(f)))
    return view


def build_prompt(cfg, project_path, name, memory, digest, inbox_notes,
                 recent_distills=None, view=None):
    budgets = cfg["budgets"]
    parts = [
        f"PROJECT: {name}  (root: {project_path})",
        f"DATE: {today()}",
        "BUDGETS (chars of body): "
        + ", ".join(f"{k}={v}" for k, v in budgets.items()),
    ]
    over = over_budget(cfg, memory)
    if over:
        parts.append(
            "\n===== OVER BUDGET =====\n" + "\n".join(
                f"{f}: {n:,} chars, budget {b:,}. Archive candidates (oldest dated "
                "sections, shown in full): "
                + (", ".join(f'"{h}"' for h in archive_candidates(memory.get(f)))
                   or "none (no dated sections)")
                for f, (n, b) in over.items())
            + "\nShrink these files toward budget: archive sections that are superseded, "
              "completed, or no longer constrain future work (archive_entries + "
              "delete_section), or merge overlapping sections. Keep anything still "
              "current, whatever its age."
        )
    parts.extend([
        (_partial_file("state.md", memory["state.md"], view["state.md"])
         if view is not None else
         "\n===== CURRENT state.md =====\n" + (memory["state.md"] or "(empty)")),
        (_partial_file("decisions.md", memory["decisions.md"], view["decisions.md"])
         if view is not None else
         "\n===== CURRENT decisions.md =====\n" + (memory["decisions.md"] or "(empty)")),
        _mistakes_block(memory["mistakes.md"]),
        (_partial_file("todo.md", memory["todo.md"], view["todo.md"])
         if view is not None else
         "\n===== CURRENT todo.md =====\n" + (memory["todo.md"] or "(empty)")),
    ])
    if inbox_notes:
        parts.append("\n===== CURATOR INBOX =====\n" + "\n\n".join(inbox_notes))
    if recent_distills:
        # What earlier distills already applied, newest last: the charter's
        # anti-reapply rule needs an evidence base or the model re-prunes from
        # stale digest text.
        parts.append(
            "\n===== PREVIOUS DISTILLS (already applied — do not redo or "
            "re-archive their prunes) =====\n" + "\n".join(recent_distills))
    parts.append(
        "\n===== SESSION DIGEST (U=user, A=assistant, T=tool call, R=result) =====\n"
        + (digest or "(no new transcript content)")
    )
    parts.append("\nProduce the JSON object now.")
    return "\n".join(parts)


# Backoff between call_curator attempts (2 retries after the first try).
CURATOR_RETRY_DELAYS = (5, 15)


# Curator calls are single-shot completions: no tools, MCP servers, user
# settings (hooks, plugins, CLAUDE.md) or skills, and no saved transcript.
LEAN_CLAUDE_FLAGS = ["--tools", "", "--strict-mcp-config", "--setting-sources", "",
                     "--disable-slash-commands", "--no-session-persistence"]


def _add_usage(acc, envelope):
    """Accumulate a claude -p envelope's token/cost figures into acc (retries
    cost real tokens too, so attempts sum)."""
    if acc is None or not isinstance(envelope, dict):
        return
    u = envelope.get("usage")
    if not isinstance(u, dict):
        return

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    acc["input_tokens"] = acc.get("input_tokens", 0) + int(
        num(u.get("input_tokens")) + num(u.get("cache_creation_input_tokens"))
        + num(u.get("cache_read_input_tokens")))
    acc["cache_read_tokens"] = acc.get("cache_read_tokens", 0) + int(
        num(u.get("cache_read_input_tokens")))
    acc["output_tokens"] = acc.get("output_tokens", 0) + int(
        num(u.get("output_tokens")))
    acc["cost_usd"] = round(acc.get("cost_usd", 0.0)
                            + float(num(envelope.get("total_cost_usd"))), 6)


def call_curator(cfg, prompt, model, usage=None):
    """Invoke the curator, retrying transient failures (timeout, nonzero
    exit, unparseable output) with backoff. A missing backend raises
    ConfigError and fails fast — retrying cannot install it.
    usage, when a dict, accumulates token/cost figures across attempts."""
    for attempt, delay in enumerate(CURATOR_RETRY_DELAYS, start=1):
        try:
            return _call_curator_once(cfg, prompt, model, usage)
        except CuratorError as e:
            log(f"curator attempt {attempt}/{len(CURATOR_RETRY_DELAYS) + 1} "
                f"failed, retrying in {delay}s: {e}")
            time.sleep(delay)
    return _call_curator_once(cfg, prompt, model, usage)


def _call_curator_once(cfg, prompt, model, usage=None):
    cmd = cfg.get("curator_cmd") or []
    custom = bool(cmd)
    if cmd:
        cmd = [arg.replace("{model}", model) for arg in cmd]
        stdin = CHARTER + "\n\n" + prompt
    else:
        bin_ = cfg["claude_bin"]
        if not shutil.which(bin_):
            for cand in (os.path.join(HOME, ".local", "bin", "claude"),):
                if os.path.exists(cand):
                    bin_ = cand
                    break
        base = [bin_, "-p", "--model", model, "--output-format", "json", "--max-turns", "1"]
        cmd = base + ["--system-prompt", CHARTER] + LEAN_CLAUDE_FLAGS
        stdin = prompt
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # allow nested invocation from inside a session
    os.makedirs(STATE_DIR, exist_ok=True)

    def run(cmd, stdin):
        return subprocess.run(
            cmd, input=stdin, capture_output=True, text=True,
            timeout=600, cwd=STATE_DIR, env=env,
        )

    try:
        proc = run(cmd, stdin)
        if not custom and proc.returncode != 0 and "unknown option" in proc.stderr:
            log(f"claude rejected lean curator flags "
                f"({proc.stderr.strip()[:200]}); retrying with full session context")
            cmd = base
            proc = run(cmd, CHARTER + "\n\n" + prompt)
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
        _add_usage(usage, envelope)
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
    try:
        # strict=False permits raw control characters (most often an unescaped
        # newline) inside strings. Weaker/local curator models emit those
        # constantly, and memd is already lenient about fences and surrounding
        # prose; rejecting an otherwise well-formed object over a literal
        # newline would make small backends unusable for no benefit.
        obj, _ = json.JSONDecoder(strict=False).raw_decode(text[start:])
    except ValueError as e:
        # Smaller/local models routinely emit near-JSON (bad \escapes, trailing
        # commas). That is a bad response, not a bug in memd: raise the
        # retryable error so the backoff gets another sample, rather than
        # letting JSONDecodeError escape as an unhandled traceback.
        raise CuratorError(f"curator output is not valid JSON ({e}): "
                           f"{text[start:start + 300]}")
    return obj


# Section-op protocol: the only op names and files validate() accepts.
OP_NAMES = ("replace_section", "append_section", "delete_section", "set_body")
# mistakes.md is accepted here but NOT advertised in the charter's output
# schema: only a legacy full-body curator can name it, and sync drops that
# write to keep the log append-only. Advertising it invited the section ops
# that validate() then rejects outright.
OP_FILES = ("state.md", "decisions.md", "todo.md", "mistakes.md")

# A curated file may not lose more than this share of its size in one distill
# unless the loss is accounted for by archive_entries. Models over-prune, and
# memory loss is the one failure this tool exists to prevent.
SHRINK_FLOOR = 0.4
SHRINK_GUARD_MIN = 800   # under this, the ratio is too noisy to mean anything


def _shrunk(old, new, archived):
    return (len(old) > SHRINK_GUARD_MIN
            and len(new) + archived < len(old) * SHRINK_FLOOR)


def _strip_wrappers(text):
    """Shared output hygiene for emitted bodies: strip markdown fences and
    curator-emitted YAML frontmatter. Returns (body, changed)."""
    stripped = text.strip()
    v2 = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", stripped)
    meta, v3 = split_frontmatter(v2)
    if meta:
        return v3, True
    return v2, v2 != stripped


def validate(result, memory, view=None):
    """Normalize and police curator output (the section-op protocol): known op names and
    files, required heading/body, mistakes.md reachable only via mistakes_new_entries, the
    shrink guard, and the partial-view rules. Non-null legacy full-body keys raise. Returns
    the same dict with summary/ops/mistakes_new_entries/archive_entries normalized."""
    if not isinstance(result, dict):
        raise CuratorError("curator output is not an object")

    # Phase 1 — shape: validate native ops before policing their bodies.
    items = []
    legacy = [k for k in ("state_body", "decisions_body", "todo_body")
              if result.get(k) is not None]
    if legacy:
        raise CuratorError(
            f"{', '.join(legacy)}: the full-body output protocol was removed in "
            "0.6.0; emit ops (set_body for a whole-file rewrite)")

    raw_ops = result.get("ops") or []
    if not isinstance(raw_ops, list):
        raise CuratorError("ops is not a list")
    for i, op in enumerate(raw_ops):
        if not isinstance(op, dict):
            raise CuratorError(f"ops[{i}] is not an object")
        kind = op.get("op")
        if kind not in OP_NAMES:
            raise CuratorError(f"ops[{i}]: unknown op {kind!r}")
        fname = op.get("file")
        if fname not in OP_FILES:
            raise CuratorError(f"ops[{i}]: unknown file {fname!r}")
        if fname == "mistakes.md" and kind != "set_body":
            # mistakes stays append-only through mistakes_new_entries; archive
            # overflow of it is code-managed (enforce_budget_mistakes).
            raise CuratorError(
                f"ops[{i}]: mistakes.md is append-only — supply entries via "
                "mistakes_new_entries")
        entry = {"op": kind, "file": fname}
        if kind != "set_body":
            heading = op.get("heading")
            if not isinstance(heading, str):
                raise CuratorError(
                    f"ops[{i}] {kind}/{fname}: missing or non-string heading")
            entry["heading"] = heading.strip()
        if kind != "delete_section":
            body = op.get("body")
            if not isinstance(body, str):
                raise CuratorError(
                    f"ops[{i}] {kind}/{fname}: missing or non-string body")
            entry["body"] = body
        label = f"ops[{i}] {kind}/{fname}"
        if kind == "replace_section" and not entry["body"].strip():
            # curators "retire" a section by emptying it; a bare heading left
            # behind reads as a still-active entry
            log(f"{label}: empty replace treated as delete_section")
            kind = entry["op"] = "delete_section"
            del entry["body"]
        if view is not None and fname in view:
            if kind == "set_body":
                log(f"dropped {label}: set_body on a partial-view file")
                continue
            if kind in ("replace_section", "delete_section") and entry["heading"] not in view[fname]:
                log(f"dropped {label}: section not shown in partial view")
                continue
        items.append((entry, label))

    ops = []
    archived = sum(
        len(a.get("content", "")) for a in result.get("archive_entries", [])
        if isinstance(a, dict))
    # Phase 2 — police every op identically regardless of protocol origin.
    for entry, label in items:
        kind, fname = entry["op"], entry["file"]
        if kind != "delete_section":
            body, changed = _strip_wrappers(entry["body"])
            if changed:
                log(f"stripped fences/frontmatter from {label}")
            entry["body"] = body
        if kind == "set_body":
            # Skeleton echo means no-change (weaker models parrot inputs);
            # dropping it preserves that exact behavior on either protocol.
            if entry["body"].strip() == SKELETONS[fname]:
                log(f"curator returned skeleton for {label}; treated as "
                    "no-change")
                continue
            old = memory.get(fname, "") or ""
            if _shrunk(old, entry["body"], archived):
                raise CuratorError(
                    f"shrink guard tripped on {label} "
                    f"({len(old)} -> {len(entry['body'])} chars, "
                    f"{archived} archived)")
        ops.append(entry)

    # Same guard, applied to what section ops actually PRODUCE. A per-op check
    # cannot see the cumulative result: delete_section on every heading leaves
    # a file holding nothing but its title, and each op looks harmless alone.
    # The set_body check above stays only because it can name the offending op.
    section_files = {e["file"] for e in ops if e["op"] != "set_body"}
    if section_files:
        applied = apply_ops(ops, {f: memory.get(f, "") or "" for f in OP_FILES},
                            skipped=[])
        for fname in sorted(section_files):
            old, new = memory.get(fname, "") or "", applied.get(fname)
            if new is not None and _shrunk(old, new, archived):
                raise CuratorError(
                    f"shrink guard tripped on {fname} section ops "
                    f"({len(old)} -> {len(new)} chars, {archived} archived)")

    result["summary"] = result["summary"] if isinstance(
        result.get("summary"), str) else ""
    result["ops"] = ops

    entries = result.get("mistakes_new_entries") or []
    if not isinstance(entries, list):
        raise CuratorError("mistakes_new_entries is not a list")
    for e in entries:
        if not isinstance(e, str):
            raise CuratorError("mistakes_new_entries contains a non-string")
    result["mistakes_new_entries"] = entries
    if not isinstance(result.get("archive_entries"), list):
        result["archive_entries"] = []

    return result


# --------------------------------------------------------------------------
# section-op application (pure)
# --------------------------------------------------------------------------


def _split_sections_raw(body):
    """Split a body into (preamble, [[heading_line, rest], ...]) at h2/h3
    boundaries (same convention as memory.SECTION_RE), but unlike
    memory.split_sections() nothing is dropped or stripped: the preamble keeps
    the h1 title, and heading+rest concatenate back to the exact source bytes,
    which is what makes untouched files round-trip identically."""
    parts = SECTION_RE.split(body)
    secs = []
    for chunk in parts[1:]:
        # sep+rest keeps the heading line's own newline so concatenating a
        # section back reproduces its source bytes exactly
        head, sep, rest = chunk.partition("\n")
        secs.append([head, sep + rest])
    return parts[0], secs


def _join_sections_raw(preamble, secs):
    out = [preamble]
    for head, rest in secs:
        out.append(head + rest)
    return "".join(out)


def _apply_section_op(op, body):
    """One replace/append/delete op against one file body.

    Returns (new_body, None) when the op applies, or (None, reason) when its
    heading does not resolve. Exact-heading matching is the most
    hallucination-sensitive interface in the pipeline, and one invented heading
    used to raise — discarding every valid op in the same batch, failing the
    distill, and replaying the identical digest into the identical model next
    sweep. A bad op is now dropped on its own; the caller reports it.

    Sections match their FULL heading line exactly ('## '/'### ' prefix and
    inline text); touched sections re-space canonically ('\n\n' around the
    body) while untouched text is preserved byte-for-byte."""
    fname, kind = op["file"], op["op"]
    preamble, secs = _split_sections_raw(body)
    heading = op.get("heading", "")
    if kind == "append_section":
        if not heading.startswith(("## ", "### ")):
            heading = "## " + heading  # curator omitted the prefix; assume h2
        if any(s[0] == heading for s in secs):
            return None, (f"append_section: heading already exists in {fname}: "
                          f"{heading!r} — use replace_section")
        if secs:
            if not secs[-1][1].endswith("\n"):
                secs[-1][1] += "\n"
        elif preamble and not preamble.endswith("\n"):
            preamble += "\n"
        secs.append([heading, "\n\n" + (op.get("body") or "").strip() + "\n\n"])
        return _join_sections_raw(preamble, secs), None
    idx = next((i for i, s in enumerate(secs) if s[0] == heading), None)
    if idx is None:
        return None, f"{kind}: no section {heading!r} in {fname}"
    if kind == "replace_section":
        new = (op.get("body") or "").strip()
        first, _, rest = new.partition("\n")
        if re.match(r"#{2,3} \S", first):
            # body opens with its own heading: a rename, so the old heading
            # must not linger above it as an empty (still "active") entry
            secs[idx] = [first.strip(), "\n\n" + rest.strip() + "\n\n"]
        else:
            secs[idx][1] = "\n\n" + new + "\n\n"
    else:  # delete_section removes heading+body wholesale
        del secs[idx]
    return _join_sections_raw(preamble, secs), None


def apply_ops(ops, bodies, skipped=None):
    """Pure section-op application (no I/O, no validation): ops + current
    bodies -> {fname: new_body_or_None}, None meaning "unchanged, do not
    write". Ops compose left-to-right within a file; files are independent;
    set_body replaces wholesale. Expects ops already through validate().

    Ops whose heading does not resolve are dropped, and their reason appended
    to `skipped` when the caller passes a list — logging is the caller's job,
    so this stays pure. A file whose ops all skipped is reported unchanged."""
    work = {}
    for op in ops:
        fname = op["file"]
        base = work[fname] if fname in work else bodies.get(fname, "")
        if op["op"] == "set_body":
            work[fname] = op["body"]
            continue
        new, reason = _apply_section_op(op, base)
        if new is None:
            if skipped is not None:
                skipped.append(reason)
            continue
        work[fname] = new
    out = dict.fromkeys(bodies, None)
    out.update(work)
    return out


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
