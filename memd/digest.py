"""Transcript digestion: claude-code JSONL, antigravity SQLite/protobuf,
generic text logs, credential redaction, and per-source dispatch.

Every digester returns a Span: one session's worth of tagged lines plus the
metadata (source tool, session id, UTC start/end, cwd) the curator needs to
date events and merge parallel agent threads into one honest timeline."""

import json
import os
import re
import datetime as dt
from dataclasses import dataclass, field

from memd.config import AG_INDEX_PATH, load_config, load_json, log, save_json

# --------------------------------------------------------------------------
# transcript digestion
# --------------------------------------------------------------------------


@dataclass
class Span:
    source: str                      # "claude-code" | "antigravity" | "text-log"
    file: str
    session_id: str | None = None
    start: str | None = None         # ISO-8601 UTC; mtime-derived when approx
    end: str | None = None
    cwd: str | None = None
    lines: list[str] = field(default_factory=list)
    approx: bool = False             # True -> start/end are file-mtime guesses


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


# Tool-input keys worth surfacing verbatim, in sniff priority order; anything
# else falls back to a truncated JSON dump. Play-by-play inputs used to be fed
# whole (300 chars of raw JSON per call), which let file-editing sessions drown
# user intent out of the digest.
SALIENT_INPUT_KEYS = ("command", "file_path", "path", "notebook_path",
                      "pattern", "url", "query", "description", "topic",
                      "prompt")


def fmt_chars(n):
    """Char count for a size-aware placeholder: '18.2k' past a thousand, exact
    below it. The count is what tells the curator a result was huge; four extra
    digits of precision on 'huge' are noise."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _salient_input(inp):
    if isinstance(inp, dict):
        bits = [str(inp[k]).strip()[:80] for k in SALIENT_INPUT_KEYS
                if isinstance(inp.get(k), str) and inp[k].strip()]
        if bits:
            return " ".join(bits)[:160]
    try:
        return json.dumps(inp)[:160]
    except (TypeError, ValueError):
        return ""


def digest_jsonl(path, offset, sidechains=False):
    """Digest new entries of a claude-code transcript from byte offset.

    Returns (Span|None, new_offset). Sidechain (subagent) entries are skipped
    unless sidechains=True, in which case they are emitted as indented
    "  SUB> "-prefixed lines with tighter caps — delegated work belongs in
    memory, at secondary signal strength."""
    span = Span(source="claude-code", file=os.path.basename(path))
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read()
            new_offset = f.tell()
    except OSError as e:
        # unchanged offset means the content replays next sweep — safe, but
        # the failure must be visible in the log, not silent
        log(f"digest error: {e}")
        return None, offset
    for rawline in raw.splitlines():
        try:
            e = json.loads(rawline)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        ts = e.get("timestamp")
        if isinstance(ts, str):
            if span.start is None or ts < span.start:
                span.start = ts
            if span.end is None or ts > span.end:
                span.end = ts
        span.session_id = span.session_id or e.get("sessionId")
        span.cwd = span.cwd or e.get("cwd")
        if e.get("isMeta"):
            continue
        sub = ""
        if e.get("isSidechain"):
            if not sidechains:
                continue
            sub = "  SUB> "
        etype = e.get("type")
        msg = e.get("message") or {}
        content = msg.get("content")
        if etype == "user":
            if isinstance(content, str):
                t = content.strip()
                if t and not t.startswith("<"):  # skip injected reminders
                    span.lines.append(sub + "U: " + t[:800 if sub else 2500])
            elif isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        t = (b.get("text") or "").strip()
                        if t and not t.startswith("<"):
                            span.lines.append(
                                sub + "U: " + t[:800 if sub else 2500])
                    elif b.get("type") == "tool_result":
                        r = _flatten(b.get("content"), 1_000_000)
                        # size-aware placeholder instead of a 240-char echo:
                        # results are the bulk of any file-heavy session
                        if r.strip():
                            span.lines.append(
                                sub + f"R: [{fmt_chars(len(r))} ch] "
                                + r[:120 if sub else 80].strip())
        elif etype == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    t = (b.get("text") or "").strip()
                    if t:
                        span.lines.append(
                            sub + "A: " + t[:800 if sub else 2500])
                elif b.get("type") == "tool_use":
                    span.lines.append(sub + "T: " + (b.get("name") or "?")
                                      + " " + _salient_input(b.get("input", {})))
    return (span if span.lines else None), new_offset


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

    Returns (Span|None, new_last_idx). The DB stores no wall-clock per step,
    so start/end fall back to the file's mtime, marked approximate."""
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
        return None, last_idx
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
    if not lines:
        return None, new_idx
    span = Span(source="antigravity", file=os.path.basename(path),
                lines=lines, approx=True)
    try:
        iso = dt.datetime.fromtimestamp(
            os.path.getmtime(path), tz=dt.timezone.utc)
        stamp = iso.strftime("%Y-%m-%dT%H:%M:%SZ")
        span.start = span.end = stamp
    except OSError:
        pass
    return span, new_idx


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


