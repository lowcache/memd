This document covers the memd curator protocol as the stable 1.0 contract.

## Backends

An empty `curator_cmd` selects the default `claude` headless backend through
`claude_bin`. memd uses a single completion turn with JSON output. It supplies
the charter as the system prompt and requests a lean session without tools,
MCP servers, user settings, skills or saved transcripts. If a failing invocation
reports `unknown option` on stderr, memd immediately retries without the lean
flags and sends the charter with the prompt on stdin. This compatibility
fallback uses the full session context. If executable lookup fails, memd also
checks `~/.local/bin/claude`.

A nonempty `curator_cmd` is an argv array executed without a shell. Every
occurrence of `{model}` in each argument is replaced with the selected model.
The custom backend receives the charter, two newlines, and the assembled prompt
on stdin. Both backends run in memd's state directory with `CLAUDECODE` removed
from the child environment.

The selected model is `model_small` unless the processed transcript digest
exceeds `escalate_chars`, in which case it is `model_large`. This applies to
every sync trigger. Inbox notes and existing memory do not count toward this
threshold. Both tiers default to `sonnet`.

## Transport

The assembled prompt is text on stdin. Each subprocess invocation has a timeout
of 600 seconds and captures stdout and stderr. The default lean invocation
receives the charter separately as described above.

A call gets at most three attempts, with 5s then 15s backoff after failures.
Timeouts, nonzero exits, invocation OSErrors other than a missing executable,
and unparseable output raise retryable `CuratorError`. A missing executable
raises `ConfigError` and fails immediately. Validation runs after the call has
returned and is not retried. The default-backend compatibility fallback is
additional to this attempt loop.

Stdout parsing proceeds in this order:

1. Parse the entire stdout as JSON. For an object whose `result` value is a
   string, extract that string for further parsing. Otherwise, a JSON object
   is returned directly as the curator result.
2. If whole-output parsing fails or produces a non-object, use the raw stdout
   as text instead.
3. Strip an opening plain or JSON markdown fence and a closing fence at the
   text boundaries. Find the first `{` and decode one JSON value from there.
   Trailing prose is ignored. Embedded raw control characters in strings are
   tolerated by this decoder. Missing braces or invalid JSON fail the attempt;
   malformed escapes and trailing commas are not repaired.

The extracted value must still pass object and protocol validation. Parsing
valid JSON does not imply the output satisfies the schema.

## The prompt

The charter defines file purposes, signal selection, pruning discipline,
style, partial-view rules and the output schema. In a custom invocation it
precedes the assembled prompt; the lean default passes it as a system prompt.
The assembled prompt has these parts in order:

1. Project name and root, current date, and the body-character budgets line.
2. An `OVER BUDGET` block when state, decisions or todo exceed their budgets.
   Each line gives current size, budget, and up to three oldest dated archive
   candidates. Undated sections are never candidates.
3. Current `state.md`, then `decisions.md`.
4. `mistakes.md` as a `### ` heading index plus the newest entry as a style
   exemplar. Older headings are shed toward a 3,000-character block cap. An
   oversized remaining exemplar is truncated with a marker.
5. Current `todo.md`.
6. Inbox notes, when present, under `CURATOR INBOX`.
7. Up to five successful previous-distill journal summaries, oldest first,
   when present. These ground the rule against reapplying earlier pruning.
8. The session digest, or a no-new-transcript-content notice.
9. The request to produce the JSON object.

A partial view applies when the combined body lengths of `state.md`,
`decisions.md` and `todo.md` exceed `curator_view_chars`. All three then carry
the `PARTIAL VIEW` marker. Every h2/h3 section heading and each file's preamble
remain visible. Hidden bodies become `[body not shown: N chars]`.

Section candidates first come from relevance ranking against the digest and
inbox notes. Sections dated in the last 14 days that were not already ranked
are then added, newest first. A section's date is its newest ISO date in its
heading or body. Candidates count heading and body lengths against the view-character budget;
the first candidate is always admitted even if oversized. Recency makes a
section eligible, not guaranteed to fit. Finally, up to three oldest dated
archive candidates per over-budget file are shown regardless of that budget.

Shown sections over 2,500 characters carry an `[oversized section: ...]`
marker asking the curator to split a replacement into focused `### ` sections.
This marker applies when rendering partial views; it does not itself reject
output or force a split.

## Output schema

Return one JSON object. The advertised keys are:

| Key | Shape | Meaning |
| --- | --- | --- |
| `summary` | string | One-line description of the memory change. A missing or non-string value normalizes to an empty string. |
| `ops` | array of objects | Section operations with `op`, `file`, and the operation-specific `heading` and `body` fields below. Missing or falsy values normalize to an empty array; a truthy non-array is an error. |
| `mistakes_new_entries` | array of strings | New audit entries, conventionally beginning with `### YYYY-MM-DD — title`, followed by symptom, cause and prevention. Missing or falsy values normalize to an empty array; truthy non-arrays or non-string entries are errors. Heading/date format is not validated. |
| `archive_entries` | array of `{"source": "<file>", "content": "<verbatim section>"}` | Sections to preserve in the monthly archive. A missing or null value is an empty array. A plain string element is accepted as `{"source": "?", "content": <string>}`. Any other element, an element without string `content`, or a non-array value is an error. |

