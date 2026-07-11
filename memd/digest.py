"""Transcript digestion: claude-code JSONL, antigravity SQLite/protobuf,
credential redaction, and per-source dispatch."""

import json
import os
import re

from memd.config import AG_INDEX_PATH, load_config, load_json, log, save_json

# --------------------------------------------------------------------------
# transcript digestion
# --------------------------------------------------------------------------


def _flatten(content, limit):
    if isinstance(content, str):
        return content[:limit]
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(str(b.get("text") or b.get("content") or ""))
            else:
                parts.append(str(b))
        return " ".join(parts)[:limit]
    return str(content)[:limit]


def digest_jsonl(path, offset):
    """Digest new entries of a claude-code transcript from byte offset.

    Returns (digest_text, new_offset)."""
    lines = []
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read()
            new_offset = f.tell()
    except OSError as e:
        # unchanged offset means the content replays next sweep — safe, but
        # the failure must be visible in the log, not silent
        log(f"digest error: {e}")
        return "", offset
    for rawline in raw.splitlines():
        try:
            e = json.loads(rawline)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if e.get("isSidechain") or e.get("isMeta"):
            continue
        etype = e.get("type")
        msg = e.get("message") or {}
        content = msg.get("content")
        if etype == "user":
            if isinstance(content, str):
                t = content.strip()
                if t and not t.startswith("<"):  # skip injected reminders
                    lines.append("U: " + t[:2500])
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        t = (b.get("text") or "").strip()
                        if t and not t.startswith("<"):
                            lines.append("U: " + t[:2500])
                    elif b.get("type") == "tool_result":
                        lines.append("R: " + _flatten(b.get("content"), 240))
        elif etype == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    t = (b.get("text") or "").strip()
                    if t:
                        lines.append("A: " + t[:2500])
                elif b.get("type") == "tool_use":
                    try:
                        inp = json.dumps(b.get("input", {}))[:300]
                    except (TypeError, ValueError):
                        inp = ""
                    lines.append(f"T: {b.get('name', '?')} {inp}")
    return "\n".join(lines), new_offset


# Sessions sometimes read credentials; never let them reach the curator.
# One raw regex per named pattern; names become regex group names (so: valid
# identifiers) and are what gets logged when a pattern fires — never the
# matched text. Order matters where prefixes overlap: anthropic_key (sk-ant-)
# must precede openai_key (sk-).
REDACT_PATTERNS = {
    # Google OAuth2 access token, ya29. prefix (developers.google.com/identity)
    "google_oauth": r"ya29\.[\w.\-]{20,}",
    # GitHub classic/app tokens ghp_/gho_/ghu_/ghs_/ghr_ and fine-grained
    # PATs github_pat_ (github.blog token-prefix format, 2021/2022)
    "github_pat": r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[\w]{20,}",
    # Anthropic API key, sk-ant-... (docs.anthropic.com)
    "anthropic_key": r"sk-ant-[\w\-]{20,}",
    # OpenAI API key, sk-... incl. sk-proj-... (platform.openai.com)
    "openai_key": r"sk-[\w\-]{20,}",
    # AWS access key ID, AKIA + 16 uppercase alnum (docs.aws.amazon.com IAM)
    "aws_access": r"AKIA[0-9A-Z]{16}",
    # Slack tokens xoxb/xoxa/xoxp/xoxs/xoxe (api.slack.com token types)
    "slack_token": r"xox[bapse]-[\w\-]{10,}",
    # GitLab personal access token, glpat- prefix (docs.gitlab.com)
    "gitlab_token": r"glpat-[\w\-]{20,}",
    # npm access token, npm_ prefix (docs.npmjs.com, GitHub secret scanning)
    "npm_token": r"npm_[A-Za-z0-9]{30,}",
    # JSON Web Token: three base64url segments; header always starts eyJ
    "jwt": r"eyJ[\w\-]{20,}\.[\w\-]{10,}\.[\w\-]{10,}",
    # OAuth token fields inside captured JSON bodies
    "json_token_field": r"\"(?:access|refresh|id)_token\"\s*:\s*\"[^\"]+\"",
    # HTTP Authorization header with a Bearer token (RFC 6750 token68 charset)
    "bearer_header": r"(?i:authorization)\s*:\s*(?i:bearer)\s+[A-Za-z0-9\-._~+/]{16,}=*",
    # .env / shell-export credential assignment: UPPERCASE name ending in a
    # credential suffix, non-trivial unquoted-token value. Anchored to line
    # start and suffix-gated to avoid plain "key=" false positives.
    "env_credential": r"(?m:^)(?:export\s+)?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)=[\"']?[^\s\"']{12,}",
    # PEM private key block (RSA/EC/OPENSSH/PKCS8). Replaced with
    # "[REDACTED KEY]"; compiled separately below because it spans lines.
    "ssh_private_key": r"(?s:-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----)",
    # NOTE: Azure storage/service keys omitted — no distinctive prefix (plain
    # base64), so any pattern is all false positives. Use REDACT_EXTRA_PATTERNS.
}

