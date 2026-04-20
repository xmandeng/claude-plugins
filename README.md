# claude-plugins

Claude Code plugins, distributed as a single marketplace.

## Quick Start

```
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

---

## Coming Soon

- **Software architecture diagrams** — interactive before/after component maps for illustrating system designs, refactors, and plan implementations.

More ideas welcome — [open an issue](https://github.com/xmandeng/claude-plugins/issues).

## License

MIT — see [LICENSE](LICENSE).