Unknown top-level keys and unknown op fields are ignored.

```json
{
  "summary": "Record the verified API port change",
  "ops": [
    {
      "op": "replace_section",
      "file": "state.md",
      "heading": "## Services",
      "body": "- API: localhost:9090."
    }
  ],
  "mistakes_new_entries": [],
  "archive_entries": []
}
```

## Ops

The supported target files are `state.md`, `decisions.md` and `todo.md`.
`op` and `file` are required on every operation. Headings match complete h2/h3
lines, including their prefix and inline text, after the supplied heading's
outer whitespace is stripped. A section ends at the next h2/h3 heading;
deleting an h2 does not implicitly delete following h3 sections. Operations
compose left to right within each file.

Bodies have outer whitespace stripped. Boundary markdown fences tagged `md`
or `markdown` (or untagged) are removed, and recognized YAML frontmatter with
nonempty metadata is stripped. memd manages file frontmatter on write.
Touched sections receive canonical blank lines; untouched sections preserve
their text.

### `replace_section`

Requires string `heading` and string `body`, plus `op` and `file`. Replaces the
first section whose heading matches exactly. Missing headings skip this op;
with duplicated existing headings, only the first is replaced.

An empty or whitespace-only replacement is converted to `delete_section`
before wrapper stripping. Otherwise, if the cleaned body's first line begins
with a nonempty h2/h3 heading, that line becomes the replacement heading and
renames the section. It is not left nested under the old heading. A new heading
that duplicates another section is not separately rejected.

### `append_section`

Requires string `heading` and string `body`, plus `op` and `file`. Appends a
section at the end of the file. A heading lacking an h2/h3 prefix receives
`## `. If that heading already exists, this op is skipped. An empty body is
accepted. Partial views allow appends even when other sections are hidden;
existing hidden headings still prevent duplicates.

### `delete_section`

Requires string `heading`, plus `op` and `file`; `body` is not required and is
ignored. Removes the first matching heading and its body. A missing heading
skips the op. With duplicated headings, only the first match is removed.

### `set_body`

Requires string `body`, plus `op` and `file`; `heading` is not required and is
ignored. Replaces the whole memory body. A body equal to the file's original
scaffold skeleton is dropped as no change. It is dropped on a partial-view
file and otherwise remains subject to the shrink guard.

`mistakes.md` is append-only through `mistakes_new_entries`. Section ops on it
are validation errors. There is one compatibility exception: validation admits
`set_body` targeting `mistakes.md`, but sync ignores that write. It cannot
rewrite the audit log; use only the three supported target files above.

## Validation and guards

The following failures reject the whole distill with `CuratorError`:

- The result is not an object.
- A non-null legacy `state_body`, `decisions_body` or `todo_body` key is present.
  These full-body keys were removed in 0.6.0; use `set_body` in `ops` instead.
- `ops` is a truthy non-list, an op is not an object, or its name or target file
  is unknown.
- A section op targets `mistakes.md`.
- An op requiring `heading` or `body` has a missing or non-string field.
- The shrink guard trips on a whole-file replacement or the cumulative result
  of section ops for a file.
- `mistakes_new_entries` is a truthy non-list or contains a non-string.
- `archive_entries` is not an array, or an element is neither a string nor an
  object with string `content`.

Shape checks happen before partial-view drops. Thus a malformed op can fail
validation even when it would have targeted a hidden section.

A file may not drop below 40% of its previous body size unless archive entries
account for the loss: new body length plus the total archive content length
must reach that floor. Files under 800 characters are exempt; exactly 800
characters is also exempt because the guard applies only above that size.
Archive content is summed across all sources and credited to each checked
file; memd does not verify that it matches the removed text. Both
individual `set_body` ops and the composed output of section ops are guarded.

The following ops are dropped individually, allowing other valid ops to apply:

- On a partial file, `replace_section` or `delete_section` targeting a heading
  whose body was hidden, and every `set_body` op.
- A `set_body` that echoes the scaffold skeleton.
- During application, replace/delete with a missing exact heading, or append
  with an already-existing heading. These skips are logged and counted in the
  distill journal.

A bad shape or shrink violation fails the whole distill; an unresolved heading
is skipped. A successful distill can therefore apply some ops and skip others,
then advance cursors and remove consumed inbox notes.

Sync writes only changed, nonempty resulting bodies. An empty resulting file
body is not written, even if validation accepted it. It appends new mistake
entries after dropping blanks and entries whose initial `### ` heading already
appears in the existing audit body. Archive entries are appended independently
to `.memory/archive/YYYY-MM.md`; they do not themselves remove active content.
Mistakes overflow is code-managed: oldest `### ` sections move to that archive
until the remaining body fits its budget or no entries remain. Other active
files are pruned only through curator ops, with over-budget notices and
candidates rather than automatic removal.

## Writing a backend

- Configure an executable argv array in `curator_cmd`; use `{model}` where the
  selected model should be substituted.
- Read the full prompt from stdin and return within the per-invocation timeout.
- Write one JSON object with the advertised fields and element types to stdout;
  use stderr for diagnostics.
- Exit zero for a successful response and nonzero for a failed invocation.

See [contrib/curators/](../contrib/curators/) for backend wrappers and examples.
