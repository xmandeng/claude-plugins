"""Integration tests for the persistent-PTY bridge in devserver.py.

The bridge keeps one live child per playground across WebSocket reconnects so a
browser reload reattaches to the SAME conversation instead of killing it and
forking a fresh one. These tests drive ``bridge_ws_to_claude_pty`` over a real
socketpair speaking the WebSocket wire protocol, with ``_pty_spawn`` swapped for
a tiny stand-in process (no real ``claude`` binary needed). They assert the two
properties the feature exists for:

  1. A dropped WebSocket detaches the client but does NOT kill the child.
  2. A reconnect reuses the same process and replays its recent output.
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

import devserver  # type: ignore[import-not-found]  # added to sys.path by conftest

# Stand-in for `claude`: emit a recognizable banner shortly after start (so the
# first client is attached before it lands and receives it live), then idle so
# the process stays alive long enough for the reconnect to find it.
_STANDIN = (
    "import sys, time; time.sleep(0.3); "
    "sys.stdout.write('BANNER-READY\\n'); sys.stdout.flush(); "
    "time.sleep(30)"
)
_MARKER = b"BANNER-READY"


def _spawn_standin(_args: list[str], cwd: str, env: dict[str, str]) -> object:
    """Drop-in for ``_pty_spawn``: ignores argv and runs the stand-in process."""
    return devserver._StdlibPty(["python3", "-c", _STANDIN], cwd=cwd, env=env)


def _drain_until(sock: socket.socket, marker: bytes | None, deadline: float):
    """Read frames until ``marker`` appears in BINARY output or time runs out.

    Returns ``(texts, binary_bytes)`` collected so far.
    """
    texts: list[bytes] = []
    binary = bytearray()
    while time.time() < deadline:
        sock.settimeout(max(0.05, deadline - time.time()))
        try:
            frame = devserver.ws_read_frame(sock)
        except OSError:
            break
        if frame is None:
            break
        opcode, payload = frame
        if opcode == devserver.OP_TEXT:
            texts.append(payload)
        elif opcode == devserver.OP_BINARY:
            binary.extend(payload)
            if marker is not None and marker in binary:
                break
    return texts, bytes(binary)


def test_reattach_preserves_process_across_reload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(devserver, "_pty_spawn", _spawn_standin)
    # Cold start forks from the authoring session; pretend its transcript exists.
    monkeypatch.setattr(devserver, "transcript_exists", lambda _sid, _cwd: True)

    session_id = "integration-test-session"
    key = f"session:{session_id}"
    devserver._sessions.pop(key, None)

    # --- First open -------------------------------------------------------
    client1, server1 = socket.socketpair()
    bridge1 = threading.Thread(
        target=devserver.bridge_ws_to_claude_pty,
        args=(server1, str(tmp_path), session_id, None),
        daemon=True,
    )
    bridge1.start()

    texts, binary = _drain_until(client1, _MARKER, time.time() + 3.0)
    assert any(b'"type": "active_session"' in t and b'"mode": "fork"' in t for t in texts), (
        f"expected a fork active_session frame, got {texts!r}"
    )
    assert _MARKER in binary, "banner should reach the first client live"

    assert key in devserver._sessions
    sess = devserver._sessions[key]
    pid_before = sess.proc.pid

    # --- Reload: drop the WebSocket --------------------------------------
    client1.close()
    bridge1.join(timeout=3)
    assert not bridge1.is_alive(), "bridge thread should return after WS close"

    # The child must still be alive and registered -- the whole point.
    assert sess.alive(), "child must survive a dropped WebSocket"
    assert devserver._sessions.get(key) is sess

    # --- Reconnect: reuse + replay ---------------------------------------
    client2, server2 = socket.socketpair()
    bridge2 = threading.Thread(
        target=devserver.bridge_ws_to_claude_pty,
        args=(server2, str(tmp_path), session_id, None),
        daemon=True,
    )
    bridge2.start()

    texts2, binary2 = _drain_until(client2, _MARKER, time.time() + 3.0)
    assert any(b'"type": "active_session"' in t and b'"mode": "attach"' in t for t in texts2), (
        f"reconnect should report attach mode, got {texts2!r}"
    )
    assert _MARKER in binary2, "replay buffer should repaint prior output on reconnect"
    assert devserver._sessions[key].proc.pid == pid_before, "must reuse the same process"

    # --- Cleanup ----------------------------------------------------------
    client2.close()
    bridge2.join(timeout=3)
    sess.terminate()
    devserver._sessions.pop(key, None)


def test_dropped_socket_does_not_kill_idle_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Even with no immediate reconnect, the child lingers (reaper handles TTL)."""
    monkeypatch.setattr(devserver, "_pty_spawn", _spawn_standin)
    monkeypatch.setattr(devserver, "transcript_exists", lambda _sid, _cwd: True)

    session_id = "integration-idle-session"
    key = f"session:{session_id}"
    devserver._sessions.pop(key, None)

    client, server = socket.socketpair()
    bridge = threading.Thread(
        target=devserver.bridge_ws_to_claude_pty,
        args=(server, str(tmp_path), session_id, None),
        daemon=True,
    )
    bridge.start()
    _drain_until(client, _MARKER, time.time() + 3.0)
    sess = devserver._sessions[key]

    client.close()
    bridge.join(timeout=3)

    # Briefly wait and confirm the process was not reaped (TTL is an hour).
    time.sleep(0.5)
    assert sess.alive()
    assert devserver._sessions.get(key) is sess

    sess.terminate()
    devserver._sessions.pop(key, None)
