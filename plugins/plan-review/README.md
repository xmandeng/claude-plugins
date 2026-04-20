# plan-review

Interactive HTML review playgrounds for implementation plans. Section-by-section approve / revise / question controls and a Send-to-Claude button that drives a live Claude Code session via an embedded terminal.

The architecture-review checkpoint in an **observable, spec-driven agentic delivery workflow** — spec → architecture review → implementation — where keeping the human in the loop per-section turns "AI slop" from a model problem into a context-alignment problem you can actually fix.

## How It Feels

**Review each section independently.** Approve (green), flag for revision (red), or ask a question (purple). Comments attach per section. Marks persist in `localStorage` — reload the page, your decisions stay.

![Section-by-section review with the feedback panel open](./assets/screenshots/feedback-panel.jpg)

**Send the bundle to Claude.** Your structured feedback streams into an embedded `claude --resume <authoring-session-id>` PTY running inside the page. The session id is baked into the HTML at generation time, so you always reconnect to the exact conversation that authored the plan. Claude responds in the same window.

![Embedded Claude terminal receiving the feedback bundle](./assets/screenshots/terminal-panel.jpg)

**Resume, don't overwrite.** Re-run `/plan-review <ticket>` a week later and the plugin detects the prior HTML. Pick **Resume** — the prior plan hydrates back into the agent's context; only the embedded session id refreshes. Your previous approve/revise/question marks stay attached.

## How It Works

You invoke `/plan-review` from a local Claude Code terminal. The plugin generates the review HTML, spins up a local HTTP + websocket devserver in the background, and returns a URL on your LAN. Open it on any device on your network: the page mounts an xterm.js terminal that websockets back to the devserver, which spawns `claude --resume <authoring-session-id>` in a PTY. Your active Claude session has effectively been handed off into the browser — the feedback you click is the feedback Claude sees.

## Install

```
/plugin marketplace add xmandeng/claude-plugins
/plugin install plan-review@xmandeng-plugins
```

## Quick Start

From any Claude Code session:

```
/plan-review TT-128
```

You get a generated review HTML, a devserver on your LAN IP, and a URL to open on any device on your network. Invoke with no argument to infer the ticket + title from conversation context.

## Configuration

- `PLAN_REVIEW_DIR` — output directory (default `.plan-review/`)
- `PLAN_REVIEW_HOST` — override auto-detected LAN IP in the returned URL
- `PLAN_REVIEW_PORT` — override devserver port (default `8765`)

## License

MIT.
