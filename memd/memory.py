"""Frontmatter, memory-file writing, scaffolding, project registry, archive,
budgets, git audit commits, and the session-start brief."""

import datetime as dt
import os
import re
import subprocess

from memd.config import (META_PATH, MEMORY_FILES, atomic_write, git_toplevel,
                         load_json, log, save_config, today)
from memd.inbox import collect_inbox

BRIEF_NOTE = (
    "Project memory is managed by memd. Read .memory/state.md, decisions.md, "
    "mistakes.md, todo.md before substantive work. To leave a note for the "
    "curator from any tool, drop a markdown file in .memory/inbox/."
)
# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------

FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def split_frontmatter(text):
    """Return (meta_dict, body). Tolerates files without frontmatter."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def render_frontmatter(meta):
    order = ["type", "project", "last_updated", "status"]
    keys = [k for k in order if k in meta] + [k for k in meta if k not in order]
    lines = [f"{k}: {meta[k]}" for k in keys]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def write_memory_file(path, body, project_name, ftype):
    meta = {}
    if os.path.exists(path):
        meta, _ = split_frontmatter(open(path).read())
    meta.setdefault("type", ftype)
    meta.setdefault("project", project_name)
    meta.setdefault("status", "active")
    meta["last_updated"] = today()
    atomic_write(path, render_frontmatter(meta) + body.strip() + "\n")


# --------------------------------------------------------------------------
# scaffolding & registry
# --------------------------------------------------------------------------

SKELETONS = {
    "state.md": "# System State\n\n_No state recorded yet. memd populates this from sessions._",
    "decisions.md": "# Architecture Decisions\n\n_No decisions recorded yet._",
    "mistakes.md": "# Mistake Audit Log (append-only)\n\n_No mistakes recorded yet._",
    "todo.md": "# Open Tasks\n\n_No tasks recorded yet._",
}

MODEL_STUB = """\
# Project Instructions