def digest_source(cfg, src, cursor):
    """Dispatch to the right digester. Returns (Span|None, new_cursor).

    Dispatch is content-sniffed so ANY tool's transcript can ride
    extra_sources; the only config input today is capture_sidechains."""
    if src.endswith(".db"):
        return digest_ag_db(src, cursor)
    return sniff_and_digest(src, cursor,
                            sidechains=bool(cfg.get("capture_sidechains")))


# --- generic text-log adapter -----------------------------------------------
# The escape hatch that makes memd tool-agnostic: any CLI's session log can be
# pointed at via extra_sources and digested as best-effort prose. Role tagging
# is heuristic (common "user:"/"assistant:"/"tool" markers); untagged chunks
# pass through bare — the curator tolerates unattributed lines, and the
# alternative (refusing unknown formats) is how ingest-stall blindspots happen.

_ROLE_RE = re.compile(
    r"^(?:>\s*)?(?:(user|human)\b[:\s]?|(assistant|ai)\b[:\s]?|(tool)\b[:\s]?)",
    re.IGNORECASE)


# One pass reads at most this much of an arbitrary log. extra_sources points at
# files memd knows nothing about, and reading a multi-gigabyte one whole to
# digest it would be a memory fault of memd's own making. The remainder is not
# lost: the cursor stops where the read stopped and the tail rides next sweep.
TEXT_LOG_READ_CAP = 4_000_000

# Floor for "this decoded blob is plausibly prose". Undecodable bytes surface as
# U+FFFD and binary is dense with C0 controls, so both are counted against it.
TEXT_PRINTABLE_FLOOR = 0.85


def _mostly_text(s):
    sample = s[:8192]
    if not sample:
        return False
    bad = sum(1 for ch in sample
              if ch == "\ufffd" or (ch < " " and ch not in "\t\n\r"))
    return (len(sample) - bad) / len(sample) >= TEXT_PRINTABLE_FLOOR


