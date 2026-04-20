# architecture-review

Interactive before/after component diagrams for architecture reviews. Draggable node graph with per-node approve / revise / question pins, saved named layouts, and a Send-to-Claude button that drives a live Claude Code session via an embedded terminal.

The architecture-review checkpoint in an **observable, spec-driven agentic delivery workflow** — spec → architecture review → implementation — where keeping the human in the loop per-node turns "AI slop" from a model problem into a context-alignment problem you can actually fix.

## How It Feels

**Click a node, leave feedback.** Approve (green), flag for revision (red), or ask a question (purple). Attach a comment. Marks persist in `localStorage` — reload the page, your decisions stay. The tally in the topbar shows what you've reviewed out of total nodes.

**Draggable layout.** Reposition nodes to clarify flow. Save the arrangement as a named layout — it writes to disk next to the HTML (the devserver accepts narrow-scoped PUT for `*-layouts.json`). Works on iPad or any touch device too.

**Send the bundle to Claude.** Your structured node feedback streams into an embedded `claude --resume <authoring-session-id>` PTY running inside the page. The session id is baked into the HTML at generation time, so you always reconnect to the exact conversation that authored the diagram. Claude responds in the same window.

**Resume, don't overwrite.** Re-run `/architecture-review <ticket>` a week later and the plugin detects the prior HTML. Pick **Resume** — the nodes and edges hydrate back into the agent's context; only the embedded session id refreshes. Your previous node pins and saved layouts stay attached.

## How It Works

You invoke `/architecture-review` from a local Claude Code terminal. The plugin generates the diagram HTML, spins up a local HTTP + websocket devserver in the background, and returns a URL on your LAN. Open it on any device on your network: the page mounts an xterm.js terminal that websockets back to the devserver, which spawns `claude --resume <authoring-session-id>` in a PTY. Your active Claude session has effectively been handed off into the browser — the feedback you click is the feedback Claude sees.

## Install

```
/plugin marketplace add xmandeng/claude-plugins
/plugin install architecture-review@xmandeng-plugins
```

## Quick Start

From any Claude Code session where you've discussed an architecture change:

```
/architecture-review TT-131
```

You get a generated before/after diagram HTML, a devserver on your LAN IP, and a URL to open on any device on your network. Invoke with no argument to infer the ticket + title from conversation context.

## Configuration

- `ARCHITECTURE_REVIEW_DIR` — output directory (default `.architecture-review/`)
- `ARCHITECTURE_REVIEW_HOST` — override auto-detected LAN IP in the returned URL
- `ARCHITECTURE_REVIEW_PORT` — override devserver port (default `8775`)

Default port is 8775 (plan-review uses 8765) so both plugins can run side-by-side in one project without fighting for ports.

## License

MIT.
