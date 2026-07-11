"""memd — agent-driven project memory curator.

Maintains ./.memory/{state,decisions,mistakes,todo}.md (+ archive/, inbox/)
across projects, CLIs (claude-code, antigravity, others), and agent swarms.

Triggers:
  - claude-code hooks: SessionStart (context brief), SessionEnd / PreCompact
    (background distill of the session transcript)
  - systemd user timer: `memd sweep` catches missed sessions, inbox notes
    from other CLIs/agents, pruning, and auto-detects new projects.

Distillation brain: headless `claude -p` (haiku by default, sonnet for large
end-of-session distills) with a curator charter prompt. memd itself enforces
the invariants the model must not be trusted with: frontmatter, append-only
mistakes.md, shrink guard, size budgets, archive overflow, git audit commits.
"""

__version__ = "0.2.0"

from memd.errors import ConfigError, CuratorError, DigestError, MemdError
from memd.config import (
    AG_INDEX_PATH,
    CLAUDE_PROJECTS_DIR,
    CLAUDE_SETTINGS,
    CONFIG_DIR,
    CONFIG_PATH,
    CURSORS_PATH,
    DEFAULT_CONFIG,
    HOME,
    LOCK_DIR,
    LOG_PATH,
    MEMORY_FILES,
    META_PATH,
    STATE_DIR,
    STATE_LOCK_PATH,
    XDG_CONFIG_HOME,
    XDG_STATE_HOME,
    atomic_write,
    git_toplevel,
    load_config,
    load_json,
    log,
    save_config,
    save_json,
    today,
    update_json,
)
from memd.memory import (
    BRIEF_NOTE,
    FM_RE,
    MODEL_STUB,
    SKELETONS,
    append_archive,
    archive_path,
    enforce_budget_mistakes,
    find_project,
    git_commit_memory,
    global_slice,
    make_brief,
    read_memory,
    register,
    render_frontmatter,
    scaffold,
    split_frontmatter,
    write_memory_file,
)
from memd.digest import (
    AG_KNOWN_STEP_TYPES,
    AG_STEP_ASSISTANT,
    AG_STEP_ERROR,
    AG_STEP_TOOL,
    AG_STEP_USER,
    REDACT_PATTERNS,
    REDACT_PEM_RE,
    REDACT_RE,
    _pb_strings,
    ag_conversations_dir,
    ag_max_idx,
    ag_workspace,
    baseline_cursor,
    cap_digest,
    digest_ag_db,
    digest_jsonl,
    digest_source,
    project_ag_dbs,
    redact,
)
from memd.inbox import collect_inbox, write_inbox_note
from memd.curator import CHARTER, build_prompt, call_curator, validate
from memd.sync import (
    encode_claude_dir,
    project_lock,
    source_pending,
    sync_project,
    transcript_files,
)
from memd.sweep import (
    cmd_sweep,
    detect_new_projects,
    ensure_global,
    write_global_index,
)
from memd.hooks import (
    HOOK_DEFS,
    cmd_hook,
    cmd_install_hooks,
    detach,
    self_invocation,
)
from memd.cli import cmd_note, cmd_status, main
