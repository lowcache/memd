#!/bin/sh
# Wrapper for ollama CLI to act as a memd curator backend.
# Config: "curator_cmd": ["/absolute/path/to/ollama-curator.sh", "{model}"]
set -eu

if [ "$#" -lt 1 ] || [ -z "$1" ]; then
  echo "Error: model name required as first argument (\$1)" >&2
  exit 2
fi

# `ollama run` silently downloads a model that isn't present locally. memd calls
# this from a background sweep, so a typo in model_small must not kick off a
# multi-gigabyte pull; require the model up front instead.
# `ollama list` is space-padded, not tab-separated, so take the first field.
if ! ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | grep -qx -- "$1"; then
  echo "Error: model '$1' is not pulled locally. Run: ollama pull $1" >&2
  echo "(refusing to let 'ollama run' download it implicitly)" >&2
  exit 2
fi

# ollama writes ANSI spinner escapes to stderr even when its output is piped.
# memd puts a failed backend's stderr into the error it reports, so passing the
# spinner through buries the real message — but discarding stderr outright
# leaves a failing backend undiagnosable. Buffer it, and replay it on failure
# with the escape sequences stripped.
err=$(mktemp) || exit 1
trap 'rm -f "$err"' EXIT

if ollama run "$1" --format json 2>"$err"; then
  exit 0
else
  # $? here is the failed condition's status. Reading it after `fi` would give
  # the status of the if-construct itself, which is 0 when no branch ran.
  rc=$?
  esc=$(printf '\033')
  sed "s/${esc}\[[0-9;?]*[a-zA-Z]//g" "$err" >&2
  exit "$rc"
fi
