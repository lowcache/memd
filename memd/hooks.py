"""claude-code hook entries, detached self-invocation, and hook
installation."""

import json
import os
import subprocess
import sys

from memd.config import (CLAUDE_SETTINGS, LOG_PATH, STATE_DIR, git_toplevel,
                         load_json, log, save_json)
from memd.memory import find_project, make_brief, register, scaffold
from memd.sweep import ensure_global

# --------------------------------------------------------------------------
# hooks (claude-code)
# --------------------------------------------------------------------------


def self_invocation():
    """Command used to re-invoke memd in detached children and hooks.

    argv[0] is the entry script (memd.py shim or installed bin/memd);
    __file__ would point inside the package, which is not runnable."""
    return [sys.executable or "python3", os.path.abspath(sys.argv[0])]


def detach(args):
    os.makedirs(STATE_DIR, exist_ok=True)
    logf = open(LOG_PATH, "a")
    subprocess.Popen(self_invocation() + args, stdout=logf, stderr=logf,
                     stdin=subprocess.DEVNULL, start_new_session=True)


def cmd_hook(cfg, event):
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    try:
        return _run_hook(cfg, event, payload)
    except (RuntimeError, OSError) as e:
        # hooks run headless inside claude-code: a silent failure here means
        # briefs/distills just stop happening, so make it loud in the log
        log(f"hook {event} failed: {e}")
        return 1


def _run_hook(cfg, event, payload):
    cwd = payload.get("cwd") or os.getcwd()
    transcript = payload.get("transcript_path")
    ensure_global(cfg)
    root = find_project(cfg, cwd)

    if event == "session-start":
        if root is None:
            top = git_toplevel(cwd)
            gr = cfg.get("global_root")
            # Don't auto-scaffold the global root as a normal project (would
            # write a .model/ stub into $HOME and shadow the global entry) — this
            # matters only if $HOME is ever itself a git repo.
            if (top and cfg["auto_scaffold"] and top not in cfg["exclude"]
                    and (not gr or top != os.path.realpath(gr))):
                scaffold(top)
                register(cfg, top)
                root = top
                log(f"auto-scaffolded memory for {top}")
        if root is None:
            # No project: surface the global memory brief (read-only). Global
            # memory is fed by its inbox (e.g. the noctalia `remember` tool) and
            # distilled by the sweep, not by auto-distilling every stray session.
            root = cfg.get("global_root")
        if root:
            brief = make_brief(cfg, root)
            if brief:
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": brief,
                }}))
        return 0

    if event in ("session-end", "pre-compact"):
        if root is None:
            return 0
        args = ["sync", "--project", root, "--trigger", event]
        if transcript:
            args += ["--transcript", transcript]
        detach(args)
        return 0
    return 0


HOOK_DEFS = {
    "SessionStart": "hook session-start",
    "SessionEnd": "hook session-end",
    "PreCompact": "hook pre-compact",
}


def cmd_install_hooks():
    settings = load_json(CLAUDE_SETTINGS, {})
    hooks = settings.setdefault("hooks", {})
    changed = False
    for event, sub in HOOK_DEFS.items():
        cmdstr = f"memd {sub}"
        entries = hooks.setdefault(event, [])
        present = any(
            cmdstr in h.get("command", "")
            for e in entries for h in e.get("hooks", [])
        )
        if not present:
            entries.append({"hooks": [{"type": "command", "command": cmdstr}]})
            changed = True
    if changed:
        save_json(CLAUDE_SETTINGS, settings)
        print(f"hooks installed into {CLAUDE_SETTINGS}")
    else:
        print("hooks already installed")
