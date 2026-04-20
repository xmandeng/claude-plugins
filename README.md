# claude-plugins

> Observable checkpoints for agentic software delivery — so agents ship production code, not slop.

## Why

An observable, spec-driven agentic delivery workflow — **spec → architecture review → implementation** — built to reliably ship production code from agents. The premise: "AI slop" is a context-alignment problem, not a model limitation. Give the human a real review surface at each checkpoint and the agent keeps tracking reality.

Every plugin here runs the same pattern. You launch it from a local Claude Code terminal; it spins up a local HTTP server in the background and hands the active Claude session off into a browser page. You review, annotate, and send structured feedback back into the same running session — no copy-paste, no context drift, no fresh chat that forgot what you were doing.

---

## Quick Start

```text
/plugin marketplace add xmandeng/claude-plugins
/plugin install <plugin-name>@xmandeng-plugins
```

---

## Plugins

### `plan-review`

Interactive HTML review playgrounds for implementation plans. Every section becomes an independently reviewable unit — approve, flag for revision, or ask a question. Review state persists across reloads.

![Section-by-section review with the feedback panel open](./plugins/plan-review/assets/screenshots/feedback-panel.jpg)

Click **Send to Claude** and the feedback bundle streams into an embedded `claude --resume <authoring-session-id>` PTY running inside the page. The session id is baked into the HTML at generation time — you always reconnect to the exact conversation that authored the plan.

![Embedded Claude terminal receiving the feedback bundle](./plugins/plan-review/assets/screenshots/terminal-panel.jpg)

- **Install:** `/plugin install plan-review@xmandeng-plugins`
- **Invoke:** `/plan-review [<ticket>]`
- **Docs:** [`plugins/plan-review/`](./plugins/plan-review/)

### `architecture-review`

Interactive before/after component diagrams for architecture reviews. Draggable node graph with per-node approve / revise / question pins, saved named layouts, and a Send-to-Claude button that delivers a structured node feedback bundle into a live Claude Code session via the same embedded-terminal + session-resume pattern `plan-review` uses.

- **Install:** `/plugin install architecture-review@xmandeng-plugins`
- **Invoke:** `/architecture-review [<ticket>]`
- **Docs:** [`plugins/architecture-review/`](./plugins/architecture-review/)

---

## Coming Soon

More ideas welcome — [open an issue](https://github.com/xmandeng/claude-plugins/issues).

## License

MIT — see [LICENSE](LICENSE).
