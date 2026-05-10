#!/usr/bin/env bash
set -euo pipefail

sid=$(jq -r '.session_id')
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"review-suite-session-id: %s"}}\n' "$sid"