REDACT_RE = re.compile("|".join(
    f"(?P<{n}>{p})" for n, p in REDACT_PATTERNS.items()
    if n != "ssh_private_key"))
REDACT_PEM_RE = re.compile(REDACT_PATTERNS["ssh_private_key"])

_REDACT_EXTRA = None  # lazy: [(name, compiled)] from config REDACT_EXTRA_PATTERNS


def _redact_extras():
    """Per-instance additions from config key REDACT_EXTRA_PATTERNS (a list of
    raw regex strings), compiled once per process; broken regexes are skipped
    loudly rather than disabling redaction."""
    global _REDACT_EXTRA
    if _REDACT_EXTRA is None:
        _REDACT_EXTRA = []
        for i, pat in enumerate(load_config().get("REDACT_EXTRA_PATTERNS") or []):
            try:
                _REDACT_EXTRA.append((f"extra_{i}", re.compile(pat)))
            except re.error as e:
                log(f"bad regex in REDACT_EXTRA_PATTERNS[{i}]: {e}")
    return _REDACT_EXTRA


def redact(text):
    def sub(m):
        log(f"redacted credential: {m.lastgroup}")
        return "[REDACTED]"

    def sub_key(m):
        log("redacted credential: ssh_private_key")
        return "[REDACTED KEY]"

    text = REDACT_RE.sub(sub, text)
    text = REDACT_PEM_RE.sub(sub_key, text)
    for name, rx in _redact_extras():
        def sub_extra(m, _name=name):
            log(f"redacted credential: {_name}")
            return "[REDACTED]"
        text = rx.sub(sub_extra, text)
    return text


# --- antigravity-cli adapter ------------------------------------------------
# Conversations live in <antigravity_dir>/conversations/*.db (SQLite, one
# trajectory per file; `steps` rows hold protobuf payloads). There is no
# published schema, so text is extracted as printable-string runs — noisy but
# the curator model tolerates it. Legacy *.pb conversations are not parsed.

# step types observed on antigravity-cli as of 2026-07 — undocumented, may
# change with any update
AG_STEP_USER = 14
AG_STEP_ASSISTANT = 33
AG_STEP_TOOL = 15
AG_STEP_ERROR = 17
AG_KNOWN_STEP_TYPES = frozenset(
    {AG_STEP_USER, AG_STEP_ASSISTANT, AG_STEP_TOOL, AG_STEP_ERROR})

_STR_RUN = re.compile(rb"[\x20-\x7e\n\t]{12,}")


def _pb_strings(blob, minlen=12):
    """Printable-ASCII runs >= minlen from a protobuf blob. Heuristic, not a
    parser: wire framing can glue adjacent fields into one run or split a
    string containing non-ASCII bytes into several."""
    return [s for s in _STR_RUN.findall(blob or b"") if len(s) >= minlen]


def ag_conversations_dir(cfg):
    return os.path.join(os.path.expanduser(cfg["antigravity_dir"]), "conversations")


def _ag_connect(path):
    import sqlite3
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def ag_workspace(path):
    """Best-effort workspace dir for a conversation DB (file:// URIs in meta)."""
    import sqlite3  # lazy, like _ag_connect
    try:
        con = _ag_connect(path)
        blobs = [r[0] for r in con.execute(
            "select data from trajectory_metadata_blob")]
        blobs += [r[0] for r in con.execute(
            "select step_payload from steps order by idx limit 5")]
        con.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return None
    candidates = set()
    for blob in blobs:
        for m in re.finditer(rb"file://(/[A-Za-z0-9_\-./]+)", blob or b""):
            p = m.group(1).decode("utf-8", "replace")
            while p and p != "/" and not os.path.isdir(p):
                p = p[:-1]  # protobuf adjacency can glue trailing bytes on
            if p and p != "/":
                candidates.add(p)
    return max(candidates, key=len) if candidates else None


def ag_max_idx(path):
    try:
        con = _ag_connect(path)
        n = con.execute("select coalesce(max(idx), 0) from steps").fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0


def _ag_clean(s):
    t = s.decode("utf-8", "replace").strip().strip('"').strip()
    return re.sub(r"^[^\w#/<\[({`*-]+", "", t)