## Memory protocol (managed by memd)
Read `./.memory/state.md`, `decisions.md`, `mistakes.md`, and `todo.md` before
substantive work. They are distilled automatically after sessions — keep them
authoritative. To leave an explicit note for the memory curator (from any CLI,
agent, or swarm member), write a markdown file into `./.memory/inbox/`.
"""


def scaffold(path, name=None, model_stub=True):
    """Create .memory/ (+ optional .model/ stub) for a project; safe on existing dirs.

    model_stub=False for the global root: it is not a project with agent
    instructions, just a memory store, so no .model/CLAUDE.md is written."""
    name = name or os.path.basename(path.rstrip("/")) or path
    mem = os.path.join(path, ".memory")
    created = []
    for sub in ("", "archive", "inbox"):
        d = os.path.join(mem, sub)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            created.append(d)
    for sub in ("archive", "inbox"):
        keep = os.path.join(mem, sub, ".gitkeep")
        if not os.path.exists(keep):
            open(keep, "w").close()
    for fname, skeleton in SKELETONS.items():
        fpath = os.path.join(mem, fname)
        if not os.path.exists(fpath):
            ftype = fname.split(".")[0]
            write_memory_file(fpath, skeleton, name, ftype)
            created.append(fpath)
    if model_stub:
        model_dir = os.path.join(path, ".model")
        model_md = os.path.join(model_dir, "CLAUDE.md")
        if not os.path.exists(model_md):
            os.makedirs(model_dir, exist_ok=True)
            atomic_write(model_md, MODEL_STUB)
            created.append(model_md)
    return created
def register(cfg, path, name=None):
    path = os.path.realpath(path)
    if path not in cfg["projects"]:
        cfg["projects"][path] = {
            "name": name or os.path.basename(path.rstrip("/")),
            "extra_sources": [],
        }
        save_config(cfg)
    return cfg["projects"][path]


def find_project(cfg, cwd):
    """Map a session cwd to a registered project root (longest prefix wins).

    The global root (entry flagged "global") is deliberately NOT matched here:
    it is the explicit fallback for sessions belonging to no project, applied by
    callers AFTER new-project auto-scaffolding has had its chance on an
    unmatched cwd."""
    cwd = os.path.realpath(cwd)
    best = None
    for p, e in cfg["projects"].items():
        if e.get("global"):
            continue
        if cwd == p or cwd.startswith(p.rstrip("/") + "/"):
            if best is None or len(p) > len(best):
                best = p
    return best
def read_memory(project_path):
    mem = os.path.join(project_path, ".memory")
    out = {}
    for fname in MEMORY_FILES:
        p = os.path.join(mem, fname)
        if os.path.exists(p):
            _, body = split_frontmatter(open(p).read())
            out[fname] = body.strip()
        else:
            out[fname] = ""
    return out
def archive_path(project_path):
    return os.path.join(
        project_path, ".memory", "archive", dt.date.today().strftime("%Y-%m") + ".md"
    )


def append_archive(project_path, entries, reason):
    if not entries:
        return
    p = archive_path(project_path)
    header = not os.path.exists(p)
    with open(p, "a") as f:
        if header:
            f.write(f"---\ntype: archive\nlast_updated: {today()}\nstatus: archived\n---\n")
        f.write(f"\n<!-- archived {today()} ({reason}) -->\n")
        for e in entries:
            src = e.get("source", "?") if isinstance(e, dict) else "?"
            content = e.get("content", "") if isinstance(e, dict) else str(e)
            f.write(f"\n## from {src}\n\n{content.strip()}\n")


def enforce_budget_mistakes(project_path, name, budget):
    """Deterministic overflow: move oldest H3 sections of mistakes.md to archive."""
    p = os.path.join(project_path, ".memory", "mistakes.md")
    if not os.path.exists(p):
        return
    meta, body = split_frontmatter(open(p).read())
    if len(body) <= budget:
        return
    sections = re.split(r"(?m)^(?=### )", body)
    head, entries = sections[0], sections[1:]
    moved = []
    while entries and len(head) + sum(len(s) for s in entries) > budget:
        moved.append(entries.pop(0))
    if moved:
        append_archive(project_path, [{"source": "mistakes.md", "content": s} for s in moved],
                       "size budget overflow")
        write_memory_file(p, head + "".join(entries), name, "mistakes")
        log(f"pruned {len(moved)} mistakes.md sections to archive in {project_path}")


def git_commit_memory(project_path, message):
    mem = os.path.join(project_path, ".memory")
    # Resolve the repo from .memory itself, not the project path. For a normal
    # project this is the project's own repo (.memory lives inside it); for the
    # global root it is ~/.memory's standalone repo. Deriving from project_path
    # would, if $HOME were ever a git repo (dotfiles), commit global memory into
    # the home repo instead of ~/.memory.
    top = git_toplevel(mem)
    if not top:
        return
    rel = os.path.relpath(mem, top)
    try:
        subprocess.run(["git", "-C", top, "add", "--", rel],
                       capture_output=True, timeout=30)
        diff = subprocess.run(["git", "-C", top, "diff", "--cached", "--quiet", "--", rel],
                              timeout=30)
        if diff.returncode == 0:
            return
        r = subprocess.run(
            ["git", "-C", top, "commit", "-m", message, "--", rel],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            log(f"git commit failed in {top}: {r.stderr.strip()[:200]}")
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"git commit error in {top}: {e}")
# --------------------------------------------------------------------------
# brief (session-start context)
# --------------------------------------------------------------------------


def global_slice(cfg, global_root):
    """Bounded, pointer-style view of cross-project (global) memory for layering
    into a project session's brief. Surfaces a pointer to the global store, a
    capped excerpt of its state.md, and a few global open todos — never the full
    bodies, so global facts are discoverable without bulk-injecting them every
    session. Cap is cfg['global_brief_chars']: 0 = pointer only, <0 = omit."""
    cap = cfg.get("global_brief_chars", 800)
    if cap < 0:
        return []
    mem = os.path.join(global_root, ".memory")
    if not os.path.isdir(mem):
        return []
    out = [f"Global memory (cross-project) lives at {mem} — read its state.md / "
           "decisions.md there when a task touches user, system, or cross-project "
           "facts; leave global notes in its inbox/."]
    if cap > 0:
        state_p = os.path.join(mem, "state.md")
        if os.path.exists(state_p):
            _, body = split_frontmatter(open(state_p).read())
            excerpt = body.strip()
            if excerpt:
                if len(excerpt) > cap:
                    excerpt = (excerpt[:cap].rstrip()
                               + "\n[... global state.md truncated; read in full "
                                 "if relevant ...]")
                out.append("Global state.md (excerpt):\n" + excerpt)
    todo_p = os.path.join(mem, "todo.md")
    if os.path.exists(todo_p):
        _, body = split_frontmatter(open(todo_p).read())
        items = re.findall(r"(?m)^\s*[-*] \[ \] (.+)$", body)[:5]
        if items:
            out.append("Global open todo items:\n"
                       + "\n".join(f"- {i}" for i in items))
    return out


# Content-section boundary inside memory-file bodies: h2/h3 headings only —
# the h1 file title is never a section boundary.
SECTION_RE = re.compile(r"(?m)^(?=#{2,3} \S)")
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def split_sections(body):
    """Split a memory-file body into (heading, body) h2/h3 sections. Anything
    before the first section heading (h1 title, preamble) is dropped."""
    out = []
    for chunk in SECTION_RE.split(body):
        chunk = chunk.strip()
        if not re.match(r"#{2,3} \S", chunk):
            continue
        heading, _, rest = chunk.partition("\n")
        out.append((heading.strip(), rest.strip()))
    return out


def section_date(text):
    """Newest ISO date mentioned in a section, or None. Missing timestamps are
    tolerated — recency scoring degrades to 'sorted last', never a crash."""
    dates = DATE_RE.findall(text)
    return max(dates) if dates else None


def brief_sections(cfg, mem):
    """Ordered (priority, heading, body, char_count) content sections for the
    budgeted brief. Priority tiers: 1 open todos, 2 recent decisions, 3 state.md
    excerpts, 4 mistakes summary. Recency DESC within a tier (state.md keeps
    document order: top of file is most recent per curator conventions)."""
    sections = []

    def add(priority, heading, body):
        sections.append((priority, heading, body, len(heading) + 1 + len(body)))

    todo_p = os.path.join(mem, "todo.md")
    if os.path.exists(todo_p):
        _, body = split_frontmatter(open(todo_p).read())
        items = re.findall(r"(?m)^\s*[-*] \[ \] (.+)$", body)[:8]
        if items:
            add(1, "Open todo items:", "\n".join(f"- {i}" for i in items))

    dec_p = os.path.join(mem, "decisions.md")
    if os.path.exists(dec_p):
        _, body = split_frontmatter(open(dec_p).read())
        days = cfg.get("brief_decisions_days", 30)
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        recent = []
        for heading, sbody in split_sections(body):
            d = section_date(heading + "\n" + sbody)
            # undated sections are kept (graceful degradation) but sort last
            if d is None or d >= cutoff:
                recent.append((d or "", heading, sbody))
        recent.sort(key=lambda t: t[0], reverse=True)
        for _, heading, sbody in recent:
            add(2, heading, sbody)

    state_p = os.path.join(mem, "state.md")
    if os.path.exists(state_p):
        _, body = split_frontmatter(open(state_p).read())
        for heading, sbody in split_sections(body):
            add(3, heading, sbody)

    mis_p = os.path.join(mem, "mistakes.md")
    if os.path.exists(mis_p):
        _, body = split_frontmatter(open(mis_p).read())
        entries = re.split(r"(?m)^(?=### )", body)[1:]
        if entries:
            # summary only: count + newest entry (append-only file, so last
            # entry is newest), never the full log body
            add(4, f"Mistakes log: {len(entries)} entries; most recent:",
                entries[-1].strip())
    return sections


def make_brief(cfg, project_path, max_chars=None, topic=None):
    project_path = os.path.realpath(project_path)
    mem = os.path.join(project_path, ".memory")
    if not os.path.isdir(mem):
        return None
    sections = brief_sections(cfg, mem)
    if topic is not None:
        t = topic.lower()
        sections = [s for s in sections
                    if t in s[1].lower() or t in s[2].lower()]
        if not sections:
            return BRIEF_NOTE
    head, tail = [BRIEF_NOTE], []
    if topic is None:
        # fixed (unbudgeted) parts around the content sections — the pre-budget
        # brief in full, so default output is a strict superset of it
        meta = load_json(META_PATH, {}).get(project_path)
        if meta:
            head.append(f"Last memory distill: {meta['last_sync']} "
                        f"({meta['trigger']}) — {meta['summary']}")
        state_p = os.path.join(mem, "state.md")
        if os.path.exists(state_p):
            fm, _ = split_frontmatter(open(state_p).read())
            if fm:
                head.append(f"Project: {fm.get('project', '?')} | state.md updated "
                            f"{fm.get('last_updated', '?')} | status {fm.get('status', '?')}")
        _, inbox_paths = collect_inbox(project_path)
        if inbox_paths:
            tail.append(f"{len(inbox_paths)} unprocessed curator inbox note(s).")
        # Layer in a bounded slice of cross-project (global) memory, but only
        # when this session belongs to a real project — not when it IS the
        # global root (which would duplicate its own content) or has no
        # global_root configured.
        gr = cfg.get("global_root")
        if gr:
            gr = os.path.realpath(gr)
            if gr != project_path:
                tail += global_slice(cfg, gr)
    # --max-chars hint in the omission notice: total size with nothing omitted
    full = len("\n\n".join(head + [h + "\n" + b for _, h, b, _ in sections]
                           + tail))

    def assemble(kept, omitted):
        parts = head + kept
        if omitted:
            parts.append(f"{omitted} more sections omitted — "
                         f"use --max-chars {full} to expand")
        return "\n\n".join(parts + tail)

    budget = cfg.get("brief_chars", 2500)
    kept, used, omitted = [], 0, 0
    for i, (_, heading, body, count) in enumerate(sections):
        if used + count > budget:
            # never truncate mid-section: drop this and all trailing sections
            omitted = len(sections) - i
            break
        kept.append(heading + "\n" + body)
        used += count
    out = assemble(kept, omitted)
    if max_chars is not None:
        # hard cap on TOTAL output: shed whole trailing sections first
        # (boundary-clean), raw-truncate only if the fixed parts alone exceed it
        while len(out) > max_chars and kept:
            kept.pop()
            omitted += 1
            out = assemble(kept, omitted)
        if len(out) > max_chars:
            out = out[:max_chars].rstrip()
    return out
