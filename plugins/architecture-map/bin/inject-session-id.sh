#!/usr/bin/env bash
set -euo pipefail

sid=$(jq -r '.session_id')
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"architecture-map-session-id: %s"}}\n' "$sid"
