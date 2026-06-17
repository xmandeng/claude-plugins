"""Devserver for the review-suite plugin.

Serves generated review/architecture/map HTML over HTTP, bridges a browser
xterm.js terminal to a local `claude --resume <session-id>` PTY via WebSocket,
and accepts PUT uploads of `*-layouts.json` files so the "saved named layouts"
feature of the architecture / map templates can persist to disk next to the HTML.

Shared by all skills in the bundle (plan-review, design-review,
architecture-map, code-diagram, devserver). One binary, one protocol.

Usage:
    python3 devserver.py [port]          # default port: 8765

Serves the current working directory (project root; skills launch this without
`cd`ing first so the PTY bridge spawns `claude --resume <sid>` in the same
project the transcript was recorded under).

Endpoints:
    GET  /                            — static file serving (SimpleHTTPRequestHandler)
    PUT  /*-layouts.json              — atomic write of layouts JSON, scoped to spawn cwd
    WS   /api/claude?session=<id>     — bridges browser xterm.js to `claude --resume <id>` PTY
"""

import base64
import fcntl
import fnmatch
import hashlib
import json
import os
import pty
import re
import shlex
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


# =============================================================================
# PUT handler helpers — the architecture template persists named layouts via
# `fetch(LAYOUTS_FILE, { method: 'PUT', body: JSON.stringify(layouts) })`.
# We scope PUT writes to `*-layouts.json` under the spawn cwd and cap body size
# so an opportunistic request can't clobber arbitrary files or balloon disk.
# =============================================================================

LAYOUTS_MAX_BYTES = 256 * 1024


def resolve_safe_layouts_target(raw_path: str, spawn_cwd: str) -> Path | None:
    """Return the absolute target path for a PUT to /*-layouts.json, or None if rejected.

    Rejects paths that don't match `*-layouts.json`, empty paths, and any path
    that escapes `spawn_cwd` after resolution (via `..`, absolute paths, or
    symlinks).
    """
    rel = raw_path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if not rel or not fnmatch.fnmatch(os.path.basename(rel), "*-layouts.json"):
        return None
    base = Path(spawn_cwd).resolve()
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate


# =============================================================================
# Sticky-session helpers (QUE-226) — each playground HTML embeds an
# ACTIVE_SESSION JS constant. First WS connect forks from CLAUDE_SESSION and
# writes the new fork's SID into ACTIVE_SESSION via targeted in-place mutation
# (tmp + rename). Subsequent connects read ACTIVE_SESSION and attach instead
# of forking. The HTML is the only artifact — no sibling state files.
# =============================================================================

# HTML basename suffixes the three review-suite skills produce. We accept the
# WS playground path only if its basename matches one of these — same
# defensive posture as resolve_safe_layouts_target.
_VALID_PLAYGROUND_SUFFIXES = (
    "-design-review.html",
    "-architecture-map.html",
    "-review.html",
)

# Targeted in-place replace anchor for the ACTIVE_SESSION constant. The
# anchor is intentionally loose on leading whitespace (the three templates
# use different indentation) but strict on the constant name and the
# double-quote-delimited value so we never rewrite an unrelated string.
_ACTIVE_SESSION_PATTERN = re.compile(
    r'(?P<lead>(^|\n)[ \t]*const ACTIVE_SESSION = ")(?P<value>[^"]*)(?P<tail>";)'
)


def resolve_safe_html_target(playground_rel_path: str, spawn_cwd: str) -> Path | None:
    """Return the absolute path to a playground HTML, or None if rejected.

    Same containment contract as resolve_safe_layouts_target: rejects empty
    paths, paths whose basename doesn't match a known review-suite HTML
    suffix, and any path that escapes spawn_cwd after resolution. Returns
    None on any rejection — the caller treats None as "no sticky-session
    support for this connection" and falls back to the pre-QUE-226
    always-fork path.
    """
    rel = playground_rel_path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    if not rel:
        return None
    base = Path(spawn_cwd).resolve()
    try:
        candidate = (base / rel).resolve()
        candidate.relative_to(base)
    except (ValueError, OSError):
        return None
    if not any(candidate.name.endswith(s) for s in _VALID_PLAYGROUND_SUFFIXES):
        return None
    if not candidate.is_file():
        return None
    return candidate


