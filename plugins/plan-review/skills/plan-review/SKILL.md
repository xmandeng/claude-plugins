---
name: plan-review
description: Create an interactive HTML review playground for an implementation plan. Generates a section-by-section reviewable document with approve/revise/question controls and a "Send to Claude" button that delivers feedback directly to a live Claude Code session. Usage - /plan-review [<ticket>]
allowed-tools: Read, Write, Bash
argument-hint: "[<ticket>]"
---

# Plan Review Skill

Creates an interactive HTML review playground from a bundled template, pre-populated with plan sections supplied by the main agent. The reviewer can approve, flag for revision, or ask questions on each section. Clicking "Send to Claude" delivers structured feedback directly into an embedded Claude Code terminal (PTY-bridged via the bundled devserver).

## Invocation Forms

| Invocation | Behavior |
|---|---|
| `/plan-review` | Model infers both ticket ID and title from conversation context. Confirms with user before writing; asks if no context is recoverable. |
| `/plan-review <ticket>` | User supplies the ticket ID (any tracker format: `TT-128`, `RFC-042`, `PROJ-7`, etc.). Model infers the title from context; asks if unclear. |

A third form with both args explicit (`/plan-review <id> <title>`) is **not** supported — keep the signature minimal.

## How It Works

1. Read the bundled review template: `${CLAUDE_PLUGIN_ROOT}/assets/review-template.html`
2. Populate the `docSections` array with plan content from the main agent.
3. Set the page title, heading, and `PLAN_NAME` constant.
4. Write the output HTML to the resolved output directory.
5. Start the bundled devserver: `${CLAUDE_PLUGIN_ROOT}/bin/devserver.py` on port 8765.
6. Return the LAN-IP URL.

### Output Directory Resolution

1. **`PLAN_REVIEW_DIR` env var** (if set) — explicit override, absolute or project-relative.
2. **`.plan-review/`** — default, auto-created via `mkdir -p` if missing.

## Instructions

When invoked:

1. **Parse arguments.**
   - Zero args: infer ticket + title from conversation context. Confirm the inferred values with the user before writing. If context is empty, ask the user for both.
   - One arg: treat as ticket ID. Accept any tracker format. Infer title from conversation; ask if the title is unclear.
   - Free-form slugs (e.g., `auth-rework`) passed as a single arg are **not** ticket IDs — treat as title text or ask the user to clarify.

2. **Resolve output directory.** Use `$PLAN_REVIEW_DIR` if set, else `.plan-review/`. Ensure it exists (`mkdir -p`).

3. **Construct filename.** `<output-dir>/<ticket>-<slugified-title>-review.html` (lowercase, hyphens). If no ticket, use `<slugified-title>-review.html`.

4. **Read the template.** `${CLAUDE_PLUGIN_ROOT}/assets/review-template.html`.

5. **Ask the main agent to provide plan sections.** Each section needs:
   - `id` — unique slug for DOM IDs and navigation
   - `title` — displayed in nav and section header
   - `content` — markdown-like string (supports `**bold**`, `` `code` ``, fenced code blocks, `### Heading`, `- list items`, tables, `[links](url)`)
   - `revised` (optional) — set to `true` to highlight as updated in subsequent review rounds

6. **Replace the `docSections` array** in the template with the provided sections.

7. **Update identifiers** in the HTML:
   - `<title>` tag → `Plan Review: <ticket>: <title>` (or `Plan Review: <title>` if no ticket)
   - Topbar `<h1>` → `<ticket>: <title>` (or `<title>`)
   - `PLAN_NAME` JS constant → `<ticket>: <title>` (or `<title>`)

8. **Write the file** to the resolved output directory.

9. **Start the devserver** if not already running on port 8765. Check `lsof -i :8765`; if free, start with:
   ```bash
   cd <output-dir> && python3 "${CLAUDE_PLUGIN_ROOT}/bin/devserver.py" 8765 &
   ```
   The devserver prints the LAN IP at startup and serves from its CWD.

10. **Return the URL.** Format: `http://<lan-ip>:8765/<filename>.html`.

## Structuring Good Review Sections

Each section should be **independently reviewable** — one decision or approval point per section. See `${CLAUDE_PLUGIN_ROOT}/assets/REVIEW_TEMPLATE.md` for authoring guidelines:

- **One concern per section** — avoid mixing unrelated topics
- **Actionable titles** — reviewer should know what they're approving from the nav alone
- **Context first** — lead with a Context section that frames the problem
- **Decision sections last** — put scoping checklists and open questions at the end

## Handling Review Feedback

When the reviewer sends feedback (either via the "Send to Claude" button, which writes directly to the embedded Claude terminal, or via pasted "Copy Feedback" output if they're not using the PTY bridge):

1. **Parse** the approved / revision / question sections from the feedback payload.
2. **For revisions:** update the `content` of affected sections and set `revised: true`. **Clear any stale "What Changed" notes or prior-round commentary from that section — do not stack them.** If the historical note appears load-bearing (documents a specific decision still informing the current content), ask the reviewer before clearing.
3. **For prior approvals:** add the section `id` to the `priorApprovals` object so it shows as pre-approved on reload.
4. **Rewrite the HTML file** with the updated `docSections` and `priorApprovals`.
5. **Tell the reviewer to refresh** the page.

## Session Context Preamble

The review template's "Send to Claude" button prepends a one-time context-switch preamble to the first click of a browser session, then omits it on subsequent clicks. State is tracked via `localStorage` keyed by the review doc's filename.

The preamble text:
> **Context switch:** you are now in the plan-review playground. The review document is at `<path>`. Discuss the feedback below conversationally. Do NOT edit the HTML until I explicitly say to update. When discussion on a section wraps, ask whether to update the document.

## Session Resume

The devserver's PTY bridge spawns `claude --continue`, which resumes the most-recently-modified Claude Code session in the working directory.

**Known limitation:** if the user runs multiple Claude Code sessions in the same project between creating and resuming a review, `--continue` may pick the wrong one. Close other sessions before resuming, or create the review fresh.

## Prerequisites

- Python 3.10+ on the user's machine
- `claude` CLI available in PATH
- `ptyprocess` (optional) — used by the devserver if installed; otherwise the bridge falls back to stdlib `pty.fork()`. No user action needed either way.

## Environment Variable Reference

| Variable | Default | Purpose |
|---|---|---|
| `PLAN_REVIEW_DIR` | `.plan-review/` | Where generated review HTML files are written |
| `PLAN_REVIEW_HOST` | auto-detected LAN IP | Override the host in the returned URL |
| `PLAN_REVIEW_PORT` | `8765` | Devserver port |

## Content Format Reference

The `content` field supports markdown-like syntax rendered by the template's built-in `renderMarkdown()`:

| Syntax | Renders as |
|---|---|
| `**bold**` | bold |
| `` `inline code` `` | inline code |
| ` ```code block``` ` | fenced code block |
| `### Heading` | h4 subheading |
| `- item` or `* item` | unordered list |
| `1. item` | ordered list |
| `\| col \| col \|` | table (first row = header, skip separator row) |
| `[text](url)` | clickable link (opens in new tab) |

Use template literal backtick strings for content (multi-line supported). Escape backticks within content with `\` + backtick.
