# claude-plugins

Growing collection of [Claude Code](https://claude.com/claude-code) plugins, distributed as a single marketplace.

## Quick Start

Add the marketplace once (this clones the repo into your local plugin cache):

```
/plugin marketplace add xmandeng/claude-plugins
```

Then install any plugin from the list below:

```
/plugin install <plugin-name>@xmandeng-plugins
```

> **Why two names?** The GitHub repo is `xmandeng/claude-plugins`, but the marketplace's internal identifier is `xmandeng-plugins` (Claude Code reserves the `claude-*` namespace for Anthropic's official marketplace, so we can't use `claude-plugins` as the marketplace name). The `xmandeng/claude-plugins` form is what you `marketplace add`; the `@xmandeng-plugins` form is what you `install` against.

---

## Plugins

### `plan-review`

Interactive HTML review playgrounds for implementation plans. Each section of your plan gets independent **approve** / **revise** / **question** controls, and a **Send to Claude** button delivers structured feedback straight to a live Claude Code session via an embedded terminal — no copy-paste, no context-switching.

- **Install:** `/plugin install plan-review@xmandeng-plugins`
- **Invoke:** `/plan-review [<ticket>]` inside any Claude Code session
- **Docs:** [`plugins/plan-review/`](./plugins/plan-review/)

---

## Coming Soon

- **Software architecture diagrams** — interactive before/after component maps for illustrating system designs, refactors, and plan implementations. Draggable nodes, annotated data flow, inline code snippets.
- More ideas welcome — [open an issue](https://github.com/xmandeng/claude-plugins/issues).

---

## Repository Layout

```
.claude-plugin/
└── marketplace.json          # marketplace manifest (lists every plugin below)
plugins/
└── <plugin-name>/
    ├── .claude-plugin/
    │   └── plugin.json       # plugin manifest
    ├── skills/
    │   └── <skill-name>/
    │       └── SKILL.md      # the skill that backs the slash command
    └── README.md             # per-plugin docs
```

Each plugin lives in its own subdirectory under `plugins/`. The root `.claude-plugin/marketplace.json` registers them all so a single `marketplace add` exposes the full collection.

## Adding a New Plugin

1. Create `plugins/<new-plugin>/` with the standard layout above.
2. Add an entry to `plugins[]` in `.claude-plugin/marketplace.json` with `"source": "./plugins/<new-plugin>"`.
3. Commit and push — the new plugin becomes installable on the next `/plugin marketplace update`.

## License

MIT — see [LICENSE](LICENSE). Individual plugins may also include their own LICENSE files.
