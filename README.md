# claude-plugins

Collection of [Claude Code](https://claude.com/claude-code) plugins, distributed as a single marketplace.

## Add the marketplace

```
/plugin marketplace add xmandeng/claude-plugins
```

## Install a plugin

```
/plugin install <plugin-name>@claude-plugins
```

## Plugins

| Plugin | Description |
|---|---|
| [`plan-review`](./plugins/plan-review/) | Interactive HTML review playgrounds for implementation plans. Section-by-section approve/revise/question controls with a Send-to-Claude button that drives a live Claude Code session via an embedded terminal. |

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
