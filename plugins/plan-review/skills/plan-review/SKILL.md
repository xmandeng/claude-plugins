---
name: plan-review
description: Create an interactive HTML review playground for an implementation plan. Generates a section-by-section reviewable document with approve/revise/question controls and a "Send to Claude" button that delivers feedback directly to a live Claude Code session. Usage - /plan-review [<ticket>]
allowed-tools: Read Write Edit Bash(mkdir:*) Bash(cp:*) Bash(lsof:*) Bash(python3:*) Bash(ls:*) Bash(cat:*) Bash(echo:*)
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

1. Check for a prior plan matching the ticket. If found, offer **Resume** / **Overwrite** / **Cancel**.
2. Read the bundled review template: `${CLAUDE_PLUGIN_ROOT}/assets/review-template.html`
3. Populate the `docSections` array with plan content from the main agent.
4. Set the page title, heading, and `PLAN_NAME` constant.
5. Write the output HTML to the resolved output directory.
6. Start the bundled devserver: `${CLAUDE_PLUGIN_ROOT}/bin/devserver.py` on port 8765.
7. Return the LAN-IP URL.

On **Resume** the flow short-circuits: hydrate the prior plan's `docSections` and `priorApprovals` into the agent's context, rewrite only the `CLAUDE_SESSION` constant in the existing HTML, then jump to step 6.

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

2a. **Detect prior plan for this ticket.** Before reading the template or constructing a new filename, glob the output directory for an existing HTML matching the ticket:

   ```bash
   shopt -s nullglob
   PRIOR=( "$OUT_DIR"/"$TICKET"-*-review.html )
   shopt -u nullglob
   ```

   Glob rather than exact `<ticket>-<slug>-review.html` match so the check still catches the prior file when the user has tweaked the plan title (slug drift). Skip this step entirely if no ticket was supplied — titles alone are too noisy to match prior runs reliably.

   - **Zero matches** → continue to step 3 (write-new flow unchanged).
   - **One match** → ask the user:

     > Found a prior plan for `<ticket>` at `<path>` (modified `<mtime>`).
     >
     > - **Resume** — keep the plan content and your prior review marks; refresh the embedded session id so the terminal bridge works.
     > - **Overwrite** — replace with a freshly generated plan from the current conversation. Prior review marks for this filename remain in the browser's localStorage.
     > - **Cancel** — do nothing.

     On **Resume**, jump to the "Resume: Hydrate and Refresh" section below. On **Overwrite**, continue to step 3. On **Cancel**, return without writing or starting the devserver.
   - **Multiple matches** → list each prior file with path + mtime; ask the user to choose which to resume, or Overwrite / Cancel. (Rare: only happens if the title slug changed between runs and the stale file was never deleted.)

3. **Construct filename.** `<output-dir>/<ticket>-<slugified-title>-review.html` (lowercase, hyphens). If no ticket, use `<slugified-title>-review.html`.

4. **Read the template.** `${CLAUDE_PLUGIN_ROOT}/assets/review-template.html`.

5. **Ask the main agent to provide plan sections.** Each section needs:
   - `id` — unique slug for DOM IDs and navigation
   - `title` — displayed in nav and section header
   - `content` — markdown-like string (supports `**bold**`, `` `code` ``, fenced code blocks, `### Heading`, `- list items`, tables, `[links](url)`)
   - `revised` (optional) — set to `true` to highlight as updated in subsequent review rounds

5a. **Read the authoring session id.** Look for the `plan-review-session-id: <sid>` line in your own context — it is injected by the plugin's `UserPromptSubmit` hook on every turn. Extract `<sid>` for step 7. If absent, the hook did not fire (plugin not installed, or first turn of a malformed setup) — surface the error to the user rather than generating an unusable HTML.

6. **Replace the `docSections` array** in the template with the provided sections.

