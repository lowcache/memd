"""CLI argument parsing and command dispatch."""

import argparse
import os
import shutil
import sys

from memd import __doc__, __version__
from memd.config import (CURSORS_PATH, META_PATH, git_toplevel, load_config,
                         load_json, save_config, update_json)
from memd.digest import baseline_cursor
from memd.hooks import cmd_hook, cmd_install_hooks
from memd.inbox import collect_inbox, write_inbox_note
from memd.memory import (find_project, make_brief, register, scaffold,
                         init_memory_repo, main_worktree_root, migrate_parent_untrack,
                         memory_checkout_clean)
from memd.sweep import cmd_sweep, ensure_global
from memd.sync import source_pending, sync_project, transcript_files

# --------------------------------------------------------------------------
# status / CLI plumbing
# --------------------------------------------------------------------------


def cmd_status(cfg):
    ensure_global(cfg)
    meta = load_json(META_PATH, {})
    cursors = load_json(CURSORS_PATH, {})
    if not cfg["projects"]:
        print("no projects registered (run: memd init [path])")
        return
    for path, entry in sorted(cfg["projects"].items()):
        m = meta.get(path, {})
        srcs = transcript_files(cfg, path)
        pending = [s for s in srcs if source_pending(s, cursors)]
        ag = sum(1 for s in srcs if s.endswith(".db"))
        inbox = len(collect_inbox(path)[1])
        print(f"{entry['name']}  ({path})")
        print(f"  last distill : {m.get('last_sync', 'never')}"
              + (f"  [{m.get('trigger')}/{m.get('model')}]" if m else ""))
        if m.get("summary"):
            print(f"  summary      : {m['summary']}")
        print(f"  sources      : {len(srcs)} ({ag} antigravity), "
              f"{len(pending)} with pending content, {inbox} inbox note(s)")


def cmd_note(cfg, args):
    text = args.message if args.message is not None else sys.stdin.read()
    text = text.strip()
    if not text:
        print("empty note; nothing written", file=sys.stderr)
        return 2
    if args.is_global:
        root = ensure_global(cfg)
        if not root:
            print("no global_root configured", file=sys.stderr)
            return 2
    else:
        path = os.path.realpath(args.project)
        root = find_project(cfg, path) or git_toplevel(path) or path
    print(write_inbox_note(os.path.realpath(root), text, source=args.source))
    return 0


