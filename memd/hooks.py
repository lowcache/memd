"""claude-code hook entries, detached self-invocation, and hook
installation."""

import json
import shutil
import os
import subprocess
import sys

from memd.config import (CLAUDE_SETTINGS, LOG_PATH, STATE_DIR, git_toplevel,
                         load_json, log, save_json)
from memd.memory import find_project, make_brief, provision_project
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
            # write instruction files into $HOME and shadow the global entry)
            # — this matters only if $HOME is ever itself a git repo.
            if (top and cfg["auto_scaffold"] and top not in cfg["exclude"]
                    and (not gr or top != os.path.realpath(gr))):
                # provision_project(), not bare scaffold(): an auto-discovered
                # project must get its own .memory repo too, or its memory gets
                # committed into the user's project history.
                provision_project(cfg, top)
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


def hook_command_prefix():
    """The command claude-code should run for a hook.

    A bare "memd" only works if memd happens to be on the PATH claude-code was
    started with — which it is not for a venv or pipx install invoked from a
    different shell. Resolve the running executable instead, so the hooks keep
    working regardless of how memd was installed. Falls back to "memd" only if
    the path cannot be determined."""
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if argv0 and os.path.exists(argv0) and not argv0.endswith(".py"):
        return argv0
    found = shutil.which("memd")
    if found:
        return found
    if argv0 and os.path.exists(argv0):
        # running from a source checkout (memd.py shim): keep the interpreter
        # that is running now, or the shim would be executed by whatever
        # python3 happens to be first on claude-code's PATH.
        return f"{sys.executable or 'python3'} {argv0}"
    return "memd"


def cmd_install_hooks():
    settings = load_json(CLAUDE_SETTINGS, {})
    hooks = settings.setdefault("hooks", {})
    prefix = hook_command_prefix()
    changed = False
    for event, sub in HOOK_DEFS.items():
        cmdstr = f"{prefix} {sub}"
        entries = hooks.setdefault(event, [])
        # Match on the subcommand, not the full string: an install that predates
        # absolute-path resolution wrote a bare "memd hook session-start", and
        # comparing whole commands would not recognise it and would append a
        # second, duplicate hook.
        present = any(
            h.get("command", "").rstrip().endswith(sub)
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