def read_active_session(html_path: Path) -> str | None:
    """Return the SID embedded in the HTML's ACTIVE_SESSION constant, or None.

    None on: file missing, constant not present (older HTML from a pre-QUE-226
    plugin), empty value (first-open state), or value that isn't a valid UUID
    (garbage / user-edited bogus content). The caller treats None as "fork
    from CLAUDE_SESSION" — same as the missing-file case in the prior
    sibling-file design.
    """
    if html_path is None or not html_path.exists():
        return None
    try:
        text = html_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _ACTIVE_SESSION_PATTERN.search(text)
    if not m:
        return None
    value = m.group("value").strip()
    if len(value) != 36:
        return None
    try:
        uuid.UUID(value)
    except ValueError:
        return None
    return value


def write_active_session(html_path: Path, sid: str) -> None:
    """Mutate ACTIVE_SESSION in the HTML in-place via tmp + atomic rename.

    Targeted slice-and-splice — we do NOT use re.sub with a string
    replacement here because the replacement value (a UUID) is safe, but the
    HTML body contains arbitrary content with backslash sequences that
    re.sub would interpret. Lambda or slice avoids that hazard. Raises
    RuntimeError if the ACTIVE_SESSION constant can't be found (means the
    HTML was generated by an older plugin version that doesn't carry it).
    OSError propagates from the filesystem.
    """
    text = html_path.read_text(encoding="utf-8")
    m = _ACTIVE_SESSION_PATTERN.search(text)
    if not m:
        raise RuntimeError(
            f"ACTIVE_SESSION constant not found in {html_path.name} -- "
            "HTML was generated by a pre-QUE-226 plugin version"
        )
    new_text = text[: m.start("value")] + sid + text[m.end("value") :]
    tmp = html_path.with_suffix(html_path.suffix + ".tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, html_path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def transcript_exists(sid: str, cwd: str) -> bool:
    """True if ``claude --resume <sid>`` would find a transcript for this project.

    Claude records per-project transcripts at
    ``~/.claude/projects/<slug>/<sid>.jsonl``, where ``<slug>`` is the project
    cwd with path separators and dots flattened to dashes. A forked session
    that never received any input, or an archived / garbage id, has no such
    file — resuming it dies with "No conversation found". The bridge checks
    this first so it can fall back to a working session rather than hand the
    user a dead terminal.
    """
    if not sid:
        return False
    slug = re.sub(r"[/.]", "-", cwd)
    return (Path.home() / ".claude" / "projects" / slug / f"{sid}.jsonl").is_file()


# =============================================================================
# WebSocket framing (RFC 6455) — minimal text/binary support for the
# /api/claude PTY bridge. Hand-rolled to avoid pulling websockets/asyncio into
# the otherwise-sync http.server.
# =============================================================================

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


def ws_recv_exactly(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def ws_read_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    """Read one WebSocket frame. Returns (opcode, payload) or None on close/error."""
    header = ws_recv_exactly(sock, 2)
    if header is None:
        return None
    byte1, byte2 = header[0], header[1]
    opcode = byte1 & 0x0F
    masked = (byte2 & 0x80) != 0
    length = byte2 & 0x7F
    if length == 126:
        ext = ws_recv_exactly(sock, 2)
        if ext is None:
            return None
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = ws_recv_exactly(sock, 8)
        if ext is None:
            return None
        length = struct.unpack("!Q", ext)[0]
    mask_key = b""
    if masked:
        mk = ws_recv_exactly(sock, 4)
        if mk is None:
            return None
        mask_key = mk
    payload = ws_recv_exactly(sock, length) if length else b""
    if payload is None:
        return None
    if masked:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return (opcode, payload)


def ws_send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    """Send one unfragmented frame from server (no masking, FIN=1)."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    sock.sendall(bytes(header) + payload)


def resolve_lan_ip() -> str:
    """Return the host's primary LAN IPv4, or 'localhost' if unresolvable."""
    override = os.environ.get("REVIEW_SUITE_HOST")
    if override:
        return override
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and not ip.startswith("127.") else "localhost"
    except OSError:
        return "localhost"


class _StdlibPty:
    """Minimal ptyprocess.PtyProcess-compatible PTY using stdlib only.

    Used as a fallback when `ptyprocess` is not installed. Exposes the same
    `.fd`, `.setwinsize()`, `.isalive()`, `.terminate()` interface so the
    bridge code below works against either backend transparently.
    """

    def __init__(self, argv, cwd=None, env=None, dimensions=(40, 120)):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            # Child: replace process with the target command
            try:
                if cwd:
                    os.chdir(cwd)
            except OSError:
                pass
            try:
                os.execvpe(argv[0], argv, env or os.environ.copy())
            except OSError:
                os._exit(127)
        # Parent: best-effort initial window size
        try:
            self.setwinsize(*dimensions)
        except OSError:
            pass

    def setwinsize(self, rows: int, cols: int) -> None:
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def isalive(self) -> bool:
        try:
            result_pid, _ = os.waitpid(self.pid, os.WNOHANG)
            return result_pid == 0
        except ChildProcessError:
            return False

    def terminate(self, force: bool = False) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(self.pid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            os.waitpid(self.pid, 0)
        except (ChildProcessError, OSError):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def _pty_spawn(argv, cwd, env, dimensions=(40, 120)):
    """Spawn a PTY child. Prefers `ptyprocess`, falls back to stdlib.

    Returns an object exposing `.fd`, `.setwinsize(rows, cols)`, `.isalive()`,
    and `.terminate(force=False)` — compatible with both `ptyprocess.PtyProcess`
    and the in-house `_StdlibPty` fallback.
    """
    try:
        import ptyprocess  # type: ignore[import-untyped]
        return ptyprocess.PtyProcess.spawn(  # type: ignore[no-any-return]
            argv, cwd=cwd, env=env, dimensions=dimensions
        )
    except ImportError:
        return _StdlibPty(argv, cwd=cwd, env=env, dimensions=dimensions)


def bridge_ws_to_claude_pty(
    sock: socket.socket,
    cwd: str,
    session_id: str,
    playground_html: Path | None = None,
) -> None:
    """Spawn the playground's PTY-bridged `claude` session and proxy I/O over the WS.

    Sticky-session behavior (QUE-226):
      - If ``playground_html`` is set and its ACTIVE_SESSION constant holds a
        valid UUID, attach to that SID (``claude --resume <stored>``). One
        fork per playground lifetime.
      - Otherwise, fork from ``session_id`` (the authoring SID baked into the
        HTML's CLAUDE_SESSION), capture the new SID, and write it into the
        HTML's ACTIVE_SESSION constant so the next open re-attaches instead
        of re-forking. The HTML itself is the persistence layer — no sibling
        state file.
      - If ``playground_html`` is None (caller passed no playground identifier,
        the path was rejected as unsafe, or the HTML predates QUE-226 and
        doesn't carry ACTIVE_SESSION), fall back to the pre-QUE-226
        always-fork behavior. Preserves backward compatibility with older
        rendered HTMLs.

    Wire protocol:
      - BINARY frames in both directions carry raw PTY bytes (terminal I/O).
      - TEXT frames carry JSON control messages from the client. Currently
        only ``{"type":"resize","rows":N,"cols":N}`` is recognised.
      - Server sends one TEXT frame at startup with the active session info:
        ``{"type":"active_session","sid":"<sid>","mode":"attach"|"fork"}``.
        Replaces the pre-QUE-226 ``forked_session`` message; clients should
        accept both for the upgrade window.

    Backend selection: if `ptyprocess` is installed, uses it (battle-tested
    third-party PTY wrapper). Otherwise falls back to a stdlib-only
    implementation. Either way works — `pip install ptyprocess` is optional.

    Unix-only (Windows would need `pywinpty`; out of scope).
    """
    if not session_id:
        try:
            ws_send_frame(
                sock,
                OP_TEXT,
                json.dumps(
                    {"error": "WS /api/claude requires ?session=<id> query parameter"}
                ).encode(),
            )
            ws_send_frame(sock, OP_CLOSE, b"")
        except OSError:
            pass
        return

    env = os.environ.copy()
    env.setdefault("TERM", "xterm-256color")

    # `cwd` is the spawn cwd computed at devserver startup (project root).
    # If `claude --resume <sid>` fails because the session id doesn't match a
    # resumable transcript, that's a real configuration error — we surface it
    # rather than masking by silently falling back to a fresh session.
    #
    # Spawn strategy (QUE-226):
    #
    # 1. If the HTML's ACTIVE_SESSION constant holds a valid UUID, attach to
    #    that SID. No --fork-session: this is the "second open and beyond"
    #    path. The user clicks Send-to-Claude or refreshes the page; they
    #    land on the same session every time. This is the whole point of
    #    the sticky model.
    #
    # 2. Otherwise (first open, or backward-compat path with no
    #    playground_html), fork from the authoring SID. Write the new SID
    #    back into the HTML's ACTIVE_SESSION constant so step 1 fires from
    #    now on.
    #
    # Failure modes intentionally NOT recovered silently:
    #   - Stored SID points to a dead session → the claude CLI's error message
    #     surfaces in the terminal; the user blanks out ACTIVE_SESSION in the
    #     HTML to reset.
    #   - Two browser tabs attach to the same SID → claude's session lock
    #     surfaces; second tab sees the error rather than silently forking.
    #   Per QUE-226 design: visible failures > hidden recovery branches.
    # Resilient session selection. The original QUE-226 path resumed the
    # stored fork (or forked the authoring SID) and let `claude --resume` die
    # if that SID had no transcript. In practice that left a dead terminal
    # whenever a fork never persisted (e.g. a fork that received no input, or a
    # playground authored by a session that isn't resumable). We instead try,
    # in order, the first session whose transcript actually exists:
    #   1. stored fork (ACTIVE_SESSION)      -> attach
    #   2. authoring session (CLAUDE_SESSION) -> fork
    #   3. neither resumable                  -> fresh session in this project
    # Any fallback is ANNOUNCED in the terminal (see fallback_notice below), so
    # the QUE-226 "failures stay visible" intent holds — we surface the swap
    # rather than hide it, but the terminal stays usable instead of dying.
    stored_sid = read_active_session(playground_html) if playground_html else None
    fallback_notice: str | None = None
    if stored_sid and transcript_exists(stored_sid, cwd):
        args = ["claude", "--resume", stored_sid]
        active_sid = stored_sid
        spawn_mode = "attach"
    elif session_id and transcript_exists(session_id, cwd):
        if stored_sid:
            fallback_notice = (
                f"stored session {stored_sid} has no transcript; "
                "re-forking from the authoring session."
            )
        new_sid = str(uuid.uuid4())
        args = ["claude", "--resume", session_id, "--fork-session", "--session-id", new_sid]
        active_sid = new_sid
        spawn_mode = "fork"
    else:
        missing = stored_sid or session_id or "(none)"
        fallback_notice = (
            f"session {missing} has no resumable transcript; starting a fresh "
            "session in this project. Prior conversation context is not carried "
            "over — the review doc path is re-sent on your next Send-to-Claude."
        )
        new_sid = str(uuid.uuid4())
        args = ["claude", "--session-id", new_sid]
        active_sid = new_sid
        spawn_mode = "fresh"

    try:
        proc = _pty_spawn(args, cwd=cwd, env=env)
    except Exception as exc:
        try:
            ws_send_frame(
                sock,
                OP_TEXT,
                json.dumps({"error": f"failed to spawn claude: {exc}"}).encode(),
            )
            ws_send_frame(sock, OP_CLOSE, b"")
        except OSError:
            pass
        return

    # Persist the new fork's SID by writing it into the HTML's ACTIVE_SESSION
    # constant. A persist failure isn't fatal (the spawn already succeeded),
    # but we surface it so the user knows future opens will fork-fresh
    # instead of being sticky.
    if spawn_mode in ("fork", "fresh") and playground_html:
        try:
            write_active_session(playground_html, active_sid)
        except (OSError, RuntimeError) as exc:
            try:
                ws_send_frame(
                    sock,
                    OP_TEXT,
                    json.dumps(
                        {
                            "type": "session_persist_failed",
                            "msg": (
                                f"spawned fork {active_sid} but couldn't update "
                                f"ACTIVE_SESSION in {playground_html.name}: {exc}. "
                                "future opens will spawn fresh forks instead of "
                                "re-attaching."
                            ),
                        }
                    ).encode(),
                )
            except OSError:
                pass

    # Always announce the active session so the playground UI can display the
    # right SID in the handoff bar — same message shape whether we attached or
    # forked, plus a `mode` discriminator the UI can use to badge fork sessions.
    try:
        ws_send_frame(
            sock,
            OP_TEXT,
            json.dumps(
                {"type": "active_session", "sid": active_sid, "mode": spawn_mode}
            ).encode(),
        )
    except OSError:
        pass

    # Surface any session fallback directly in the terminal so the swap is
    # visible (QUE-226 intent) rather than a silent substitution. Sent as a
    # binary frame so xterm.js renders it inline alongside claude's output.
    if fallback_notice:
        try:
            ws_send_frame(
                sock, OP_BINARY, ("\r\n[devserver] " + fallback_notice + "\r\n").encode()
            )
        except OSError:
            pass

    pty_fd = proc.fd
    stop = threading.Event()

    def pty_to_ws() -> None:
        try:
            while not stop.is_set():
                try:
                    data = os.read(pty_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                try:
                    ws_send_frame(sock, OP_BINARY, data)
                except OSError:
                    break
        finally:
            stop.set()

    def ws_to_pty() -> None:
        try:
            while not stop.is_set():
                frame = ws_read_frame(sock)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == OP_CLOSE:
                    break
                if opcode == OP_PING:
                    try:
                        ws_send_frame(sock, OP_PONG, payload)
                    except OSError:
                        break
                    continue
                if opcode == OP_TEXT:
                    try:
                        msg = json.loads(payload.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(msg, dict) and msg.get("type") == "resize":
                        try:
                            rows = int(msg["rows"])
                            cols = int(msg["cols"])
                            proc.setwinsize(rows, cols)
                        except (KeyError, ValueError, OSError):
                            pass
                    continue
                if opcode == OP_BINARY:
                    try:
                        os.write(pty_fd, payload)
                    except OSError:
                        break
        finally:
            stop.set()

    t1 = threading.Thread(target=pty_to_ws, name="claude-pty->ws", daemon=True)
    t2 = threading.Thread(target=ws_to_pty, name="claude-ws->pty", daemon=True)
    t1.start()
    t2.start()
    try:
        stop.wait()
    finally:
        try:
            if proc.isalive():
                proc.terminate(force=True)
        except Exception:
            pass
        try:
            ws_send_frame(sock, OP_CLOSE, b"")
        except OSError:
            pass
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        t1.join(timeout=2)
        t2.join(timeout=2)


class DevHandler(SimpleHTTPRequestHandler):
    """Extends SimpleHTTPRequestHandler with a WebSocket endpoint for the PTY bridge."""

    spawn_cwd: str = os.getcwd()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/claude" and (
            self.headers.get("Upgrade", "").lower() == "websocket"
        ):
            qs = parse_qs(parsed.query)
            session_values = qs.get("session", [])
            session_id = session_values[0] if session_values else ""
            # QUE-226: optional `playground` param identifies which playground
            # HTML triggered this WS. The devserver reads/writes that HTML's
            # ACTIVE_SESSION constant for sticky-session behavior. Absent or
            # unrecognized → falls back to the pre-QUE-226 always-fork path.
            playground_values = qs.get("playground", [])
            playground_path = playground_values[0] if playground_values else ""
            self.handle_claude_upgrade(session_id, playground_path)
            return
        super().do_GET()

    def handle_claude_upgrade(self, session_id: str, playground_path: str = "") -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        version = self.headers.get("Sec-WebSocket-Version", "")
        if not key or version != "13":
            self.send_error(400, "Bad WebSocket upgrade")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()
        ).decode()
        self.close_connection = True
        self.wfile.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            ).encode()
        )
        self.wfile.flush()
        playground_html = (
            resolve_safe_html_target(playground_path, self.spawn_cwd)
            if playground_path
            else None
        )
        try:
            bridge_ws_to_claude_pty(
                self.connection, self.spawn_cwd, session_id, playground_html
            )
        except Exception as exc:
            self.log_message("WS /api/claude bridge error: %s", exc)

    def do_PUT(self) -> None:
        """Accept `PUT /*-layouts.json` to persist named layouts next to the HTML.

        Narrowly scoped: only filenames matching `*-layouts.json` within the
        spawn cwd are accepted. Everything else is a 403. Body must be JSON and
        within `LAYOUTS_MAX_BYTES`. Writes atomically via tmp + rename.
        """
        parsed = urlparse(self.path)
        target = resolve_safe_layouts_target(parsed.path, self.spawn_cwd)
        if target is None:
            self.send_error(403, "PUT only permitted on *-layouts.json under server root")
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("application/json"):
            self.send_error(415, "Content-Type must be application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return
        if length <= 0:
            self.send_error(400, "Empty body")
            return
        if length > LAYOUTS_MAX_BYTES:
            self.send_error(413, f"Body exceeds {LAYOUTS_MAX_BYTES} bytes")
            return
        body = self.rfile.read(length)
        try:
            json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400, "Body is not valid JSON")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(body)
            os.replace(tmp, target)
        except OSError as exc:
            self.send_error(500, f"Write failed: {exc}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return
        self.send_response(204)
        self.end_headers()

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        # Quieter logging — skip 200/304 GETs
        if len(args) >= 2 and str(args[1]) in ("200", "304"):
            return
        super().log_message(format, *args)


# =============================================================================
# Project-scoped discovery (TT-150) — devserver instances are intrinsically
# tied to their spawn cwd (static root, PUT containment base, PTY-bridge
# `claude --resume` cwd). When the skill kickoff runs from project A, we must
# only reuse devservers whose `/proc/<pid>/cwd` resolves to project A's root.
# Sharing across projects would serve the wrong files and would attach the
# PTY bridge to the wrong transcript.
# =============================================================================

PORT_SCAN_RANGE = range(8765, 8800)
SPAWN_PORT_WAIT_TIMEOUT = 5.0
SPAWN_PORT_WAIT_INTERVAL = 0.05


def lsof_listening_pids(port: int) -> list[int]:
    """Return PIDs holding a LISTEN socket on `port`, or [] if none / lsof missing."""
    try:
        out = subprocess.run(
            ["lsof", "-t", "-i", f":{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def pid_cwd(pid: int) -> Path | None:
    """Return realpath of /proc/<pid>/cwd, or None if not introspectable."""
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        return None


def devserver_on_port_matches_cwd(port: int, project_root: Path) -> bool:
    """True iff any LISTEN-er on `port` has cwd resolving to `project_root`."""
    for pid in lsof_listening_pids(port):
        cwd = pid_cwd(pid)
        if cwd is not None and cwd == project_root:
            return True
    return False


def pgrep_review_suite_devservers() -> list[int]:
    """Return PIDs whose command line matches `review-suite.*devserver\\.py`."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", r"review-suite.*devserver\.py"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def socket_inodes_for_pid(pid: int) -> set[str]:
    """Return the set of socket inodes referenced by `pid`'s open fds.

    Each fd in `/proc/<pid>/fd/` whose readlink starts with `socket:[<inode>]`
    contributes its inode. Returns empty set on any access error (pid gone,
    permission denied, etc.).
    """
    inodes: set[str] = set()
    try:
        entries = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return inodes
    for entry in entries:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{entry}")
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            inodes.add(target[len("socket:[") : -1])
    return inodes


def listening_port_for_pid(pid: int) -> int | None:
    """Return the first TCP LISTEN port held by `pid`, or None.

    Pure-`/proc` implementation — avoids `lsof -p <pid>` which can take many
    seconds on hosts with filesystem mounts that stall lsof's initial walk
    (CIFS, FUSE, Docker overlays). Reads `/proc/<pid>/fd/*` for socket
    inodes, then matches them against LISTEN rows in `/proc/net/tcp` and
    `/proc/net/tcp6`. State `0A` is TCP_LISTEN.

    Columns in /proc/net/tcp:
        sl  local_address  rem  st  tx/rx  tr/tm  retrnsmt  uid  timeout  inode  ...
        0   1              2    3   4      5      6         7    8        9
    `local_address` is `<hex-addr>:<hex-port>`; we want the hex port.
    """
    inodes = socket_inodes_for_pid(pid)
    if not inodes:
        return None
    for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_path) as fh:
                next(fh, None)  # header
                for line in fh:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    if parts[3] != "0A":  # TCP_LISTEN
                        continue
                    if parts[9] not in inodes:
                        continue
                    local = parts[1]
                    colon = local.rfind(":")
                    if colon < 0:
                        continue
                    try:
                        return int(local[colon + 1 :], 16)
                    except ValueError:
                        continue
        except OSError:
            continue
    return None


def find_review_suite_devserver_for_cwd(project_root: Path) -> int | None:
    """Return a LISTEN port for a review-suite devserver whose cwd matches.

    Filters pgrep matches by `/proc/<pid>/cwd == project_root`. Returns the
    first matching pid's listening port, or None if no match.
    """
    for pid in pgrep_review_suite_devservers():
        cwd = pid_cwd(pid)
        if cwd is None or cwd != project_root:
            continue
        port = listening_port_for_pid(pid)
        if port is not None:
            return port
    return None


def port_is_free(port: int) -> bool:
    """True iff `port` is not currently held by any LISTEN socket."""
    return not lsof_listening_pids(port)


def pick_free_port(requested: int | None) -> int:
    """Resolve a port to bind. Honors `requested` if free, otherwise scans 8765-8799.

    Raises RuntimeError on a requested-port collision or if the scan exhausts.
    """
    if requested is not None:
        if not port_is_free(requested):
            raise RuntimeError(f"port {requested} is already in use")
        return requested
    for candidate in PORT_SCAN_RANGE:
        if port_is_free(candidate):
            return candidate
    raise RuntimeError(f"no free port in {PORT_SCAN_RANGE.start}-{PORT_SCAN_RANGE.stop - 1}")


def wait_for_listen(port: int, timeout: float = SPAWN_PORT_WAIT_TIMEOUT) -> bool:
    """Poll until `port` has a LISTEN-er, or `timeout` elapses. True on success."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lsof_listening_pids(port):
            return True
        time.sleep(SPAWN_PORT_WAIT_INTERVAL)
    return False


def spawn_background_devserver(port: int, project_root: Path) -> subprocess.Popen[bytes]:
    """Spawn `python3 devserver.py <port>` detached, with stdio captured to a log.

    `project_root` is the cwd passed to the child — the devserver reads it
    via os.getcwd() at startup and uses it as the static root, PUT containment
    base, and PTY-bridge spawn cwd. start_new_session detaches the child so
    it survives the find-or-start parent exit.
    """
    log_dir = project_root / ".plan-review"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ".devserver.log"
    # Open in append mode so successive spawns accumulate. The log file is
    # not part of the public contract; it exists for human debugging.
    log_fh = log_path.open("ab")
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(port)],
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )


def write_port_file(project_root: Path, port: int) -> None:
    """Persist the active port under `<project_root>/.plan-review/.devserver-port`."""
    port_dir = project_root / ".plan-review"
    port_dir.mkdir(parents=True, exist_ok=True)
    (port_dir / ".devserver-port").write_text(f"{port}\n")


def read_port_file(project_root: Path) -> int | None:
    """Return the port recorded under `.plan-review/.devserver-port`, or None."""
    port_file = project_root / ".plan-review" / ".devserver-port"
    try:
        raw = port_file.read_text().strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def emit_kv(url: str, port: int, lan_ip: str) -> None:
    """Print the find-or-start key=value envelope expected by SKILL.md kickoffs.

    Three single-quoted shell-safe assignments so SKILL.md can `eval` the
    output. `shlex.quote` defends against pathological LAN-IP values (e.g. a
    REVIEW_SUITE_HOST override containing whitespace) — the port is an int
    so it's always safe, but quoting all three keeps the contract uniform.
    """
    print(f"URL={shlex.quote(url)}")
    print(f"PORT={shlex.quote(str(port))}")
    print(f"LAN_IP={shlex.quote(lan_ip)}")


def find_or_start(port_arg: int | None) -> int:
    """Subcommand entry: discover or spawn a project-scoped devserver, print URL=/PORT=/LAN_IP=.

    Returns the process exit code (0 on success, non-zero on failure).

    Order of operations:
      1. `<project_root>/.plan-review/.devserver-port` → verify the listener's
         cwd matches project_root. Reuse if it does.
      2. If no port arg was given, scan `pgrep -f "review-suite.*devserver\\.py"`
         matches and reuse the first whose cwd matches project_root.
      3. Otherwise spawn a fresh devserver in project_root, write the port file,
         and emit the URL once the port is listening.

    An explicit `port_arg` skips the pgrep step (the user asked for that specific
    port). It still consults the port file fast path because if a server is
    already running on the requested port AND it belongs to this project, we
    should reuse rather than fail.
    """
    project_root = Path.cwd().resolve()
    lan_ip = resolve_lan_ip()

    # Step 1: port-file fast path. Reuse only if the listener is ours.
    saved = read_port_file(project_root)
    if saved is not None and devserver_on_port_matches_cwd(saved, project_root):
        if port_arg is None or port_arg == saved:
            emit_kv(f"http://{lan_ip}:{saved}/", saved, lan_ip)
            return 0

    # Step 2: pgrep fallback, scoped to this project's cwd. Skipped if the
    # user gave an explicit port arg — they want that port specifically, not
    # whatever happens to be running.
    if port_arg is None:
        found = find_review_suite_devserver_for_cwd(project_root)
        if found is not None:
            write_port_file(project_root, found)
            emit_kv(f"http://{lan_ip}:{found}/", found, lan_ip)
            return 0

    # Step 3: spawn fresh.
    try:
        port = pick_free_port(port_arg)
    except RuntimeError as exc:
        print(f"devserver find-or-start: {exc}", file=sys.stderr)
        return 1
    spawn_background_devserver(port, project_root)
    if not wait_for_listen(port):
        print(
            f"devserver find-or-start: spawned child on port {port} did not "
            "begin listening within timeout; check .plan-review/.devserver.log",
            file=sys.stderr,
        )
        return 1
    write_port_file(project_root, port)
    emit_kv(f"http://{lan_ip}:{port}/", port, lan_ip)
    return 0


def main() -> None:
    # Subcommand dispatch: `find-or-start [PORT]` runs the project-scoped
    # discovery logic and exits. Everything else falls through to the legacy
    # foreground server mode for backward compatibility (skills that haven't
    # been updated yet, direct `python3 devserver.py 8765` invocations).
    if len(sys.argv) > 1 and sys.argv[1] == "find-or-start":
        port_arg: int | None = None
        if len(sys.argv) > 2 and sys.argv[2]:
            try:
                port_arg = int(sys.argv[2])
            except ValueError:
                print(
                    f"devserver find-or-start: invalid port arg {sys.argv[2]!r}",
                    file=sys.stderr,
                )
                sys.exit(2)
        sys.exit(find_or_start(port_arg))

    port = int(os.environ.get("REVIEW_SUITE_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8765))

    # Spawn cwd = current working directory at startup. The skill invokes the
    # devserver from the user's project root (no `cd` first), so this is
    # naturally the right place for `claude --resume <sid>` to find the transcript.
    DevHandler.spawn_cwd = os.getcwd()

    class ReusableThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True

        def server_bind(self) -> None:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            super().server_bind()

    server = ReusableThreadingHTTPServer(("0.0.0.0", port), DevHandler)
    lan_ip = resolve_lan_ip()
    print(f"review-suite devserver: http://{lan_ip}:{port}/")
    print(f"Serving: {Path.cwd()}")
    print(f"PTY bridge spawns `claude --resume <sid>` from: {DevHandler.spawn_cwd}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