def digest_ag_db(path, last_idx):
    """Digest antigravity conversation steps with idx > last_idx.

    Returns (digest_text, new_last_idx)."""
    lines = []
    new_idx = last_idx
    try:
        con = _ag_connect(path)
        rows = list(con.execute(
            "select idx, step_type, step_payload from steps "
            "where idx > ? order by idx", (last_idx,)))
        con.close()
    except Exception as e:
        log(f"ag digest failed for {path}: {e}")
        return "", last_idx
    for idx, st, payload in rows:
        new_idx = max(new_idx, idx)
        if st not in AG_KNOWN_STEP_TYPES:
            log(f"unknown ag step type: {st}")
            continue
        strs = _pb_strings(payload)
        if not strs:
            continue
        if st == AG_STEP_USER:
            lines.append("U: " + _ag_clean(max(strs, key=len))[:2500])
        elif st == AG_STEP_ASSISTANT:
            lines.append("A: " + _ag_clean(max(strs, key=len))[:2500])
        elif st == AG_STEP_TOOL:  # json descriptor with toolAction/Summary
            for s in strs:
                t = s.decode("utf-8", "replace")
                start = t.find('{"')
                if start < 0:
                    continue
                try:
                    obj, _ = json.JSONDecoder().raw_decode(t[start:])
                    summary = obj.get("toolSummary") or obj.get("toolAction")
                    if summary:
                        lines.append("T: " + str(summary)[:200])
                        break
                except (json.JSONDecodeError, AttributeError):
                    continue
        elif st == AG_STEP_ERROR:
            lines.append("E: " + _ag_clean(max(strs, key=len))[:300])
    return "\n".join(lines), new_idx


def _ag_assign_project(db, roots):
    """Antigravity records the launch dir, not the project, as workspace —
    so attribute a conversation to the registered root its payloads
    reference most (>=3 mentions to avoid drive-by matches)."""
    hits = dict.fromkeys(roots, 0)
    try:
        con = _ag_connect(db)
        for (payload,) in con.execute("select step_payload from steps"):
            for r in roots:
                hits[r] += (payload or b"").count(r.encode())
        con.close()
    except Exception:
        return None
    best = max(hits, key=hits.get) if hits else None
    return best if best and hits[best] >= 3 else None


def project_ag_dbs(cfg, project_path):
    """Conversation DBs attributed to this project (cached in ag_index)."""
    import glob as _glob
    conv = ag_conversations_dir(cfg)
    if not os.path.isdir(conv):
        return []
    # Exclude the global root: its path ($HOME) is a substring of every home
    # path, so it would win _ag_assign_project's mention-count and steal every
    # conversation from the real (nested) project. Global is inbox-only anyway.
    roots = sorted(os.path.realpath(p) for p, e in cfg["projects"].items()
                   if not e.get("global"))
    index = load_json(AG_INDEX_PATH, {})
    changed = False
    out = []
    root = os.path.realpath(project_path)
    for db in _glob.glob(os.path.join(conv, "*.db")):
        e = index.get(db)
        if not isinstance(e, dict):
            e = {"project": "", "scanned_idx": -1}
        maxidx = ag_max_idx(db)
        # unattributed conversations get rescanned as they grow
        if not e["project"] and maxidx > e.get("scanned_idx", -1):
            proj = None
            ws = ag_workspace(db)
            if ws:
                inside = [r for r in roots
                          if ws == r or ws.startswith(r.rstrip("/") + "/")]
                proj = max(inside, key=len, default=None)
            if not proj:
                proj = _ag_assign_project(db, roots)
            e = {"project": proj or "", "scanned_idx": maxidx}
            index[db] = e
            changed = True
        if e["project"] == root:
            out.append(db)
    if changed:
        save_json(AG_INDEX_PATH, index)
    return sorted(out)


def digest_source(src, cursor):
    """Dispatch to the right digester. Returns (text, new_cursor)."""
    if src.endswith(".db"):
        return digest_ag_db(src, cursor)
    return digest_jsonl(src, cursor)


def baseline_cursor(src):
    return ag_max_idx(src) if src.endswith(".db") else os.path.getsize(src)


def cap_digest(text, cap):
    if len(text) <= cap:
        return text
    head = int(cap * 0.3)
    tail = cap - head
    return text[:head] + "\n[... digest truncated ...]\n" + text[-tail:]


def collapse_repeats(text):
    """Collapse runs of identical non-blank lines (agent retry loops)
    into one line plus a repeat marker."""
    out, prev, reps = [], None, 0
    for line in text.splitlines():
        if line == prev and line.strip():
            reps += 1
            continue
        if reps:
            out.append(f"[... line repeated {reps} more time(s) ...]")
            reps = 0
        out.append(line)
        prev = line
    if reps:
        out.append(f"[... line repeated {reps} more time(s) ...]")
    return "\n".join(out)