def digest_text_log(path, offset):
    """Digest an arbitrary text log from byte offset into heuristic turns.

    Returns (Span|None, new_offset)."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(TEXT_LOG_READ_CAP)
            new_offset = f.tell()
    except OSError as e:
        log(f"digest error: {e}")
        return None, offset
    text = raw.decode("utf-8", "replace")
    # The text fallback is deliberately undemanding, but "any bytes at all" is
    # too undemanding: a glob that catches a database, an archive or an image
    # would otherwise ship mojibake to the curator, burn the tokens, and advance
    # the cursor as though something had been learned.
    if raw and not _mostly_text(text):
        log(f"skipped non-text source ({len(raw)} bytes read): {path}")
        return None, new_offset
    span = Span(source="text-log", file=os.path.basename(path),
                session_id=os.path.basename(path)[:12], approx=True)
    try:
        iso = dt.datetime.fromtimestamp(
            os.path.getmtime(path), tz=dt.timezone.utc)
        stamp = iso.strftime("%Y-%m-%dT%H:%M:%SZ")
        span.start = span.end = stamp
    except OSError:
        pass
    for chunk in re.split(r"\n\s*\n", text):  # blank-line paragraph split
        flat = " ".join(chunk.split())
        if not flat:
            continue
        m = _ROLE_RE.match(flat)
        tag = ""
        if m:
            tag = {m.group(1): "U: ", m.group(2): "A: ",
                   m.group(3): "T: "}.get(
                       next(g for g in m.groups() if g), "")
            flat = flat[m.end():].strip()
        if not flat:
            continue
        span.lines.append(tag + flat[:1200])
    return (span if span.lines else None), new_offset


def sniff_and_digest(path, cursor, sidechains=False):
    """Content-sniffing dispatch for non-.db sources: a first line that parses
    as JSON with claude-code's shape routes to the JSONL digester, anything
    else degrades to the generic text-log path instead of stalling."""
    probe = b""
    try:
        with open(path, "rb") as f:
            probe = f.read(4096)
    except OSError as e:
        log(f"digest error: {e}")
        return None, cursor
    for line in probe.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            break  # first real line isn't JSON -> not a JSONL transcript
        if isinstance(e, dict) and "type" in e \
                and ("message" in e or "timestamp" in e):
            return digest_jsonl(path, cursor, sidechains=sidechains)
        break
    return digest_text_log(path, cursor)


def baseline_cursor(src):
    return ag_max_idx(src) if src.endswith(".db") else os.path.getsize(src)


# --- span assembly: render, order, budget ------------------------------------


def _fmt_ts(ts):
    """'2026-08-24T14:32:11.123Z' -> '2026-08-24 14:32' (display form)."""
    return ts[:16].replace("T", " ") if ts else None


def render_span(span):
    """Header + tagged lines. The header is what lets the curator date events
    and what truncation markers quote when spans are dropped for budget."""
    parts = [f"source={span.source}"]
    if span.session_id:
        parts.append(f"session={span.session_id[:12]}")
    mark = "≈ " if span.approx else ""
    t0, t1 = _fmt_ts(span.start), _fmt_ts(span.end)
    if t0 and t1:
        parts.append(f"{mark}{t0}→{t1} UTC")
    elif t0 or t1:
        parts.append(f"{mark}{t0 or t1} UTC")
    else:
        parts.append("undated")
    if span.cwd:
        parts.append(f"cwd={span.cwd}")
    return "--- session span | " + " | ".join(parts) + " ---" \
        + "\n" + "\n".join(span.lines)


def sort_spans(spans):
    """Chronological ascending; undated spans sort last, stable otherwise."""
    return sorted(spans, key=lambda s: (s.start or "9999")[:19])


def score_span(span):
    """Signal score for budget triage: user intent outweighs everything, then
    errors; assistant volume counts once, bulk adds a bounded bonus. Purely
    structural — no NLP, deterministic across runs."""
    u = sum(1 for ln in span.lines if ln.lstrip().startswith("U: "))
    e = sum(1 for ln in span.lines if ln.lstrip().startswith("E: "))
    a = sum(1 for ln in span.lines if ln.lstrip().startswith("A: "))
    chars = sum(len(ln) for ln in span.lines)
    return 3 * u + 2 * e + a + min(chars // 4000, 3)


def truncate_spans(spans, cap):
    """Budget triage that drops WHOLE low-signal spans instead of silently
    eliding the middle of the timeline.

    Returns (kept_spans, omitted_labels). kept preserves input order. The
    newest span is never dropped (most recent context wins ties with
    everything). Labels describe each dropped span so the curator at least
    knows THAT something happened, and WHEN. If nothing is droppable (one
    oversize span), kept still exceeds cap — the caller applies cap_digest()'s
    head/tail elision to the rendered join as the last resort."""
    texts = [(s, render_span(s)) for s in spans]
    if sum(len(t) for _, t in texts) <= cap or not spans:
        return list(spans), []
    kept_idx = list(range(len(spans)))
    newest = max(range(len(spans)), key=lambda i: (spans[i].start or "")[:19])

    def total(idxs):
        return sum(len(texts[i][1]) for i in idxs)

    omitted = []
    while total(kept_idx) > cap and len(kept_idx) > 1:
        droppable = [i for i in kept_idx if i != newest]
        # len(kept_idx) > 1 guarantees droppable is non-empty
        drop = min(droppable, key=lambda i: (
            score_span(spans[i]), len(texts[i][1]), texts[i][1]))
        s = spans[drop]
        mark = "≈ " if s.approx else ""
        when = (_fmt_ts(s.start) or "undated") + (
            "→" + _fmt_ts(s.end) if s.end and s.start else "")
        omitted.append(f"{when} UTC ({s.source}, dropped: low signal)")
        kept_idx.remove(drop)
    labels = [f"[... omitted: {len(omitted)} low-signal span(s): "
              + " ; ".join(omitted) + " ...]"] if omitted else []
    return [spans[i] for i in kept_idx], labels


def cap_digest(text, cap):
    """Last-resort single-text elision (head/tail) for a span too large to
    keep even alone — whole-span dropping has nothing left to sacrifice."""
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

