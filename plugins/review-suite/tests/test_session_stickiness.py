"""Tests for the playground session bridge in devserver.py.

Design (Option A — always re-fork from the authoring session):

- The live `claude` child held in ``_sessions`` carries the conversation
  across browser reloads. There is no on-disk fork state, because an
  interactive forked `claude` never flushes a resumable transcript (only
  print mode `-p` does -- verified by an integration probe against a real
  binary, not reproducible in a unit test).
- A cold start (first open, devserver restart, reaped idle session) always
  re-forks from ``CLAUDE_SESSION`` (the authoring session), which IS
  resumable. It never attaches to a stored fork id, because none exists.

Helpers under test:

- ``resolve_safe_html_target`` — validates that the playground path passed
  via the WS query string points at a real review-suite HTML inside the
  spawn cwd (path-traversal-safe, suffix-restricted, file-must-exist).
- ``transcript_exists`` — true iff ``~/.claude/projects/<slug>/<sid>.jsonl``
  exists for the project cwd. **Not mocked** here: tests redirect ``$HOME``
  and write real transcript files, so the on-disk check runs for real. The
  previous suite stubbed this out, which is exactly why the dead-fork bug
  survived five rewrites -- the one fact that was false in reality (a forked
  transcript never lands) was the one fact the mock faked true.
- ``_cold_start_spawn`` — always returns a fork argv when the authoring
  session is resumable, else sends an error frame and returns None.

The end-to-end PTY/WS flow is not exercised here -- that needs a real
`claude` binary and is integration territory.
"""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

import pytest

import devserver  # type: ignore[import-not-found]  # added to sys.path by conftest