def main():
    ap = argparse.ArgumentParser(prog="memd", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"memd {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="scaffold .memory/ and register a project")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--name")
    p.add_argument("--global", dest="is_global", action="store_true",
                   help="seed the global memory root (~/.memory) instead of a project")

    p = sub.add_parser("sync", help="distill new session content into memory")
    p.add_argument("--project", default=".")
    p.add_argument("--transcript")
    p.add_argument("--trigger", default="manual")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("sweep",
                       help="timer entry: catch up all projects, prune, detect")
    p.add_argument("--jobs", type=int,
                   help="parallel project syncs (default: config sweep_jobs, 4)")
    sub.add_parser("status", help="show registry, backlog, last distills")
    sub.add_parser("install-hooks", help="wire memd into ~/.claude/settings.json")

    p = sub.add_parser("brief", help="print session-start memory brief")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--max-chars", type=int,
                   help="hard cap on total brief output for this run")
    p.add_argument("--topic",
                   help="only content sections mentioning this keyword "
                        "(case-insensitive)")

    p = sub.add_parser("hook", help="claude-code hook entry (reads JSON on stdin)")
    p.add_argument("event", choices=["session-start", "session-end", "pre-compact"])

    p = sub.add_parser("note", help="append a collision-safe note to a curator inbox")
    p.add_argument("--project", default=".")
    p.add_argument("--global", dest="is_global", action="store_true",
                   help="write to the global memory inbox instead of a project")
    p.add_argument("-m", "--message", help="note text (default: read from stdin)")
    p.add_argument("--source", help="optional writer label recorded in the note")

    p = sub.add_parser("exclude", help="never auto-manage a path")
    p.add_argument("path")

    args = ap.parse_args()
    cfg = load_config()

    if args.cmd == "init" and getattr(args, "is_global", False):
        gr = ensure_global(cfg)
        print(f"global memory root ready at {os.path.join(gr, '.memory')}" if gr
              else "no global_root configured")
    elif args.cmd == "init" and os.path.realpath(args.path) == os.path.realpath(cfg.get("global_root") or "\0"):
        # Don't let a bare `memd init ~` register the global root as a normal
        # project (model stub in $HOME, lost "global" flag). Route to --global.
        print("that path is the global memory root; use: memd init --global",
              file=sys.stderr)
        sys.exit(2)  # config error
    elif args.cmd == "init":
        path = os.path.realpath(args.path)
        mem = os.path.join(path, ".memory")
        mem_pre = os.path.exists(mem)  # real .memory here before we scaffold?
        # A pre-existing .memory is safe to relink only if it is a clean, tracked
        # checkout (content already in git); check before scaffold dirties it.
        mem_pre_clean = mem_pre and memory_checkout_clean(path)
        created = scaffold(path, args.name)

        if cfg.get("memory_own_repo") and cfg.get("git_commit"):
            migrate_parent_untrack(path)
            main_root = main_worktree_root(path)
            if main_root != path:
                # Linked worktree: share the main worktree's single store via a
                # symlink so worktrees can't diverge — but never destroy unsaved
                # memory (memd must not lose memory).
                main_mem = os.path.join(main_root, ".memory")
                created.extend(scaffold(main_root, name=args.name))
                init_memory_repo(main_mem, "memd: seed project memory root")
                if os.path.islink(mem):
                    pass  # already linked (idempotent)
                elif (not mem_pre) or mem_pre_clean:
                    # Only the templates we just scaffolded, or a clean checkout
                    # whose content already lives in git and the shared store.
                    shutil.rmtree(mem)
                    os.symlink(main_mem, mem)
                else:
                    print(f"warning: {mem} holds unsaved memory; not replacing it "
                          f"with a symlink to {main_mem} (resolve manually)", file=sys.stderr)
            else:
                init_memory_repo(mem, "memd: seed project memory root")

        register(cfg, path, args.name)
        # Baseline: start memory from now. Pre-existing transcripts are
        # presumed covered by existing memory (or not worth back-filling).
        skipped = 0
        def _baseline(cursors):
            nonlocal skipped
            for src in transcript_files(cfg, path):
                if src not in cursors:
                    cursors[src] = baseline_cursor(src)
                    skipped += 1
        update_json(CURSORS_PATH, _baseline)
        print(f"registered {path}"
              + (f", created {len(created)} file(s)" if created else "")
              + (f", baselined {skipped} existing transcript(s)" if skipped else ""))
    elif args.cmd == "sync":
        ok = sync_project(cfg, args.project, trigger=args.trigger,
                          transcript=args.transcript, dry_run=args.dry_run)
        # exit contract: 0 = ok/no-op, 3 = curator/distill failure
        sys.exit(3 if ok is False else 0)
    elif args.cmd == "sweep":
        # 1 = known error (failed distills)
        sys.exit(1 if cmd_sweep(cfg, jobs=args.jobs) else 0)
    elif args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "install-hooks":
        cmd_install_hooks()
    elif args.cmd == "brief":
        brief = make_brief(cfg, args.path, max_chars=args.max_chars,
                           topic=args.topic)
        print(brief or "(no .memory directory here)")
    elif args.cmd == "note":
        sys.exit(cmd_note(cfg, args))
    elif args.cmd == "hook":
        sys.exit(cmd_hook(cfg, args.event))
    elif args.cmd == "exclude":
        path = os.path.realpath(args.path)
        if path not in cfg["exclude"]:
            cfg["exclude"].append(path)
        cfg["projects"].pop(path, None)
        save_config(cfg)
        print(f"excluded {path}")

