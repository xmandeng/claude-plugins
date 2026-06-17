#!/usr/bin/env bash
set -euo pipefail

# Emit the session id the review-suite playgrounds should bake into their
# embedded terminal (CLAUDE_SESSION). It MUST be a resumable id — one whose
# transcript exists on disk — so the devserver can `claude --resume` / fork it.
#
# The hook payload's `.session_id` is NOT reliable for this: over a long,
# compacted, or background-job conversation the harness advances the live
# session id while the transcript keeps persisting under the ORIGINAL id. The
# advancing id has no transcript of its own, so baking it produces a stillborn
# fork and a dead playground terminal. `.transcript_path` always points at the
# file the conversation actually persists to, so its basename is the correct,
# resumable id. Fall back to `.session_id` only when transcript_path is absent.
input=$(cat)
transcript_path=$(jq -r '.transcript_path // empty' <<<"$input")
session_id=$(jq -r '.session_id // empty' <<<"$input")

if [ -n "$transcript_path" ]; then
  sid=$(basename "$transcript_path" .jsonl)
else
  sid="$session_id"
fi

printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"review-suite-session-id: %s"}}\n' "$sid"
