# plan-review

Interactive HTML review playgrounds for implementation plans. Turn any plan into a section-by-section reviewable document with approve / revise / question controls, then send structured feedback directly to a live Claude Code session via an embedded terminal.

## What It Does

Given an implementation plan, `/plan-review` generates an HTML review document where each section can be individually approved, flagged for revision, or questioned. The "Send to Claude" button delivers structured feedback straight into a running Claude Code session via a PTY bridge — no copy-paste.

## Prerequisites

- **Python 3.10+** on the host machine
- **`claude` CLI** available in PATH
- **`ptyprocess`** pip package (required for the PTY bridge)

## Install

```bash
# Add this marketplace (once) and install the plugin
/plugin marketplace add xmandeng/plan-review
/plugin install plan-review

# Install the one Python dependency
pip install ptyprocess
```

## Quick Start

From any Claude Code session:

```
/plan-review TT-128
```

The skill:
1. Reads your conversation context to infer the plan title (or asks)
2. Generates a review HTML at `.claude/plans/TT-128-<slug>-review.html`
3. Starts a local devserver on port 8765 (bound to your LAN IP)
4. Returns a URL — open it in any browser on your LAN

## Invocation Forms

| Invocation | Behavior |
|---|---|
| `/plan-review` | Model infers both ticket ID and title from conversation context. Confirms with you before writing. |
| `/plan-review <ticket>` | You provide the ticket ID (any tracker format: `TT-128`, `RFC-042`, `PROJ-7`, etc.). Title inferred from context. |

## Output Directory

**Default:** `.claude/plans/` (auto-created if missing). Keeps generated artifacts out of your project's source tree.

**Override:** set `PLAN_REVIEW_DIR` to redirect:

```bash
# Write reviews into docs/plans/ instead
PLAN_REVIEW_DIR=docs/plans /plan-review TT-128

# Or persistently via your shell profile / .envrc
export PLAN_REVIEW_DIR=docs/plans
```

## Feedback Loop

Open the generated HTML in your browser. Each section has three buttons:

- **Approve** — mark the section as accepted
- **Needs Revision** — flag with a freeform note
- **Question** — ask for clarification

When you've reviewed enough sections, click **Send to Claude**. The feedback bundle is delivered to the embedded Claude Code terminal (which the devserver launched via `claude --continue`) as a new user turn. The first click of each browser session prepends a context-switch preamble so Claude knows it's now in review-discussion mode.

If you refresh the page, your review state persists via `localStorage`.

## Session Resume

The embedded terminal uses `claude --continue`, which picks up the most-recently-modified session in the working directory.

**Limitation:** if you run multiple Claude Code sessions in the same project between creating and resuming a review, `--continue` may pick the wrong one. Close other sessions first, or create the review fresh.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `PLAN_REVIEW_DIR` | `.claude/plans/` | Output directory for generated reviews |
| `PLAN_REVIEW_HOST` | auto-detected LAN IP | Override the host in the returned URL |
| `PLAN_REVIEW_PORT` | `8765` | Devserver port |

## Content Format

When the skill asks the main agent for plan sections, each section has an `id`, `title`, and `content` string. The `content` supports a markdown subset:

| Syntax | Renders as |
|---|---|
| `**bold**` | bold |
| `` `inline code` `` | inline code |
| ` ```code block``` ` | fenced block |
| `### Heading` | h4 subheading |
| `- item` / `* item` | unordered list |
| `1. item` | ordered list |
| `\| col \| col \|` | table (first row = header) |
| `[text](url)` | link (opens in new tab) |

## Troubleshooting

- **`devserver port already in use`** — another process holds port 8765. Kill it, or set `PLAN_REVIEW_PORT=8766` and invoke again.
- **`ptyprocess not installed`** — run `pip install ptyprocess` in the environment where `python3` runs for you.
- **`claude command not found` inside the terminal** — the devserver's PTY spawns `claude` from your PATH. Make sure `claude` is on PATH wherever you launched the devserver from.
- **The URL shows `localhost` instead of a LAN IP** — your machine couldn't resolve an outbound IP (no network, VPN isolation, etc.). Set `PLAN_REVIEW_HOST` explicitly if you know the right address.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and PRs welcome. This plugin was extracted from an internal tool; the open-source scope is intentionally minimal — approve / revise / question controls plus the Send-to-Claude bridge. Feature requests beyond that are welcome but not guaranteed to land.
