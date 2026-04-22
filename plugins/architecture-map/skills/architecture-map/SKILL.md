---
name: architecture-map
description: Create an interactive architecture concept-map playground for any application, seeded from chat context. Generates a draggable node graph with layered filters, per-node insights, saved named layouts, per-node feedback pins, and a Send-to-Claude button that drives a live Claude Code session via an embedded terminal. Usage - /architecture-map [<ticket>]
allowed-tools: Read Write Edit Bash(mkdir:*) Bash(cp:*) Bash(lsof:*) Bash(python3:*) Bash(ls:*) Bash(cat:*) Bash(echo:*)
argument-hint: "[<ticket>]"
---

# Architecture Map Skill

Creates an interactive architecture concept-map playground from a bundled template, pre-populated with nodes, edges, layers, and insights that the main agent derives from conversation context. The reviewer attaches per-node approve / revise / question marks plus free-text comments. Clicking "Send to Claude" delivers a structured feedback bundle directly into an embedded Claude Code terminal (PTY-bridged via the bundled devserver).

Positioned alongside its sibling plugins:

| Plugin | Output | When to use |
|---|---|---|
| `plan-review` | Section-by-section plan review | Approving an implementation plan |
| `architecture-review` | Before/after diagram for a proposed change | Reviewing an architecture diff |
| **`architecture-map`** | **Single-view** concept map of a system | Mapping an existing application end-to-end |

## Invocation Forms

| Invocation | Behavior |
|---|---|
| `/architecture-map` | Infer both ticket ID and scope from recent conversation context. Generate without prompting if context is clear; ask only when context is thin. |
| `/architecture-map <ticket>` | User supplies the ticket ID (any tracker format). Infer scope from conversation; ask if unclear. |

A two-arg form is **not** supported — keep the signature minimal, same as `plan-review` and `architecture-review`.

## How It Works

1. Check for a prior map matching the ticket. If found, offer **Resume** / **Overwrite** / **Cancel**.
2. Read the bundled template: `${CLAUDE_PLUGIN_ROOT}/assets/map-template.html`
3. Populate the `nodes` and `edges` arrays with the system under discussion.
4. Set the page title, heading, and JS constants (`PLAN_NAME`, `CLAUDE_SESSION`, `LAYOUTS_FILE`, `SCOPE_HEADER`).
5. Write the output HTML to the resolved output directory.
6. Start the bundled devserver: `${CLAUDE_PLUGIN_ROOT}/bin/devserver.py` on port 8785.
7. Return the LAN-IP URL.

On **Resume** the flow short-circuits: hydrate the prior node/edge arrays into the agent's context, rewrite only the `CLAUDE_SESSION` constant in the existing HTML, then jump to step 6.

### Output Directory Resolution

1. **`ARCHITECTURE_MAP_DIR` env var** (if set) — explicit override, absolute or project-relative.
2. **`.architecture-map/`** — default, auto-created via `mkdir -p` if missing.

Kept distinct from `.plan-review/` and `.architecture-review/` so artifacts don't collide when multiple plugins run in one project.

## Instructions

When invoked:

1. **Parse arguments.**
   - Zero args: infer ticket + scope from conversation context. Generate without prompting if the conversation clearly identifies the application and scope.
   - One arg: treat as ticket ID. Accept any tracker format. Infer scope from conversation; ask only if ambiguous.
   - Free-form slugs passed as a single arg are **not** ticket IDs — treat as a title hint or ask the user to clarify.

2. **Resolve output directory.** Use `$ARCHITECTURE_MAP_DIR` if set, else `.architecture-map/`. Ensure it exists (`mkdir -p`).

