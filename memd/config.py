"""Config loading, defaults, path constants, and shared low-level utilities
(logging, atomic JSON, locked registry updates)."""

import datetime as dt
import fcntl
import json
import os
import subprocess

HOME = os.path.expanduser("~")
XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
XDG_STATE_HOME = os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state")
CONFIG_DIR = os.path.join(XDG_CONFIG_HOME, "memd")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_DIR = os.path.join(XDG_STATE_HOME, "memd")
CURSORS_PATH = os.path.join(STATE_DIR, "cursors.json")
AG_INDEX_PATH = os.path.join(STATE_DIR, "ag_index.json")
META_PATH = os.path.join(STATE_DIR, "meta.json")
LOCK_DIR = os.path.join(STATE_DIR, "locks")
STATE_LOCK_PATH = os.path.join(STATE_DIR, "state.lock")
LOG_PATH = os.path.join(STATE_DIR, "memd.log")
CLAUDE_PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
CLAUDE_SETTINGS = os.path.join(HOME, ".claude", "settings.json")

MEMORY_FILES = ("state.md", "decisions.md", "mistakes.md", "todo.md")

DEFAULT_CONFIG = {
    "claude_bin": "claude",
    "curator_cmd": [],              # override distill backend: argv list, prompt on
                                    # stdin, "{model}" substituted; output must
                                    # contain one JSON object (fences/prose ok).
                                    # Empty -> claude_bin headless invocation.
    "antigravity_dir": os.path.join(HOME, ".gemini", "antigravity-cli"),
    "model_small": "haiku",
    "model_large": "sonnet",
    "escalate_chars": 15000,        # session-end digests above this go to model_large
    "digest_cap_chars": 60000,      # max transcript digest fed to the model
    "quiet_seconds": 600,           # sweep skips transcripts modified more recently
    "sweep_jobs": 4,                # parallel project syncs per sweep (threads;
                                    # per-project flock still serializes any
                                    # single project). CLI --jobs overrides.
    "auto_scaffold": True,          # scaffold .memory/ in detected git-root projects
    "git_commit": True,             # commit .memory/ changes after each distill
    "memory_own_repo": True,        # use a standalone git repo for .memory/ to decouple history
    "budgets": {                    # active-file size budgets (chars of body)
        "state.md": 10000,
        "decisions.md": 12000,
        "todo.md": 10000,
        "mistakes.md": 22000,
    },
    "REDACT_EXTRA_PATTERNS": [],    # extra raw regex strings redacted from
                                    # digests/inbox notes on top of the
                                    # built-in REDACT_PATTERNS
    "exclude": [],                  # absolute paths never auto-managed
    "projects": {},                 # path -> {"name": str, "extra_sources": [globs]}
    "global_root": HOME,            # project path whose .memory holds system/user/
                                    # cross-project truth (store: ~/.memory). The
                                    # fallback when a session belongs to no project.
    "global_brief_chars": 800,      # cap for the global state.md excerpt layered
                                    # into a project session's brief. 0 = pointer
                                    # only (no excerpt); <0 = omit the global slice.
    "brief_chars": 2500,            # budget for the brief's content sections
                                    # (todos / decisions / state / mistakes) —
                                    # the fixed BRIEF_NOTE + metadata overhead
                                    # (~300 chars) is not counted against it.
    "brief_decisions_days": 30,     # recency window for decisions.md sections
                                    # surfaced in the brief.
}

# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------


LOG_BACKUPS = 3  # rotated copies kept: memd.log.1 (newest) .. memd.log.3


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            for i in range(LOG_BACKUPS - 1, 0, -1):
                if os.path.exists(f"{LOG_PATH}.{i}"):
                    os.replace(f"{LOG_PATH}.{i}", f"{LOG_PATH}.{i + 1}")
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except OSError:
        pass
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a") as f:
        f.write(f"{stamp} {msg}\n")


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def update_json(path, mutate):
    """Read-modify-write a shared registry JSON file (cursors, meta) under a
    process-wide lock, so concurrent project syncs can't lose each other's
    updates. The on-disk value is re-read inside the lock and only the caller's
    delta is merged in — never a stale in-memory snapshot. The lock is held only
    for the merge, never across a curator call."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = open(STATE_LOCK_PATH, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = load_json(path, {})
        mutate(data)
        save_json(path, data)
        return data
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    on_disk = load_json(CONFIG_PATH, {})
    for k, v in on_disk.items():
        if k == "budgets":
            cfg["budgets"] = {**DEFAULT_CONFIG["budgets"], **v}
        else:
            cfg[k] = v
    return cfg


def save_config(cfg):
    save_json(CONFIG_PATH, cfg)


def git_toplevel(path):
    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None

def today():
    return dt.date.today().isoformat()
