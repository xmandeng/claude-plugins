# review-suite

A Claude Code plugin bundling five slash commands for interactive review and diagramming workflows. Generated playgrounds render in a browser and deliver structured feedback back into the live Claude Code session that authored them, via a shared devserver with a WebSocket PTY bridge.

## Slash Commands

| Command | What it does |
|---|---|
| `/plan-review [<ticket>]` | Section-by-section HTML review playground for an implementation plan. Approve / flag-for-revision / ask-questions per section, then click "Send to Claude" to deliver the feedback bundle. |
| `/design-review [<ticket>]` | Interactive before/after component diagram. Per-node approve/revise/question controls, saved named layouts, per-node comment pins. |
| `/architecture-map [<ticket>]` | Concept-map playground for an existing application. Draggable node graph with layered filters, per-node insights, per-node feedback pins. Seeded from chat context. |
| `/code-diagram [<scope>]` | Graphviz `.dot` source plus rendered SVG/PNG/PDF for call graphs, class models, dependency graphs, and component/process diagrams. Optional scope argument narrows the slice of code being diagrammed. |
| `/devserver [port]` | Starts (or reuses) the devserver from the project root, so you can browse generated playground HTML files without first invoking a review skill. |

## Installation

```
/plugin marketplace add xmandeng/claude-plugins
/plugin install review-suite@xmandeng-plugins
```

## Architecture

```
review-suite/
├── .claude-plugin/plugin.json   # name, version, metadata
├── README.md
├── bin/
│   ├── devserver.py             # one binary serves all skills
│   └── inject-session-id.sh     # one hook emits review-suite-session-id
├── hooks/hooks.json             # registers UserPromptSubmit → inject-session-id.sh
├── skills/
│   ├── plan-review/SKILL.md
│   ├── design-review/SKILL.md
│   ├── architecture-map/SKILL.md
│   ├── code-diagram/SKILL.md
│   └── devserver/SKILL.md
├── assets/
│   ├── review-template.html
│   ├── design-review-template.html
│   ├── map-template.html
│   ├── REVIEW_TEMPLATE.md
│   └── screenshots/
└── tests/
    ├── conftest.py
    └── test_*.py                # devserver, sessions, worktrees, access control
```

### Devserver

`bin/devserver.py` is a `SimpleHTTPRequestHandler` plus:

- **PUT `/*-layouts.json`** — atomic write, scoped to spawn cwd, 256 KB cap, path-traversal-safe. Used by design-review / architecture-map templates to persist named layouts.
- **WS `/api/claude?session=<sid>&playground=<rel-path>`** — bridges browser `xterm.js` to a live `claude` PTY kept per playground across reloads and dropped connections (the page auto-reconnects). On a cold start (first open, devserver restart, idle reap) it resumes the fork that playground ran last, recorded in `.plan-review/.playground-sessions.json`, or forks afresh from the authoring session with `claude --resume <authoring-sid> --fork-session`. The authoring transcript is found under any `~/.claude/projects/` directory and linked into the devserver's project when needed, so playgrounds work from git worktrees.

Default port: `8765`. Override with `REVIEW_SUITE_PORT`. Override LAN IP with `REVIEW_SUITE_HOST`. Idle `claude` children are stopped after `REVIEW_SUITE_IDLE_REAP_SECONDS` (default 3600) without a browser attached. A devserver exits on its own once its directory is deleted (a removed worktree), and one from an older plugin version is not reused after `/plugin update`.

### Hook

A single `UserPromptSubmit` hook injects the current session id into every turn as `review-suite-session-id: <sid>`. The three playground-generating skills (`/plan-review`, `/design-review`, `/architecture-map`) grep for this label to bake the sid into the generated HTML's `CLAUDE_SESSION` constant, so the "Send to Claude" button can resume the exact authoring session. `/code-diagram` and `/devserver` don't use the hook (no PTY bridge needed).

### Port reuse

The first invocation of any skill that starts the devserver (`/plan-review`, `/design-review`, `/architecture-map`, or `/devserver` directly) picks the first free port in 8765-8799 and writes it to `<project>/.plan-review/.devserver-port`. Subsequent invocations in the same project reuse that port. Concurrent projects auto-allocate sequential ports.

## Tests

```
python3 -m pytest plugins/review-suite/tests/ -v
```

The suite covers WebSocket framing (RFC 6455), LAN-IP resolution, the PUT handler, project-scoped discovery, persistent PTY sessions, cold-start fork/resume decisions, worktree transcript resolution, and the live HTTP server.

## Prerequisites

- Python 3.10+
- `claude` CLI in PATH (for the PTY bridge)
- `jq` (for the session-id hook)
- `graphviz` / `dot` (only for `/code-diagram`)
- `ptyprocess` (optional — devserver falls back to stdlib `pty.fork()` if missing)

## License

MIT