2a. **Detect prior map for this ticket.** Before reading the template or constructing a new filename, glob the output directory for an existing HTML matching the ticket:

   ```bash
   shopt -s nullglob
   PRIOR=( "$OUT_DIR"/"$TICKET"-*-architecture-map.html )
   shopt -u nullglob
   ```

   Glob rather than exact match so the check still catches the prior file when the title has drifted. Skip this step entirely if no ticket was supplied — titles alone are too noisy to match prior runs reliably.

   - **Zero matches** → continue to step 3 (write-new flow unchanged).
   - **One match** → ask the user:

     > Found a prior architecture map for `<ticket>` at `<path>` (modified `<mtime>`).
     >
     > - **Resume** — keep the nodes/edges/insights and your prior review marks; refresh the embedded session id so the terminal bridge works.
     > - **Overwrite** — replace with a freshly generated map from the current conversation. Prior review marks for this filename remain in the browser's localStorage.
     > - **Cancel** — do nothing.

     On **Resume**, jump to "Resume: Hydrate and Refresh" below. On **Overwrite**, continue to step 3. On **Cancel**, return without writing or starting the devserver.

   - **Multiple matches** → list each prior file with path + mtime; ask the user to choose which to resume, or Overwrite / Cancel.

3. **Construct filename.** `<output-dir>/<ticket>-<slugified-scope>-architecture-map.html` (lowercase, hyphens). If no ticket, use `<slugified-scope>-architecture-map.html`.

4. **Read the template.** `${CLAUDE_PLUGIN_ROOT}/assets/map-template.html`.

5. **Ask the main agent for node + edge data.** Node schema (mirrors the source `architecture_playground.html`):

   | Property | Required | Description |
   |---|---|---|
   | `id` | Yes | Unique identifier (used in edges). |
   | `x`, `y` | Yes | Initial position on canvas (user can drag to rearrange). |
   | `layer` | Yes | Layer name (free-form string; layer pill is auto-derived). Keep names stable across a map. |
   | `label` | Yes | Component name displayed on the node. |
   | `type` | Yes | Subtitle text (e.g., "Async Manager", "asyncio.Queue[dict]"). |
   | `file` | No | Source file path (shown in detail panel). |
   | `desc` | Yes | One-line description (shown in tooltip + detail panel). |
   | `details` | No | Multi-line body for the detail panel (`\n` separators). |
   | `code` | No | Code snippet shown in the detail panel (`\n` for newlines). |
   | `badge` | No | Pill text below label. |
   | `note` | No | Warning/callout text. |
   | `insights` | No | Array of `{author, text}` pairs — design rationale and implementation notes. `author` is a free-form string; the template picks a color per unique author. |
   | `connections` | Yes | Array of node ids this node connects to (used to highlight related nodes in the detail panel). Can be empty. |

   Edge schema:

   | Property | Required | Description |
   |---|---|---|
   | `from` | Yes | Source node id. |
   | `to` | Yes | Target node id. |
   | `label` | No | Edge label text. |
   | `style` | No | `dashed` for out-of-band flows (callbacks, async tasks); default is a solid arrow. |

5a. **Read the authoring session id.** Look for the `architecture-map-session-id: <sid>` line in your own context — it is injected by the plugin's `UserPromptSubmit` hook on every turn. Extract `<sid>` for step 7. If absent, the hook did not fire (plugin not installed, or first turn of a malformed setup) — surface the error to the user rather than generating an unusable HTML.

6. **Replace the data arrays** (`nodes` and `edges`) in the template with the provided data.

7. **Update identifiers** in the HTML:
   - `<title>` tag → `Architecture Map: <ticket>: <scope>` (or `Architecture Map: <scope>` if no ticket)
   - Topbar `<h1>` → `<ticket> <scope>` (or `<scope>`)
   - `PLAN_NAME` JS constant → `<ticket>: <scope>` (or `<scope>`)
   - `CLAUDE_SESSION` JS constant → the session id from step 5a
   - `LAYOUTS_FILE` JS constant → `<ticket>-<slug>-layouts.json` (relative to the HTML; devserver scopes PUT access to `*-layouts.json` under cwd)
   - `SCOPE_HEADER` JS constant → one-line description of the scope the map captures (e.g., "Full ingestion pipeline", "Auth service only", "Event bus and direct consumers"). Shown as a sub-header in the topbar so reviewers know what was mapped.