7. **Update identifiers** in the HTML:
   - `<title>` tag → `Plan Review: <ticket>: <title>` (or `Plan Review: <title>` if no ticket)
   - Topbar `<h1>` → `<ticket>: <title>` (or `<title>`)
   - `PLAN_NAME` JS constant → `<ticket>: <title>` (or `<title>`)
   - `CLAUDE_SESSION` JS constant → the session id extracted in step 5a. The embedded terminal uses this to spawn `claude --resume <sid>`.

8. **Write the file** to the resolved output directory.

9. **Start (or reuse) the devserver.** Each project gets its own port, recorded in `<output-dir>/.devserver-port`. Re-invocations in the same project reuse the existing devserver; concurrent projects auto-allocate sequential free ports. Do NOT `cd` first — the devserver must be launched from the user's project root.

   ```bash
   OUT_DIR="${PLAN_REVIEW_DIR:-.plan-review}"
   mkdir -p "$OUT_DIR"
   PORT_FILE="$OUT_DIR/.devserver-port"

   PORT=""
   if [ -f "$PORT_FILE" ]; then
     SAVED=$(cat "$PORT_FILE")
     # Reuse if devserver is still listening on the recorded port
     lsof -i ":$SAVED" >/dev/null 2>&1 && PORT="$SAVED"
   fi

   if [ -z "$PORT" ]; then
     # Scan for first free port in 8765-8799
     PORT=8765
     while lsof -i ":$PORT" >/dev/null 2>&1 && [ "$PORT" -lt 8800 ]; do
       PORT=$((PORT + 1))
     done
     python3 "${CLAUDE_PLUGIN_ROOT}/bin/devserver.py" "$PORT" &
     echo "$PORT" > "$PORT_FILE"
   fi
   ```

10. **Return the URL.** Format: `http://<lan-ip>:$PORT/<output-dir-relative-to-cwd>/<filename>.html` (e.g., `http://192.168.1.237:8765/.plan-review/TT-128-foo-review.html`).

## Resume: Hydrate and Refresh

When the user chooses **Resume** at step 2a (or picks a specific file in the multi-match case):

1. **Hydrate context.** Read the prior HTML. Locate the `const docSections = [...];` and `const priorApprovals = {...};` literals and internalize them into the conversation. Then surface a short summary to the user, e.g.:

   > Resumed N-section plan: [list of section titles]. K sections previously approved, M flagged for revision. Where do you want to pick up?

   This step is what makes resume a real resume — without it the agent is blind to the content it's serving. The cost is the prior plan's full text entering context, which is acceptable because resume is user-initiated and infrequent.

2. **Refresh the session id.** Read the current session id from the `plan-review-session-id: <sid>` line injected by the `UserPromptSubmit` hook (same source step 5a uses). Rewrite **only** the `CLAUDE_SESSION = "..."` JS constant in the prior HTML by matching on the anchor and replacing the quoted value in place. Do **not** re-read the template or re-populate `docSections`, `priorApprovals`, `PLAN_NAME`, `<title>`, or `<h1>`.

3. **Validate.** If the `CLAUDE_SESSION` constant can't be found (file edited externally, corrupted, or produced by a pre-session-id plugin version), surface the error and ask the user whether to regenerate from scratch. Do not silently overwrite.

4. **Start (or reuse) the devserver** per step 9.

5. **Return the URL** per step 10.

`PLAN_NAME` is intentionally **not** rewritten on resume — the browser's `localStorage` key is `review-state:<PLAN_NAME>`, and preserving it is what keeps the reviewer's prior approve/revise/question marks attached to the restored file.

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

The devserver's PTY bridge spawns `claude --resume <session-id>` using the sid embedded in the generated HTML at authoring time (see step 5a and step 7). This guarantees the browser resumes the exact session that authored the plan, even when multiple Claude Code sessions run in the same project.

### Handoff

Each generated review includes a "Hand off to terminal" button that copies `claude --resume <sid>` to the clipboard and sends Ctrl+D to the embedded Claude child so the session is released. Paste the copied command in any local terminal to resume from there.

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
