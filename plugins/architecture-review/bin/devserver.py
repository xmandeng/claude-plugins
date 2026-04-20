"""Devserver for the architecture-review plugin.

Serves the generated review HTML over HTTP, bridges a browser xterm.js terminal
to a local `claude --resume <session-id>` PTY via WebSocket, and accepts PUT
uploads of `*-layouts.json` files so the "saved named layouts" feature of the
architecture template can persist to disk next to the HTML.

Usage:
    python3 devserver.py [port]          # default port: 8775

Serves the current working directory (project root; the skill launches this
without `cd`ing first so the PTY bridge spawns `claude --resume <sid>` in the
same project the transcript was recorded under).

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
import signal
import socket
import struct
import sys
import termios
import threading
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
    override = os.environ.get("ARCHITECTURE_REVIEW_HOST")
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


def bridge_ws_to_claude_pty(sock: socket.socket, cwd: str, session_id: str) -> None:
    """Spawn `claude --resume <session_id>` in a PTY and bridge stdin/stdout to the websocket.

    Wire protocol:
      - BINARY frames in both directions carry raw PTY bytes (terminal I/O).
      - TEXT frames carry JSON control messages from the client. Currently
        only `{"type":"resize","rows":N,"cols":N}` is recognised.

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
    try:
        proc = _pty_spawn(["claude", "--resume", session_id], cwd=cwd, env=env)
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
            session_values = parse_qs(parsed.query).get("session", [])
            session_id = session_values[0] if session_values else ""
            self.handle_claude_upgrade(session_id)
            return
        super().do_GET()

    def handle_claude_upgrade(self, session_id: str) -> None:
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
        try:
            bridge_ws_to_claude_pty(self.connection, self.spawn_cwd, session_id)
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


def main() -> None:
    port = int(os.environ.get("ARCHITECTURE_REVIEW_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8775))

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
    print(f"architecture-review devserver: http://{lan_ip}:{port}/")
    print(f"Serving: {Path.cwd()}")
    print(f"PTY bridge spawns `claude --resume <sid>` from: {DevHandler.spawn_cwd}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