8. **Write the file** to the resolved output directory.

9. **Start (or reuse) the devserver.** Each project gets its own port, recorded in `<output-dir>/.devserver-port`. Re-invocations in the same project reuse the existing devserver; concurrent projects auto-allocate sequential free ports. Launch the devserver **from the user's project root** (no `cd` first) so the PTY bridge's `claude --resume <sid>` finds the session transcript.

   ```bash
   OUT_DIR="${ARCHITECTURE_MAP_DIR:-.architecture-map}"
   mkdir -p "$OUT_DIR"
   PORT_FILE="$OUT_DIR/.devserver-port"

   PORT=""
   if [ -f "$PORT_FILE" ]; then
     SAVED=$(cat "$PORT_FILE")
     lsof -i ":$SAVED" >/dev/null 2>&1 && PORT="$SAVED"
   fi

   if [ -z "$PORT" ]; then
     PORT=8785
     while lsof -i ":$PORT" >/dev/null 2>&1 && [ "$PORT" -lt 8810 ]; do
       PORT=$((PORT + 1))
     done
     python3 "${CLAUDE_PLUGIN_ROOT}/bin/devserver.py" "$PORT" &
     echo "$PORT" > "$PORT_FILE"
   fi
   ```

10. **Return the URL.** Format: `http://<lan-ip>:$PORT/<output-dir-relative-to-cwd>/<filename>.html` (e.g., `http://192.168.1.237:8785/.architecture-map/TT-134-ingestion-architecture-map.html`).

## Resume: Hydrate and Refresh

When the user chooses **Resume** at step 2a (or picks a specific file in the multi-match case):

1. **Hydrate context.** Read the prior HTML. Extract the `nodes` and `edges` literals and internalize them. Surface a short summary:

   > Resumed architecture map: N nodes across L layers, M edges. Picking up where you left off — where do you want to focus?

2. **Refresh the session id.** Rewrite **only** the `CLAUDE_SESSION = "..."` JS constant. Do not touch `nodes`, `edges`, `PLAN_NAME`, `LAYOUTS_FILE`, `SCOPE_HEADER`, `<title>`, or `<h1>`.

3. **Validate.** If the `CLAUDE_SESSION` constant can't be found, surface the error and ask whether to regenerate. Never silently overwrite.

4. **Start (or reuse) the devserver** per step 9.

5. **Return the URL** per step 10.

`PLAN_NAME` is intentionally **not** rewritten on resume — the browser's `localStorage` keys are `map-state:<PLAN_NAME>` / `map-layout:<PLAN_NAME>` / `map-autosave:<PLAN_NAME>`, and preserving them is what keeps the reviewer's prior node feedback + saved layouts + in-progress drag positions attached to the restored file.

## Authoring Guidelines

### Scoping the map

Lean on the conversation to decide what to include. Common scope shapes:

- **End-to-end application** — every subsystem, from external inputs through persistence. Default when the user asks to "map the application".
- **One subsystem** — e.g., "auth service only", "event bus and its consumers", "ingestion path only". Draw the subsystem's inputs and outputs as boundary nodes but don't expand them.
- **Cross-cutting concern** — e.g., "how config flows", "how errors propagate". Nodes are ordered around the concern rather than by data flow.

Set `SCOPE_HEADER` to a one-line summary so reviewers can see at a glance what the map does and doesn't cover.

### Node layout

Arrange nodes as a **top-down tree**, not a circular web:

1. **Source nodes at the top** — data origins (APIs, external services, users) get the smallest `y` values.
2. **Processing nodes in the middle** — handlers, routers, services that transform or route data.
3. **Sink nodes at the bottom** — databases, message buses, terminal outputs get the largest `y` values.
4. **Fan-out horizontally** — when a node writes to multiple sinks, spread them across the `x` axis on the same row.
5. **Center the primary flow** — the main path runs down the center; secondary paths branch left/right.

