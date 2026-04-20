"""Unit tests for architecture-review devserver.

Covers the pure logic: WebSocket framing (RFC 6455), LAN IP resolution,
the DevHandler log-filtering tweak, and the `*-layouts.json` PUT path
resolution. PTY/fork paths, the HTTP server itself, and the full WS->PTY
bridge are integration concerns and are not exercised here.
"""

import json
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import devserver  # type: ignore[import-not-found]  # added to sys.path by conftest


# =============================================================================
# ws_recv_exactly
# =============================================================================


class FakeSocket:
    """Socket stub that yields queued chunks from recv()."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = bytearray()

    def recv(self, n):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        # Honor the caller's requested size so tests can simulate fragmentation
        if len(chunk) > n:
            self._chunks.insert(0, chunk[n:])
            return chunk[:n]
        return chunk

    def sendall(self, data):
        self.sent.extend(data)


class TestWsRecvExactly:
    def test_reads_exactly_n_bytes_in_one_chunk(self):
        sock = FakeSocket([b"hello world"])
        assert devserver.ws_recv_exactly(sock, 5) == b"hello"

    def test_assembles_fragmented_recv(self):
        sock = FakeSocket([b"he", b"ll", b"o"])
        assert devserver.ws_recv_exactly(sock, 5) == b"hello"

    def test_returns_none_on_eof_before_full_read(self):
        sock = FakeSocket([b"he", b""])
        assert devserver.ws_recv_exactly(sock, 5) is None

    def test_returns_none_on_oserror(self):
        sock = FakeSocket([OSError("boom")])
        assert devserver.ws_recv_exactly(sock, 4) is None

    def test_zero_bytes_is_empty(self):
        sock = FakeSocket([])
        assert devserver.ws_recv_exactly(sock, 0) == b""


# =============================================================================
# ws_send_frame — RFC 6455 server framing (FIN=1, no masking)
# =============================================================================


class TestWsSendFrame:
    def test_small_text_frame(self):
        sock = FakeSocket([])
        devserver.ws_send_frame(sock, devserver.OP_TEXT, b"hi")
        # FIN=1, opcode=TEXT -> 0x81; len=2; no mask bit -> 0x02
        assert bytes(sock.sent) == b"\x81\x02hi"

    def test_binary_frame_at_125_byte_boundary(self):
        payload = b"x" * 125
        sock = FakeSocket([])
        devserver.ws_send_frame(sock, devserver.OP_BINARY, payload)
        assert bytes(sock.sent) == b"\x82" + bytes([125]) + payload

    def test_medium_frame_uses_16bit_extended_length(self):
        payload = b"y" * 126
        sock = FakeSocket([])
        devserver.ws_send_frame(sock, devserver.OP_BINARY, payload)
        assert sock.sent[0] == 0x82
        assert sock.sent[1] == 126
        assert struct.unpack("!H", bytes(sock.sent[2:4]))[0] == 126
        assert bytes(sock.sent[4:]) == payload

    def test_large_frame_uses_64bit_extended_length(self):
        payload = b"z" * (1 << 16)
        sock = FakeSocket([])
        devserver.ws_send_frame(sock, devserver.OP_BINARY, payload)
        assert sock.sent[0] == 0x82
        assert sock.sent[1] == 127
        assert struct.unpack("!Q", bytes(sock.sent[2:10]))[0] == (1 << 16)
        assert bytes(sock.sent[10:]) == payload

    def test_close_frame_empty_payload(self):
        sock = FakeSocket([])
        devserver.ws_send_frame(sock, devserver.OP_CLOSE, b"")
        assert bytes(sock.sent) == b"\x88\x00"

    def test_server_frames_are_never_masked(self):
        sock = FakeSocket([])
        devserver.ws_send_frame(sock, devserver.OP_TEXT, b"abc")
        # Length byte's top bit (mask) must be 0
        assert sock.sent[1] & 0x80 == 0


# =============================================================================
# ws_read_frame — parsing incoming client frames (always masked per RFC 6455)
# =============================================================================


def _client_frame(opcode: int, payload: bytes, mask: bytes = b"\x00\x00\x00\x00") -> bytes:
    """Build a FIN=1 client-to-server frame with the given 4-byte mask."""
    assert len(mask) == 4
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < (1 << 16):
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


class TestWsReadFrame:
    def test_parses_small_masked_text_frame(self):
        frame = _client_frame(devserver.OP_TEXT, b"ping", mask=b"\x11\x22\x33\x44")
        sock = FakeSocket([frame])
        result = devserver.ws_read_frame(sock)
        assert result == (devserver.OP_TEXT, b"ping")

    def test_parses_binary_frame_with_16bit_length(self):
        payload = b"A" * 200
        frame = _client_frame(devserver.OP_BINARY, payload, mask=b"\x01\x02\x03\x04")
        sock = FakeSocket([frame])
        result = devserver.ws_read_frame(sock)
        assert result == (devserver.OP_BINARY, payload)

    def test_parses_binary_frame_with_64bit_length(self):
        payload = b"B" * (1 << 16)
        frame = _client_frame(devserver.OP_BINARY, payload, mask=b"\xaa\xbb\xcc\xdd")
        sock = FakeSocket([frame])
        result = devserver.ws_read_frame(sock)
        assert result == (devserver.OP_BINARY, payload)

    def test_parses_unmasked_server_style_frame(self):
        # Server-style frames (no mask bit) are unusual from a client but the
        # parser still handles them — ensure we don't apply a stale mask.
        sock = FakeSocket([b"\x81\x03abc"])
        result = devserver.ws_read_frame(sock)
        assert result == (devserver.OP_TEXT, b"abc")

    def test_parses_close_frame(self):
        frame = _client_frame(devserver.OP_CLOSE, b"", mask=b"\x00\x00\x00\x00")
        sock = FakeSocket([frame])
        result = devserver.ws_read_frame(sock)
        assert result == (devserver.OP_CLOSE, b"")

    def test_returns_none_on_truncated_header(self):
        sock = FakeSocket([b"\x81"])  # only 1 byte, need 2
        assert devserver.ws_read_frame(sock) is None

    def test_returns_none_on_truncated_extended_length(self):
        # Header says 16-bit extended length, but we only provide 1 byte of it
        sock = FakeSocket([b"\x81\x7e", b"\x00"])
        assert devserver.ws_read_frame(sock) is None

    def test_returns_none_on_truncated_mask_key(self):
        # Mask bit set, length 4, but we only supply 2 bytes of mask key
        sock = FakeSocket([b"\x81\x84", b"\x00\x00"])
        assert devserver.ws_read_frame(sock) is None

    def test_returns_none_on_truncated_payload(self):
        # Claim 10 bytes, provide 3
        sock = FakeSocket([b"\x82\x0a", b"abc"])
        assert devserver.ws_read_frame(sock) is None


class TestWsRoundtrip:
    """Server frames aren't masked, so ws_read_frame should parse our own output."""

    @pytest.mark.parametrize("size", [0, 1, 125, 126, 127, 1024, (1 << 16)])
    def test_roundtrip_binary(self, size):
        payload = bytes(range(256)) * (size // 256 + 1)
        payload = payload[:size]
        sock_out = FakeSocket([])
        devserver.ws_send_frame(sock_out, devserver.OP_BINARY, payload)
        sock_in = FakeSocket([bytes(sock_out.sent)])
        result = devserver.ws_read_frame(sock_in)
        assert result == (devserver.OP_BINARY, payload)


# =============================================================================
# resolve_lan_ip
# =============================================================================


class TestResolveLanIp:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("ARCHITECTURE_REVIEW_HOST", "10.0.0.42")
        assert devserver.resolve_lan_ip() == "10.0.0.42"

    def test_uses_connected_socket_name(self, monkeypatch):
        monkeypatch.delenv("ARCHITECTURE_REVIEW_HOST", raising=False)
        fake_sock = MagicMock()
        fake_sock.getsockname.return_value = ("192.168.1.50", 54321)
        with patch.object(devserver.socket, "socket", return_value=fake_sock):
            assert devserver.resolve_lan_ip() == "192.168.1.50"

    def test_loopback_address_falls_back_to_localhost(self, monkeypatch):
        monkeypatch.delenv("ARCHITECTURE_REVIEW_HOST", raising=False)
        fake_sock = MagicMock()
        fake_sock.getsockname.return_value = ("127.0.0.1", 54321)
        with patch.object(devserver.socket, "socket", return_value=fake_sock):
            assert devserver.resolve_lan_ip() == "localhost"

    def test_oserror_falls_back_to_localhost(self, monkeypatch):
        monkeypatch.delenv("ARCHITECTURE_REVIEW_HOST", raising=False)
        with patch.object(devserver.socket, "socket", side_effect=OSError("no net")):
            assert devserver.resolve_lan_ip() == "localhost"


# =============================================================================
# DevHandler.log_message — custom filter that silences 200/304 noise
# =============================================================================


class TestDevHandlerLogMessage:
    def _make_handler(self):
        # Bypass __init__ — we only need the method under test
        return devserver.DevHandler.__new__(devserver.DevHandler)

    def test_suppresses_200(self):
        handler = self._make_handler()
        with patch.object(
            devserver.SimpleHTTPRequestHandler, "log_message"
        ) as parent_log:
            handler.log_message('"%s" %s %s', "GET /", "200", "-")
            parent_log.assert_not_called()

    def test_suppresses_304(self):
        handler = self._make_handler()
        with patch.object(
            devserver.SimpleHTTPRequestHandler, "log_message"
        ) as parent_log:
            handler.log_message('"%s" %s %s', "GET /", "304", "-")
            parent_log.assert_not_called()

    def test_passes_through_404(self):
        handler = self._make_handler()
        with patch.object(
            devserver.SimpleHTTPRequestHandler, "log_message"
        ) as parent_log:
            handler.log_message('"%s" %s %s', "GET /missing", "404", "-")
            parent_log.assert_called_once()

    def test_passes_through_when_args_are_short(self):
        # Fewer than 2 args -> can't be the GET-response shape, should pass through
        handler = self._make_handler()
        with patch.object(
            devserver.SimpleHTTPRequestHandler, "log_message"
        ) as parent_log:
            handler.log_message("just a message")
            parent_log.assert_called_once()


# =============================================================================
# bridge_ws_to_claude_pty — missing session_id is a pure early-exit path
# =============================================================================


class TestBridgeMissingSession:
    def test_empty_session_id_sends_error_and_closes(self):
        sock = FakeSocket([])
        devserver.bridge_ws_to_claude_pty(sock, cwd="/tmp", session_id="")
        # Parse the two frames we expect: a TEXT error, then a CLOSE
        first = devserver.ws_read_frame(FakeSocket([bytes(sock.sent)]))
        assert first is not None
        opcode, payload = first
        assert opcode == devserver.OP_TEXT
        msg = json.loads(payload.decode())
        assert "error" in msg
        assert "session" in msg["error"].lower()


# =============================================================================
# resolve_safe_layouts_target — scopes PUT writes to *-layouts.json under cwd
# =============================================================================


class TestResolveSafeLayoutsTarget:
    def test_accepts_layouts_json_at_root(self, tmp_path):
        target = devserver.resolve_safe_layouts_target("/foo-layouts.json", str(tmp_path))
        assert target == (tmp_path / "foo-layouts.json").resolve()

    def test_accepts_layouts_json_in_subdir(self, tmp_path):
        target = devserver.resolve_safe_layouts_target(
            "/.architecture-review/TT-131-architecture-review-layouts.json", str(tmp_path)
        )
        assert target is not None
        assert target.name == "TT-131-architecture-review-layouts.json"
        assert target.parent.name == ".architecture-review"

    def test_strips_query_string(self, tmp_path):
        target = devserver.resolve_safe_layouts_target(
            "/foo-layouts.json?cachebust=42", str(tmp_path)
        )
        assert target == (tmp_path / "foo-layouts.json").resolve()

    def test_rejects_non_layouts_filename(self, tmp_path):
        assert devserver.resolve_safe_layouts_target("/index.html", str(tmp_path)) is None
        assert devserver.resolve_safe_layouts_target("/secret.json", str(tmp_path)) is None
        assert (
            devserver.resolve_safe_layouts_target("/foo-layouts.json.bak", str(tmp_path))
            is None
        )

    def test_rejects_path_traversal(self, tmp_path):
        # ../ escape outside spawn cwd
        assert (
            devserver.resolve_safe_layouts_target("/../evil-layouts.json", str(tmp_path))
            is None
        )
        assert (
            devserver.resolve_safe_layouts_target(
                "/sub/../../evil-layouts.json", str(tmp_path)
            )
            is None
        )

    def test_rejects_empty_path(self, tmp_path):
        assert devserver.resolve_safe_layouts_target("/", str(tmp_path)) is None
        assert devserver.resolve_safe_layouts_target("", str(tmp_path)) is None


# =============================================================================
# DevHandler.do_PUT — end-to-end path through the request handler. We build a
# minimal fake request and exercise the handler directly rather than standing
# up a full HTTP server.
# =============================================================================


import io


class FakeDevRequest:
    """Minimal rfile/wfile wrapper to drive DevHandler.do_PUT without a socket."""

    def __init__(self, body: bytes):
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()


def build_put_handler(
    tmp_path: Path,
    request_path: str,
    body: bytes,
    content_type: str = "application/json",
    content_length: str | None = None,
) -> tuple[devserver.DevHandler, FakeDevRequest]:
    """Construct a DevHandler wired up to a fake request, ready for do_PUT()."""
    fake = FakeDevRequest(body)
    handler = devserver.DevHandler.__new__(devserver.DevHandler)
    handler.rfile = fake.rfile
    handler.wfile = fake.wfile
    handler.path = request_path
    handler.command = "PUT"
    handler.request_version = "HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.requestline = f"PUT {request_path} HTTP/1.1"
    handler.server = MagicMock()
    handler.headers = {
        "Content-Type": content_type,
        "Content-Length": content_length if content_length is not None else str(len(body)),
    }
    handler.spawn_cwd = str(tmp_path)
    handler.close_connection = False

    # send_error / send_response / end_headers write status bytes — but we only
    # care about the status code, so intercept and record instead of emitting.
    handler._status: list[tuple[int, str | None]] = []  # type: ignore[attr-defined]

    def record_error(code, message=None):
        handler._status.append((code, message))
        handler.close_connection = True

    def record_response(code, message=None):
        handler._status.append((code, message))

    handler.send_error = record_error  # type: ignore[assignment]
    handler.send_response = record_response  # type: ignore[assignment]
    handler.send_header = MagicMock(return_value=None)  # type: ignore[assignment]
    handler.end_headers = MagicMock(return_value=None)  # type: ignore[assignment]
    return handler, fake


class TestDoPutHappyPath:
    def test_writes_valid_layouts_json(self, tmp_path):
        payload = json.dumps({"named": {"a": {"x": 10, "y": 20}}}).encode()
        handler, _ = build_put_handler(tmp_path, "/foo-layouts.json", payload)
        handler.do_PUT()
        assert handler._status == [(204, None)]
        written = (tmp_path / "foo-layouts.json").read_bytes()
        assert written == payload

    def test_creates_parent_dirs(self, tmp_path):
        payload = b"{}"
        handler, _ = build_put_handler(
            tmp_path, "/.architecture-review/TT-1-layouts.json", payload
        )
        handler.do_PUT()
        assert handler._status == [(204, None)]
        assert (tmp_path / ".architecture-review" / "TT-1-layouts.json").exists()

    def test_overwrites_existing_atomically(self, tmp_path):
        target = tmp_path / "foo-layouts.json"
        target.write_bytes(b'{"old": true}')
        payload = b'{"new": true}'
        handler, _ = build_put_handler(tmp_path, "/foo-layouts.json", payload)
        handler.do_PUT()
        assert target.read_bytes() == payload
        # Ensure no dangling .tmp left behind
        assert not target.with_suffix(target.suffix + ".tmp").exists()

    def test_accepts_content_type_with_charset(self, tmp_path):
        payload = b"{}"
        handler, _ = build_put_handler(
            tmp_path,
            "/foo-layouts.json",
            payload,
            content_type="application/json; charset=utf-8",
        )
        handler.do_PUT()
        assert handler._status == [(204, None)]


class TestDoPutRejections:
    def test_non_layouts_filename_returns_403(self, tmp_path):
        handler, _ = build_put_handler(tmp_path, "/evil.html", b"{}")
        handler.do_PUT()
        assert handler._status[0][0] == 403

    def test_path_traversal_returns_403(self, tmp_path):
        handler, _ = build_put_handler(tmp_path, "/../outside-layouts.json", b"{}")
        handler.do_PUT()
        assert handler._status[0][0] == 403

    def test_wrong_content_type_returns_415(self, tmp_path):
        handler, _ = build_put_handler(
            tmp_path, "/foo-layouts.json", b"{}", content_type="text/plain"
        )
        handler.do_PUT()
        assert handler._status[0][0] == 415

    def test_oversize_body_returns_413(self, tmp_path):
        # Don't actually allocate 257 KB — set Content-Length manually so the
        # handler rejects before reading. The handler trusts Content-Length.
        handler, _ = build_put_handler(
            tmp_path,
            "/foo-layouts.json",
            b"{}",
            content_length=str(devserver.LAYOUTS_MAX_BYTES + 1),
        )
        handler.do_PUT()
        assert handler._status[0][0] == 413

    def test_zero_length_body_returns_400(self, tmp_path):
        handler, _ = build_put_handler(
            tmp_path, "/foo-layouts.json", b"", content_length="0"
        )
        handler.do_PUT()
        assert handler._status[0][0] == 400

    def test_invalid_json_returns_400(self, tmp_path):
        handler, _ = build_put_handler(tmp_path, "/foo-layouts.json", b"not json{")
        handler.do_PUT()
        assert handler._status[0][0] == 400

    def test_non_utf8_body_returns_400(self, tmp_path):
        handler, _ = build_put_handler(tmp_path, "/foo-layouts.json", b"\xff\xfe\x00{}")
        handler.do_PUT()
        assert handler._status[0][0] == 400

    def test_rejection_leaves_no_file_on_disk(self, tmp_path):
        handler, _ = build_put_handler(tmp_path, "/evil.html", b"{}")
        handler.do_PUT()
        # Nothing at all should have been written
        assert list(tmp_path.iterdir()) == []
