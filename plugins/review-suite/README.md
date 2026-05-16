# review-suite

A single Claude Code plugin bundling five slash commands for interactive review and diagramming workflows, all sharing one devserver binary with a WebSocket PTY bridge that lets review playgrounds deliver feedback directly back into a live Claude Code session.

## Slash Commands

| Command | What it does |
|---|---|
| `/plan-review [<ticket>]` | Generates a section-by-section HTML review playground from an implementation plan. Reviewer can approve / flag-for-revision / ask-questions per section, then click "Send to Claude" to deliver structured feedback back into the authoring session. |
| `/architecture-review [<ticket>]` | Generates an interactive before/after component diagram. Per-node approve/revise/question controls, saved named layouts, per-node comment pins. |
| `/architecture-map [<ticket>]` | Generates a concept-map playground. Draggable node graph with layered filters, per-node insights, per-node feedback pins. Seeded from chat context. |
| `/code-diagram [<scope>]` | Generates Graphviz `.dot` source plus rendered SVG/PNG/PDF for call graphs, class models, dependency graphs, and component/process diagrams. The optional scope argument narrows the slice of code being diagrammed. |
| `/devserver [port]` | Starts (or reuses) the bundled devserver from the user's project root. Use this to browse old playground HTMLs without invoking a full review skill. |

## Installation

```
/plugin install review-suite@xmandeng-plugins
```

If you previously installed any of the three legacy plugins (`plan-review`, `architecture-review`, `architecture-map`), uninstall them first:

```
/plugin uninstall plan-review
/plugin uninstall architecture-review
/plugin uninstall architecture-map
```

The legacy entries are marked `[DEPRECATED — migrate to review-suite]` in the marketplace and will be removed in the next release.

`/code-diagram` ships for the first time inside the bundle — there's no standalone version to uninstall.

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
│   ├── architecture-review/SKILL.md
│   ├── architecture-map/SKILL.md
│   ├── code-diagram/SKILL.md
│   └── devserver/SKILL.md
├── assets/
│   ├── review-template.html
│   ├── architecture-template.html
│   ├── map-template.html
│   ├── REVIEW_TEMPLATE.md
│   └── screenshots/
└── tests/
    ├── conftest.py
    └── test_devserver.py        # 54 tests: WS framing, LAN-IP, PUT handler
```

### Devserver

`bin/devserver.py` is a `SimpleHTTPRequestHandler` plus:

- **PUT `/*-layouts.json`** — atomic write, scoped to spawn cwd, 256 KB cap, path-traversal-safe. Used by architecture/map templates to persist named layouts.
- **WS `/api/claude?session=<sid>`** — bridges browser `xterm.js` to a local `claude --resume <sid> --fork-session` PTY. Each generated review HTML embeds the authoring session's id at generation time; the fork inherits the full conversation context that wrote the plan but runs as an independent working session. The hand-off model is intentional: it lets the playground work from background-agent (`bg`) sessions, which refuse re-attach. Feedback you send from the browser stays in the fork — set `REVIEW_SUITE_NO_FORK=1` to disable forking and use the original attach-mode (foreground sessions only).

Default port: `8765`. Override with `REVIEW_SUITE_PORT`. Override LAN IP with `REVIEW_SUITE_HOST`.

### Hook

A single `UserPromptSubmit` hook injects the current session id into every turn as `review-suite-session-id: <sid>`. The three review/map skills (`/plan-review`, `/architecture-review`, `/architecture-map`) grep for this label to bake the sid into the generated HTML's `CLAUDE_SESSION` constant, so the playground's "Send to Claude" button can resume the exact authoring session. `/code-diagram` and `/devserver` don't use the hook (no PTY bridge needed).

### Port reuse

The first invocation of any skill that starts the devserver (`/plan-review`, `/architecture-review`, `/architecture-map`, or `/devserver` directly) picks the first free port in 8765-8799 and writes it to `<project>/.plan-review/.devserver-port`. Subsequent invocations in the same project reuse that port. Concurrent projects auto-allocate sequential ports.

## Why a single bundle?

The three published predecessors (`plan-review`, `architecture-review`, `architecture-map`) shared the same devserver protocol but each shipped its own copy of `bin/devserver.py` and `bin/inject-session-id.sh`. Drift between those copies was a known problem — `plan-review` was missing the PUT-for-`*-layouts.json` handler that `architecture-review` had added. One bundle = one source of truth, plus a new `/devserver` skill that lets you start the server without first invoking a review workflow, and `/code-diagram` joins the suite as a first-time release.

## Tests

```
python3 -m pytest plugins/review-suite/tests/ -v
```

54 tests cover the pure-logic surface of the devserver: WebSocket framing (RFC 6455), LAN-IP resolution, log filtering, the `resolve_safe_layouts_target` PUT-path validator, and the `DevHandler.do_PUT` end-to-end happy path and rejection cases (403/413/415/400). PTY/fork paths and the live HTTP server are integration concerns and aren't exercised in the unit suite.

## Prerequisites

- Python 3.10+
- `claude` CLI in PATH (for the PTY bridge)
- `jq` (for the session-id hook)
- `graphviz` / `dot` (only for `/code-diagram`)
- `ptyprocess` (optional — devserver falls back to stdlib `pty.fork()` if missing)

## Migration from legacy plugins

| Legacy plugin | Bundled replacement | Action |
|---|---|---|
| `plan-review` | `/plan-review` | `/plugin uninstall plan-review`, then install `review-suite` |
| `architecture-review` | `/architecture-review` | `/plugin uninstall architecture-review`, then install `review-suite` |
| `architecture-map` | `/architecture-map` | `/plugin uninstall architecture-map`, then install `review-suite` |
| _(none — new in bundle)_ | `/code-diagram` | n/a |
| _(none — new in bundle)_ | `/devserver` | n/a |

Existing review HTML files in `.plan-review/` keep working — `PLAN_NAME` and `CLAUDE_SESSION` are baked into the HTML at authoring time, so localStorage keys and the PTY bridge spawn behavior are unchanged.

**Bookmarks at port 8775 (architecture-review) or 8785 (architecture-map) won't resolve under the bundle**, which defaults to 8765. If you need the old port, set `REVIEW_SUITE_PORT=8775` (or 8785) in your shell.

## License

MIT