Typical spacing: ~170px vertical gap between tiers, ~200px horizontal gap between siblings.

### Layers

`layer` is free-form. Use whatever names fit the system — `websocket`, `queue`, `routing`, `processor`, `persistence`, `signal`, `config`, `http`, `auth`, `ui`, etc. The template auto-derives layer pills from the unique layer names in the data and assigns a color per layer. Keep layer names stable within a map (don't mix "ws" and "websocket" for the same tier).

Aim for 3–7 distinct layers per map — fewer feels flat, more overloads the pill bar.

### Insights

The `insights` array is where the map earns its keep as a *concept* map, not just a block diagram. Each insight is `{author, text}`:

- `author` — use the codebase author's name for decisions baked into the code (e.g., `"xmandeng"`), and `"claude"` (or the current AI author) for analysis-derived observations. The template colors each unique author consistently.
- `text` — 1–3 sentences. Capture the *why*, not the *what*. Examples:
  - "Singleton is intentional — multiple connections would duplicate market data and waste bandwidth."
  - "This is the hottest path in the system — every market event passes through here."
  - "The 30s heartbeat gives a comfortable 2× safety margin over DXLink's ~60s idle timeout."

Nodes with insights get a cyan dot in the top-right corner. Nodes without are fine too — not every box needs a paragraph.

## Handling Review Feedback

When the reviewer sends feedback via the "Send to Claude" button, a structured bundle arrives in the embedded terminal:

```
Here is my architecture-map review of <ticket>: <scope>:

## Nodes flagged for revision (N)
### <label> — <layer>
File: <path>
Comment: ...

## Questions (N)
...

## Approved (N)
...
```

When you receive the bundle:

1. **Parse** the sections (revision / question / approved).
2. **For revision items:** discuss the concern conversationally. Do NOT edit the HTML until the reviewer explicitly says to update the map.
3. **When discussion on a node wraps,** ask whether to update the document (tweak the node metadata, move it to a different layer, update insights, etc.). Edits should be targeted replacements of the specific node object in the template's `nodes` array.
4. **For approved items:** note the approval; no action required unless the reviewer asks.

## Session Context Preamble

On the first Send-to-Claude click per browser session, the template prepends a one-time preamble:

> **Context switch:** you are now in the architecture-map playground. The map is at `<path>`. Discuss the feedback below conversationally. Do NOT edit the HTML or the layouts JSON until I explicitly say to update. When discussion on a node wraps, ask whether to update the document.

State is tracked via `sessionStorage` keyed off the map doc's filename.

## Session Resume

The devserver's PTY bridge spawns `claude --resume <session-id>` using the sid embedded in the generated HTML at authoring time (steps 5a and 7). This guarantees the browser resumes the exact session that authored the map, even when multiple Claude Code sessions run concurrently in the same project.

### Handoff

Each generated map includes a "Hand off to terminal" button that copies `claude --resume <sid>` to the clipboard and sends Ctrl+D to the embedded Claude child so the session is released. Paste the copied command in any local terminal to resume from there.

## Prerequisites

- Python 3.10+ on the user's machine
- `claude` CLI available in PATH
- `ptyprocess` (optional) — used by the devserver if installed; otherwise falls back to stdlib `pty.fork()`

## Environment Variable Reference

| Variable | Default | Purpose |
|---|---|---|
| `ARCHITECTURE_MAP_DIR` | `.architecture-map/` | Where generated map HTML files are written |
| `ARCHITECTURE_MAP_HOST` | auto-detected LAN IP | Override the host in the returned URL |
| `ARCHITECTURE_MAP_PORT` | `8785` | Preferred devserver port (scan starts here) |

## Saved Layouts

The template persists named layouts via `PUT <ticket>-<slug>-layouts.json`. The bundled devserver has a narrowly-scoped PUT handler that accepts only `*-layouts.json` under its spawn cwd, validates JSON, caps body size at 256 KB, and writes atomically. If PUT fails for any reason, the template gracefully falls back to localStorage + a download prompt on save.
