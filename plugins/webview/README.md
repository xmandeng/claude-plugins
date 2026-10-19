# webview

A real browser in a pane beside your Claude Code conversation. Type `/webview localhost:3000` and the page opens to the right: click, scroll and type in it while you and Claude work on the code behind it. It works on your own machine and on a remote one you reached over SSH, where `localhost` is the remote machine.

![Claude Code on a remote machine over SSH, with a dev server open in the browser pane](./assets/screenshots/browser-pane.jpg)

The browser is [terminal-browser](https://github.com/zenbu-labs/terminal-browser), a full Chromium drawn with the kitty graphics protocol. This plugin is a fork of its Claude Code plugin that also works over SSH.

## Quick start

On the machine where Claude Code runs:

1. Install terminal-browser:

   ```bash
   curl -fsSL https://terminal-browser.sh/install | bash   # or: brew install terminal-browser
   ```

2. Turn on Claude Code's function hooks in `~/.claude/settings.json`:

   ```json
   { "env": { "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1" } }
   ```

3. Install the plugin:

   ```text
   /plugin marketplace add xmandeng/claude-plugins
   /plugin install webview@xmandeng-plugins
   ```

4. Restart Claude Code and run `/webview`.

On a Linux server with no display, set up a [virtual display](#linux-server-without-a-display) first.

## Where it works

| Claude Code session | The pane shows |
|---|---|
| In your terminal, on this machine | The page |
| In your terminal, on a remote machine over SSH | The page |
| A background session, started in the background and attached to (`@claude` in its header) | A message that it isn't supported. Claude Code draws no images in background sessions. |

Your terminal must support the kitty graphics protocol with Unicode placeholders, as [Ghostty](https://ghostty.org) and [kitty](https://sw.kovidgoyal.net/kitty/) do. Nothing may sit between the terminal and Claude Code: tmux and herdr break the graphics.

## Requirements

| Piece | Where | When |
|---|---|---|
| [terminal-browser](https://github.com/zenbu-labs/terminal-browser), tested with v0.13.4 | The machine running Claude Code | Always |
| `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, Claude Code's experimental plugin UI | The machine running Claude Code | Always |
| A kitty-graphics terminal | The machine you sit at | Always |
| `python3` | The machine running Claude Code | Over SSH |
| Xvfb at display `:99` | The machine running Claude Code | Linux with no display, such as a server |

webview carries its own copy of the upstream plugin's code, so the upstream plugin is not needed. It can stay installed: its command is `/browser`, this one's is `/webview`.

### Linux server without a display

The browser needs a display to start. On a server, run Xvfb as a user service at display `:99`:

```bash
sudo apt install xvfb
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/xvfb.service <<'EOF'
[Unit]
Description=Virtual X display :99 (for webview)

[Service]
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user enable --now xvfb.service
```

The plugin finds the display through `/tmp/.X11-unix/X99` and gives it to the browser alone; nothing else on the machine sees it. The service runs while you are logged in.

## Usage

| Command | What it does |
|---|---|
| `/webview` | Opens the pane at the start page, which lists the servers listening on the machine |
| `/webview <url>` | Opens the pane at a URL, such as `localhost:3000` or `example.com` |
| `/webview close` | Closes the pane. Running `/webview` while the pane is open also closes it. |

The start page lists every listening port, not only web servers, so some of its links lead to error pages.

### Shortcuts in the pane

Shortcuts follow the machine the browser runs on: the remote one when you are connected over SSH.

| Action | macOS | Linux |
|---|---|---|
| Edit the URL | ⌘L | Ctrl+L |
| New tab | ⌘T | Ctrl+T |
| Next / previous tab | Ctrl+Tab / Ctrl+Shift+Tab | Ctrl+Tab / Ctrl+Shift+Tab |
| Back / forward | ⌘[ / ⌘] | Ctrl+[ / Ctrl+] |
| Reload | ⌘R | Ctrl+R |
| Find in page | ⌘⇧F | Ctrl+Shift+F |
| Devtools | ⌘⇧I or F12 | Ctrl+Shift+I or F12 |

Your terminal may keep some ⌘ shortcuts for itself: in Ghostty, ⌘T opens a terminal tab.

## Options

Both are off by default.

| Option | When on |
|---|---|
| `openLinks` | A plain click on a web link in Claude's replies opens it in the pane. Ctrl- or Alt-click still opens your usual browser. Needs Claude Code's fullscreen interface (`"tui": "fullscreen"`), which reports plain clicks. |
| `agentTool` | Claude gets tools to open and close the pane itself, to show you a page it is working on. Claude can drive an open page either way, with `terminal-browser action`. |

Set them with `/plugin configure webview@xmandeng-plugins`, or in `~/.claude/settings.json`:

```json
"pluginConfigs": {
  "webview@xmandeng-plugins": { "options": { "openLinks": true, "agentTool": true } }
}
```

Restart Claude Code after a change.

## Troubleshooting

| What you see | Why, and what to do |
|---|---|
| `/webview` isn't offered | Function hooks are off or Claude Code hasn't restarted since the install. Check [quick start](#quick-start) step 2, then restart. |
| The pane says background sessions aren't supported | Claude Code draws no images there. Run `claude` directly in your terminal. |
| The pane stays empty | Usually tmux or herdr between the terminal and Claude Code, or a terminal without kitty graphics. On a Linux server, check that Xvfb is running: `systemctl --user status xvfb`. |
| "Newer terminal-browser version required" | Run `terminal-browser upgrade`. |
| Links in replies still open your usual browser | Turn on `openLinks`, use the fullscreen interface, and restart. Ctrl- and Alt-click always use your usual browser. |
| Clicks land slightly off | Claude Code reports mouse positions in whole character cells, not pixels. Upstream has the same limit. |
| macOS asks whether your terminal may find devices on the local network | Allow it. The plugin talks to the browser through a server on `127.0.0.1`. |

When Claude Code keeps refusing frames, the conversation shows a `webview: frame blit refused: …` line with Claude Code's reason. Relay failures go to Claude Code's debug log (`claude --debug`), and the browser's bridge logs to `~/.terminal-browser/logs/claude-code-plugin-bridge.log`.

## How it works

terminal-browser renders each frame offscreen and writes it to POSIX shared memory. The plugin hands Claude Code the frame's name, and the terminal reads the pixels from memory and draws them under a grid of Unicode placeholder cells. That is all upstream does, and it fails over SSH in two places. webview tries the upstream way first and changes course only when Claude Code refuses a frame:

- **Over SSH, frames go as PNG.** The terminal runs on another machine and cannot read the remote one's shared memory, so Claude Code refuses the frame. webview then sends every frame as a PNG through the SSH connection. `hooks/frame-relay.py` reads the frame, deletes it, and encodes it, also deleting older frames skipped while it worked. A frame over Claude Code's 2 MiB limit is compressed harder, then halved in size until it fits. Each frame crosses the connection in full, so scrolling is less smooth than on a local terminal.
- **The browser gets a display.** The bridge that runs the browser is an Electron app, which exits at once on a Linux machine with no display. When Xvfb is running at `:99`, webview starts the bridge with `DISPLAY=:99`.
- **Background sessions are turned down cleanly.** When Claude Code reports that the session draws no images, webview shows a message and stops the browser, so its frames don't collect in memory.

webview drives terminal-browser through its `claude-bridge` command, which is not a documented public API, and through Claude Code's experimental function hooks. A release of either can change them. webview checks terminal-browser's capabilities before it starts and asks for an upgrade when they are missing.

## Credits

Based on the terminal-browser Claude Code plugin by Zenbu Labs, Inc., used under the MIT License (see [`LICENSE-terminal-browser`](./LICENSE-terminal-browser)). webview's changes are in `hooks/register.tsx` and the added `hooks/frame-relay.py`.