# A minimal HTML stub. Real templates are 80+ KB; small fixtures keep failure
# output readable and isolate regressions to the logic under test.
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>test</title></head>
<body>
<script>
const PLAN_NAME = "test plan";
const CLAUDE_SESSION = "authoring-session-id-placeholder";
const LAYOUTS_FILE = "test-layouts.json";
</script>
</body>
</html>
"""


def _write_playground(dir_path: Path, name: str) -> Path:
    """Write a stub playground HTML and return its path."""
    p = dir_path / name
    p.write_text(HTML_TEMPLATE)
    return p


def _make_transcript(home: Path, cwd: str, sid: str) -> None:
    """Create a real ``<sid>.jsonl`` under the project slug for ``cwd``.

    Mirrors how Claude records per-project transcripts so ``transcript_exists``
    -- which we deliberately do NOT mock -- finds (or doesn't find) a genuine
    file on disk.
    """
    import re

    slug = re.sub(r"[/.]", "-", cwd)
    d = home / ".claude" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text('{"type":"summary"}\n')


# ---------------------------------------------------------------------------
# resolve_safe_html_target
# ---------------------------------------------------------------------------


class TestResolveSafeHtmlTarget:
    """The playground path from the WS query string must resolve to a real
    review-suite HTML inside the spawn cwd (path-traversal-safe,
    suffix-restricted, file-must-exist)."""

    def test_resolves_design_review_html(self, tmp_path: Path) -> None:
        (tmp_path / ".design-review").mkdir()
        _write_playground(tmp_path / ".design-review", "QUE-1-foo-design-review.html")
        result = devserver.resolve_safe_html_target(
            ".design-review/QUE-1-foo-design-review.html", str(tmp_path)
        )
        assert result == tmp_path / ".design-review" / "QUE-1-foo-design-review.html"

    def test_resolves_plan_review_html(self, tmp_path: Path) -> None:
        (tmp_path / ".plan-review").mkdir()
        _write_playground(tmp_path / ".plan-review", "QUE-2-bar-review.html")
        result = devserver.resolve_safe_html_target(
            ".plan-review/QUE-2-bar-review.html", str(tmp_path)
        )
        assert result == tmp_path / ".plan-review" / "QUE-2-bar-review.html"

    def test_resolves_architecture_map_html(self, tmp_path: Path) -> None:
        (tmp_path / ".architecture-map").mkdir()
        _write_playground(tmp_path / ".architecture-map", "QUE-3-baz-architecture-map.html")
        result = devserver.resolve_safe_html_target(
            ".architecture-map/QUE-3-baz-architecture-map.html", str(tmp_path)
        )
        assert result == tmp_path / ".architecture-map" / "QUE-3-baz-architecture-map.html"

    def test_strips_query_and_fragment(self, tmp_path: Path) -> None:
        (tmp_path / ".plan-review").mkdir()
        _write_playground(tmp_path / ".plan-review", "foo-review.html")
        result = devserver.resolve_safe_html_target(
            ".plan-review/foo-review.html?nocache=1#section", str(tmp_path)
        )
        assert result is not None
        assert result.name == "foo-review.html"

    def test_strips_leading_slash(self, tmp_path: Path) -> None:
        """`location.pathname` in the browser starts with `/` — must be
        treated as project-relative, not filesystem-absolute."""
        (tmp_path / ".plan-review").mkdir()
        _write_playground(tmp_path / ".plan-review", "foo-review.html")
        result = devserver.resolve_safe_html_target(
            "/.plan-review/foo-review.html", str(tmp_path)
        )
        assert result == tmp_path / ".plan-review" / "foo-review.html"

    def test_rejects_unrecognized_suffix(self, tmp_path: Path) -> None:
        """A .html that isn't one of our three review-suite kinds is rejected
        — the devserver shouldn't manufacture session state for files it
        doesn't own."""
        (tmp_path / ".plan-review").mkdir()
        (tmp_path / ".plan-review" / "foo.html").write_text("<html></html>")
        assert devserver.resolve_safe_html_target(
            ".plan-review/foo.html", str(tmp_path)
        ) is None

    def test_rejects_empty_path(self, tmp_path: Path) -> None:
        assert devserver.resolve_safe_html_target("", str(tmp_path)) is None

    def test_rejects_parent_dir_escape(self, tmp_path: Path) -> None:
        result = devserver.resolve_safe_html_target(
            "../outside-design-review.html", str(tmp_path)
        )
        assert result is None

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        """The HTML must actually exist for the WS bridge to operate on it."""
        (tmp_path / ".plan-review").mkdir()
        assert devserver.resolve_safe_html_target(
            ".plan-review/never-rendered-review.html", str(tmp_path)
        ) is None


# ---------------------------------------------------------------------------
# transcript_exists — real on-disk check (no mock)
# ---------------------------------------------------------------------------


class TestTranscriptExists:
    """The resumability oracle. Redirect $HOME and write real transcript files
    so the check runs against the actual filesystem -- the behavior the old
    mocked suite never verified."""

    def test_true_when_jsonl_present(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        cwd = str(tmp_path / "proj")
        sid = str(uuid.uuid4())
        _make_transcript(home, cwd, sid)
        assert devserver.transcript_exists(sid, cwd) is True

    def test_false_when_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        cwd = str(tmp_path / "proj")
        assert devserver.transcript_exists(str(uuid.uuid4()), cwd) is False

    def test_false_for_empty_sid(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        assert devserver.transcript_exists("", str(tmp_path / "proj")) is False

    def test_false_when_transcript_is_under_a_different_cwd_slug(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A transcript recorded under project A is invisible when resuming
        from project B -- the slug is part of the lookup."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        sid = str(uuid.uuid4())
        _make_transcript(home, str(tmp_path / "projA"), sid)
        assert devserver.transcript_exists(sid, str(tmp_path / "projB")) is False


# ---------------------------------------------------------------------------
# _cold_start_spawn — always re-fork from the authoring session
# ---------------------------------------------------------------------------


class TestColdStartSpawn:
    """A cold start always forks from the authoring session (never attaches to
    a stored fork id -- forks aren't resumable). It bails only if the authoring
    session itself has no transcript on disk."""

    @staticmethod
    def _socketpair() -> tuple[socket.socket, socket.socket]:
        return socket.socketpair()

    def test_forks_from_authoring_when_resumable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        cwd = str(tmp_path / "proj")
        _make_transcript(home, cwd, "authoring")  # authoring IS resumable

        client, server = self._socketpair()
        try:
            spawn = devserver._cold_start_spawn(server, cwd, "authoring")
        finally:
            client.close()
            server.close()

        assert spawn is not None
        args, active_sid, mode = spawn
        assert mode == "fork"
        # Forks from the authoring session with a fresh, distinct session id.
        assert args == [
            "claude", "--resume", "authoring",
            "--fork-session", "--session-id", active_sid,
        ]
        assert active_sid != "authoring"
        # active_sid is a real uuid (so the live child has a stable handle)
        uuid.UUID(active_sid)

    def test_errors_when_authoring_not_resumable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        cwd = str(tmp_path / "proj")
        # No transcript for "authoring" anywhere on disk.

        client, server = self._socketpair()
        client.settimeout(2.0)
        try:
            spawn = devserver._cold_start_spawn(server, cwd, "authoring")
            # An error frame is written to the socket before returning None.
            sent = client.recv(4096)
        finally:
            client.close()
            server.close()

        assert spawn is None
        assert sent, "expected an error frame on the socket"
        assert b"no resumable transcript" in sent

    def test_always_forks_even_with_a_stale_id_in_html(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Regression guard for the original bug: a baked-in fork id that has
        no transcript must never be resumed. _cold_start_spawn no longer reads
        the HTML at all -- it always re-forks from the (resumable) authoring
        session. A phantom id can no longer produce a dead terminal."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        cwd = str(tmp_path / "proj")
        _make_transcript(home, cwd, "authoring")
        # A stale fork id that is NOT on disk (the 53a7aa7e-class phantom).
        phantom = str(uuid.uuid4())

        client, server = self._socketpair()
        try:
            spawn = devserver._cold_start_spawn(server, cwd, "authoring")
        finally:
            client.close()
            server.close()

        assert spawn is not None
        args, active_sid, mode = spawn
        assert mode == "fork"
        assert "authoring" in args and "--fork-session" in args
        assert active_sid != phantom  # never resumes the phantom
