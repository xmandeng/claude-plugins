#!/usr/bin/env bash
set -euo pipefail

# Emit the session id the review-suite playgrounds should bake into their
# embedded terminal (CLAUDE_SESSION). It MUST be a resumable id — one whose
# transcript exists on disk — so the devserver can `claude --resume` / fork it.
#
# Neither field in the hook payload is guaranteed to name a resumable id. Over a
# long, compacted, or background-job conversation the harness advances the live
# session id (and, observed in practice, the reported `.transcript_path`) while
# the transcript keeps persisting under the ORIGINAL id. Baking an id whose
# .jsonl does not exist produces a stillborn fork and a dead playground
# terminal, and `claude --resume <id>` fails with "No conversation found".
#
# So we treat the payload as a hint and verify against disk:
#   1. Prefer `.transcript_path`'s basename, else `.session_id`.
#   2. If that id has no .jsonl in the project's transcript directory, fall back
#      to the most-recently-modified transcript there — the file the
#      conversation is actually persisting to. (At UserPromptSubmit time the
#      current turn is not flushed yet, so the newest existing file is the real
#      prior-turn transcript, not a phantom for this turn.)
input=$(cat)
transcript_path=$(jq -r '.transcript_path // empty' <<<"$input")
session_id=$(jq -r '.session_id // empty' <<<"$input")

proj_dir=""
if [ -n "$transcript_path" ]; then
  proj_dir=$(dirname "$transcript_path")
  sid=$(basename "$transcript_path" .jsonl)
else
  sid="$session_id"
fi

# Disk-verify the candidate; fall back to the newest existing transcript if the
# harness handed us an id whose file was never written. The scan is pipe-free
# on purpose: `ls | head` takes SIGPIPE when head closes early, which under
# `set -o pipefail` + `set -e` would abort the hook before it prints anything.
if [ -n "$proj_dir" ] && [ ! -f "$proj_dir/$sid.jsonl" ]; then
  newest=""
  for f in "$proj_dir"/*.jsonl; do
    [ -e "$f" ] || continue          # no-match: glob stayed literal
    if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
      newest="$f"
    fi
  done
  if [ -n "$newest" ]; then
    sid=$(basename "$newest" .jsonl)
  fi
fi

printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"review-suite-session-id: %s"}}\n' "$sid"
