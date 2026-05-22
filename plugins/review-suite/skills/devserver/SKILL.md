---
name: devserver
description: Start (or reuse) the bundled review-suite devserver to browse generated review/architecture/map HTML files in the current project. Useful for opening prior `.plan-review/*.html` files without having to invoke a full review skill. Usage - /devserver [port]
user-invocable: true
allowed-tools: Bash(python3:*) Bash(cat:*) Bash(echo:*) Bash(ls:*) Bash(eval:*)
---

# Devserver Skill

Launches the review-suite devserver from the user's project root so any HTML file under the project tree (`.plan-review/*.html`, `docs/architecture-map/*.html`, etc.) can be browsed without first invoking a review/architecture skill.

## When to Use

- Opening a previously-generated review playground after a fresh session.
- Browsing arbitrary HTML files served from the project tree (the server is just `SimpleHTTPRequestHandler` plus PUT for `*-layouts.json`).
- Quickly checking the PTY bridge wiring without authoring a new plan.

If you want to *generate* a new playground, use `/plan-review`, `/design-review`, or `/architecture-map` directly — those skills also start the devserver as a side effect, so calling this skill first isn't necessary.

## Invocation

```
/devserver           # default port 8765 (or first free port 8765-8799)
/devserver 9000      # request specific port
```

## How It Works

Project-scoped discovery is implemented inside the devserver binary itself. The skill is a one-line invocation; all logic lives in `bin/devserver.py find-or-start`.

1. **Reuse via port-file fast path.** Checks `<project-root>/.plan-review/.devserver-port` for a saved port. If a listener on that port has `/proc/<pid>/cwd` resolving to this project root, reuse it.
2. **Reuse via process-pattern fallback, cwd-filtered.** If the port file is missing or stale, scan `pgrep -f "review-suite.*devserver\.py"` matches and pick the first whose `/proc/<pid>/cwd` matches this project root. Devservers running in **other** project roots are intentionally NOT reused — they serve the wrong static root and would attach the PTY bridge to the wrong transcript.
3. **Otherwise start fresh.** Pick the first free port in 8765-8799 (or honor the explicit port arg), spawn `python3 bin/devserver.py <port>` in this project's cwd with `start_new_session=True`, then wait until the port begins listening.
4. **Persist the port** to `<project-root>/.plan-review/.devserver-port`.
5. **Print `URL=...`, `PORT=...`, `LAN_IP=...` to stdout** so the caller can `eval` the output. The URL uses the host's LAN IP so VS Code's port forwarder can hand it to the user's local browser.

The devserver supports:

- `GET /` — static file serving (any path under the project root)
- `PUT /*-layouts.json` — atomic write of layouts JSON (used by architecture/map templates)
- `WS /api/claude?session=<sid>` — PTY bridge spawning `claude --resume <sid>` (used by review playgrounds with their session ID baked into the HTML at authoring time)

## Instructions

When invoked:

1. **Parse arg.** Optional first arg = port (integer). Pass it through to `find-or-start`. Otherwise omit.

2. **Invoke `find-or-start`.** This handles all reuse, spawn, and port-persist logic internally and prints a `URL=…`, `PORT=…`, `LAN_IP=…` envelope to stdout. `eval` the output to populate shell vars.

3. **Return URLs to the user.** Show:
   - Server root: `$URL`
   - Tip: append the directory + filename, e.g. `${URL}.plan-review/TT-128-foo-review.html`
   - List any existing `.plan-review/*.html` files as clickable suggestions.

4. **Mention** that the user can stop the server later with `kill $(lsof -t -i :$PORT)`.

## Reference Implementation

```bash
PORT_ARG="${1:-}"

# find-or-start handles: port-file fast path, pgrep+cwd-match fallback,
# free-port pick, background spawn, port-file persist. Output is a three-line
# `key='value'` envelope — eval to populate $URL, $PORT, $LAN_IP.
eval "$(python3 "${CLAUDE_PLUGIN_ROOT}/bin/devserver.py" find-or-start ${PORT_ARG:+"$PORT_ARG"})"

echo "Devserver: $URL"
```

## Environment Variable Reference

| Variable | Default | Purpose |
|---|---|---|
| `REVIEW_SUITE_HOST` | auto-detected LAN IP | Override host in printed URL |
| `REVIEW_SUITE_PORT` | `8765` | Override default port |

These are honored by the devserver binary itself; the skill passes through any port arg as `argv[1]`.

## Prerequisites

- Python 3.10+
- `lsof` (almost always installed on Linux/macOS)
- `claude` CLI in PATH (only needed if browsing review playgrounds that use the PTY bridge)
