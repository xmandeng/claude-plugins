# review-suite

A single Claude Code plugin bundling five slash commands for interactive review and diagramming workflows, all sharing one devserver binary with a WebSocket PTY bridge that lets review playgrounds deliver feedback directly back into a live Claude Code session.

## Slash Commands

| Command | What it does |
|---|---|
| `/plan-review [<ticket>]` | Generates a section-by-section HTML review playground from an implementation plan. Reviewer can approve / flag-for-revision / ask-questions per section, then click "Send to Claude" to deliver structured feedback back into the authoring session. |
| `/architecture-review [<ticket>]` | Generates an interactive before/after component diagram. Per-node approve/revise/question controls, saved named layouts, per-node comment pins. |
| `/architecture-map [<ticket>]` | Generates a concept-map playground. Draggable node graph with layered filters, per-node insights, per-node feedback pins. Seeded from chat context. |
| `/code-diagram` | Generates Graphviz `.dot` source plus rendered SVG/PNG/PDF for call graphs, class models, dependency graphs, and component/process diagrams. |
| `/devserver [port]` | Starts (or reuses) the bundled devserver from the user's project root. Use this to browse old playground HTMLs without invoking a full review skill. |

## Installation

```
/plugin install review-suite@xmandeng-plugins
```

If you previously installed any of the four legacy plugins (`plan-review`, `architecture-review`, `architecture-map`, `code-diagram`), uninstall them first:

```
/plugin uninstall plan-review
/plugin uninstall architecture-review
/plugin uninstall architecture-map
```

The legacy entries are deprecated in the marketplace and will be removed in the next release.

## Architecture

```
review-suite/
├── bin/
│   ├── devserver.py            # one binary serves all skills
│   └── inject-session-id.sh    # one hook emits review-suite-session-id
├── hooks/hooks.json            # registers UserPromptSubmit → inject-session-id.sh
├── skills/
│   ├── plan-review/SKILL.md
│   ├── architecture-review/SKILL.md
│   ├── architecture-map/SKILL.md
│   ├── code-diagram/SKILL.md
│   └── devserver/SKILL.md
└── assets/
    ├── review-template.html
    ├── architecture-template.html
    ├── map-template.html
    ├── REVIEW_TEMPLATE.md
    └── screenshots/
```

### Devserver

`bin/devserver.py` is a `SimpleHTTPRequestHandler` plus:

- **PUT `/*-layouts.json`** — atomic write, scoped to spawn cwd, 256 KB cap, path-traversal-safe. Used by architecture/map templates to persist named layouts.
- **WS `/api/claude?session=<sid>`** — bridges browser `xterm.js` to a local `claude --resume <sid>` PTY. Each generated review HTML embeds the authoring session's id at generation time, so clicking "Send to Claude" continues the exact session that wrote the plan.

Default port: `8765`. Override with `REVIEW_SUITE_PORT`. Override LAN IP with `REVIEW_SUITE_HOST`.

### Hook

A single `UserPromptSubmit` hook injects the current session id into every turn as `review-suite-session-id: <sid>`. Each review skill greps for this label to bake the sid into the generated HTML's `CLAUDE_SESSION` constant.

### Port reuse

Each project's first invocation of any review skill picks the first free port in 8765-8799 and writes it to `<project>/.plan-review/.devserver-port`. Subsequent invocations reuse that port. Concurrent projects auto-allocate sequential ports.

## Why a single bundle?

The four prior plugins shared the same devserver protocol but each shipped its own copy of the binary and hook script. Drift between copies was a known problem. One bundle = one source of truth, plus the new `/devserver` skill that lets you start the server without invoking a review workflow.

## Prerequisites

- Python 3.10+
- `claude` CLI in PATH (for the PTY bridge)
- `jq` (for the session-id hook)
- `graphviz` / `dot` (only for `/code-diagram`)
- `ptyprocess` (optional — devserver falls back to stdlib `pty.fork()` if missing)

## Migration from legacy plugins

| Legacy plugin | Replacement |
|---|---|
| `plan-review` | `review-suite` (`/plan-review`) |
| `architecture-review` | `review-suite` (`/architecture-review`) |
| `architecture-map` | `review-suite` (`/architecture-map`) |
| `code-diagram` | `review-suite` (`/code-diagram`) |

Existing review HTML files in `.plan-review/` keep working — `PLAN_NAME` and `CLAUDE_SESSION` are baked into the HTML at authoring time, so localStorage keys and the PTY bridge spawn behavior are unchanged.

**Bookmarks at port 8775 (architecture-review) or 8785 (architecture-map) won't resolve under the bundle**, which defaults to 8765. If you need the old port, set `REVIEW_SUITE_PORT=8775` (or 8785) in your shell.

## License

MIT
