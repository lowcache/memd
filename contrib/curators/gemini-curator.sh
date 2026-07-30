#!/bin/sh
# Wrapper for the gemini CLI to act as a memd curator backend.
# Config: "curator_cmd": ["/absolute/path/to/gemini-curator.sh", "{model}"]
#
# UNVERIFIED. The invocation below follows `gemini --help` (-p selects
# non-interactive mode; the piped prompt arrives on stdin), but it could not be
# run end to end: on the test machine gemini exited 1 during authentication
# with "IneligibleTierError: This client is no longer supported for Gemini Code
# Assist for individuals". Confirm it works for your account before relying on
# it — pipe a prompt through this script by hand and check you get JSON back.
set -eu

if [ "$#" -lt 1 ] || [ -z "$1" ]; then
  echo "Error: model name required as first argument (\$1)" >&2
  exit 2
fi

exec gemini -p "" -m "$1"
