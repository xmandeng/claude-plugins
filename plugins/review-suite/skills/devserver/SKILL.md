---
name: devserver
description: Start (or reuse) the bundled review-suite devserver to browse generated review/architecture/map HTML files in the current project. Useful for opening prior `.plan-review/*.html` files without having to invoke a full review skill. Usage - /devserver [port]
user-invocable: true
allowed-tools: Bash(lsof:*) Bash(python3:*) Bash(mkdir:*) Bash(cat:*) Bash(echo:*) Bash(hostname:*) Bash(awk:*)
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

1. **Reuse if running.** Checks `<project-root>/.plan-review/.devserver-port` for a saved port. If a server is still listening on that port, reuse it.
2. **Otherwise start fresh.** Scans 8765-8799 for the first free port (or honors the user's explicit port arg) and launches `${CLAUDE_PLUGIN_ROOT}/bin/devserver.py <port>` from the user's project root (NOT `cd`'d into a subdirectory — paths in URLs include the directory prefix).
3. **Persist the port** to `.plan-review/.devserver-port` so the review skills reuse it on their next invocation.
4. **Return the LAN-IP URL** so the user can open it from their local browser via VS Code Remote SSH port forwarding.

The devserver supports:

- `GET /` — static file serving (any path under the project root)
- `PUT /*-layouts.json` — atomic write of layouts JSON (used by architecture/map templates)
- `WS /api/claude?session=<sid>` — PTY bridge spawning `claude --resume <sid>` (used by review playgrounds with their session ID baked into the HTML at authoring time)

## Instructions

When invoked:

1. **Parse arg.** Optional first arg = port (integer). Default: scan 8765-8799 for first free port.

2. **Detect prior server.** Read `<cwd>/.plan-review/.devserver-port` if it exists. If `lsof -i :<saved-port>` reports something listening, reuse — emit the URL and exit. Do NOT spawn a second devserver on the same port.

3. **Pick a port.**
   - If the user supplied a port arg, use it. Fail loudly if it's already in use (`lsof -i :<port>`).
   - Otherwise scan 8765-8799 sequentially until `lsof -i :<port>` reports unused.

4. **Launch.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/bin/devserver.py" <port> &` from the **current working directory** (the user's project root). Do NOT `cd` into a subdirectory first — the PTY bridge spawns `claude --resume <sid>` from this cwd, and any review HTML's session ID was authored under this project, so the cwd must match.

5. **Persist port.** `mkdir -p .plan-review && echo <port> > .plan-review/.devserver-port`

6. **Resolve LAN IP.** `hostname -I | awk '{print $1}'` — this is what VS Code's port forwarder needs.

7. **Return URLs to the user.** Show:
   - Server root: `http://<lan-ip>:<port>/`
   - Tip: append the directory + filename, e.g. `http://<lan-ip>:<port>/.plan-review/TT-128-foo-review.html`
   - List any existing `.plan-review/*.html` files as clickable suggestions.

8. **Mention** that the user can stop the server later with `kill $(lsof -t -i :<port>)`.

## Reference Implementation

```bash
PORT_ARG="${1:-}"
OUT_DIR=".plan-review"
mkdir -p "$OUT_DIR"
PORT_FILE="$OUT_DIR/.devserver-port"

# Reuse?
if [ -f "$PORT_FILE" ]; then
  SAVED=$(cat "$PORT_FILE")
  if lsof -i ":$SAVED" >/dev/null 2>&1; then
    PORT="$SAVED"
    echo "Reusing devserver on port $PORT"
  fi
fi

# Pick port
if [ -z "${PORT:-}" ]; then
  if [ -n "$PORT_ARG" ]; then
    PORT="$PORT_ARG"
    if lsof -i ":$PORT" >/dev/null 2>&1; then
      echo "Port $PORT is in use" >&2
      exit 1
    fi
  else
    PORT=8765
    while lsof -i ":$PORT" >/dev/null 2>&1 && [ "$PORT" -lt 8800 ]; do
      PORT=$((PORT + 1))
    done
  fi
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/devserver.py" "$PORT" &
  echo "$PORT" > "$PORT_FILE"
  sleep 1
fi

LAN_IP=$(hostname -I | awk '{print $1}')
echo "Devserver: http://$LAN_IP:$PORT/"
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
