"""Tests for QUE-226 sticky-session helpers in devserver.py.

These cover the embed-in-HTML model: the playground's active fork SID lives
in the HTML's ``ACTIVE_SESSION`` constant, not a sibling file. Devserver
helpers:

- ``resolve_safe_html_target`` — validates that the playground path passed
  via the WS query string points at a real review-suite HTML inside the
  spawn cwd (path-traversal-safe, suffix-restricted, file-must-exist).
- ``read_active_session`` — extracts the UUID from the HTML's
  ``const ACTIVE_SESSION = "..."`` declaration. Returns None for missing
  file, missing constant, empty value, or invalid UUID — all map to
  "first open, fork fresh" semantics.
- ``write_active_session`` — atomically mutates the constant in-place
  via tmp + rename. Raises if the constant isn't there (older HTML).

The end-to-end PTY/WS flow is not exercised here — that needs a real
``claude`` binary and is integration territory. What's covered here is
the pure logic that decides attach vs. fork plus the in-HTML mutation
mechanics.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

import devserver  # type: ignore[import-not-found]  # added to sys.path by conftest


# A minimal HTML stub that contains the constants the helpers look for.
# Real templates are 80+ KB; keeping fixtures small makes failure output
# readable and isolates regressions to the logic under test.
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><title>test</title></head>
<body>
<script>
const PLAN_NAME = "test plan";
const CLAUDE_SESSION = "authoring-session-id-placeholder";
// QUE-226 marker
const ACTIVE_SESSION = "{active}";
const LAYOUTS_FILE = "test-layouts.json";
</script>
</body>
</html>
"""


def _write_playground(dir_path: Path, name: str, active: str = "") -> Path:
    """Write a stub playground HTML and return its path."""
    p = dir_path / name
    p.write_text(HTML_TEMPLATE.format(active=active))
    return p


# ---------------------------------------------------------------------------
# resolve_safe_html_target
# ---------------------------------------------------------------------------


class TestResolveSafeHtmlTarget:
    """Same security envelope as resolve_safe_layouts_target, applied to the
    playground HTML lookup introduced for QUE-226."""

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
        """The HTML must actually exist for the WS bridge to operate on it.
        A request pointing at a not-yet-rendered playground is rejected so
        we don't end up writing ACTIVE_SESSION into a non-existent file."""
        (tmp_path / ".plan-review").mkdir()
        assert devserver.resolve_safe_html_target(
            ".plan-review/never-rendered-review.html", str(tmp_path)
        ) is None


# ---------------------------------------------------------------------------
# read_active_session
# ---------------------------------------------------------------------------


class TestReadActiveSession:
    """Read-side discipline: only valid UUIDs become a stored SID. Everything
    else maps to None so the caller takes the fork-fresh path."""

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert devserver.read_active_session(tmp_path / "nope.html") is None

    def test_returns_none_for_empty_active_session(self, tmp_path: Path) -> None:
        """The post-generation state: ACTIVE_SESSION = "". This is "first
        open, no fork yet" — must NOT be treated as a stored UUID."""
        p = _write_playground(tmp_path, "test-review.html", active="")
        assert devserver.read_active_session(p) is None

    def test_returns_valid_uuid(self, tmp_path: Path) -> None:
        sid = str(uuid.uuid4())
        p = _write_playground(tmp_path, "test-review.html", active=sid)
        assert devserver.read_active_session(p) == sid

    def test_returns_none_for_garbage_value(self, tmp_path: Path) -> None:
        """User-edited bogus content (or 36-char-but-not-UUID) must not be
        treated as a stored SID — avoids feeding `claude --resume <garbage>`
        and getting an opaque error."""
        p = _write_playground(tmp_path, "test-review.html", active="not-a-uuid-at-all")
        assert devserver.read_active_session(p) is None

    def test_returns_none_for_uuid_like_but_invalid_hex(self, tmp_path: Path) -> None:
        p = _write_playground(
            tmp_path, "test-review.html", active="zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"
        )
        assert devserver.read_active_session(p) is None

    def test_returns_none_when_constant_missing(self, tmp_path: Path) -> None:
        """Older rendered HTML from a pre-QUE-226 plugin doesn't have
        ACTIVE_SESSION at all. Must map to None (fork-fresh) — not raise."""
        p = tmp_path / "old-design-review.html"
        p.write_text(
            "<html><script>\n"
            'const CLAUDE_SESSION = "abc";\n'
            "// no ACTIVE_SESSION\n"
            "</script></html>\n"
        )
        assert devserver.read_active_session(p) is None

    def test_tolerates_indentation(self, tmp_path: Path) -> None:
        """plan-review-template.html declares its constants inside an
        indented <script> block; design-review and architecture-map use no
        indent. The regex anchor must accept both."""
        sid = str(uuid.uuid4())
        p = tmp_path / "indented-review.html"
        p.write_text(
            "<html><script>\n"
            '    const CLAUDE_SESSION = "auth";\n'
            f'    const ACTIVE_SESSION = "{sid}";\n'
            "</script></html>\n"
        )
        assert devserver.read_active_session(p) == sid


# ---------------------------------------------------------------------------
# write_active_session
# ---------------------------------------------------------------------------


class TestWriteActiveSession:
    """Atomic in-place mutation of the HTML's ACTIVE_SESSION constant."""

    def test_writes_sid_into_constant(self, tmp_path: Path) -> None:
        sid = str(uuid.uuid4())
        p = _write_playground(tmp_path, "test-review.html", active="")
        devserver.write_active_session(p, sid)
        assert devserver.read_active_session(p) == sid

    def test_overwrites_existing_value(self, tmp_path: Path) -> None:
        """If the user reopens after a previous fork, the new SID replaces
        the old. No accumulation — one active session at a time."""
        old = str(uuid.uuid4())
        new = str(uuid.uuid4())
        p = _write_playground(tmp_path, "test-review.html", active=old)
        devserver.write_active_session(p, new)
        assert devserver.read_active_session(p) == new

    def test_preserves_rest_of_html(self, tmp_path: Path) -> None:
        """The mutation must be surgical — only the ACTIVE_SESSION value
        changes. Other constants (CLAUDE_SESSION, PLAN_NAME, etc.) and the
        HTML body remain untouched."""
        sid = str(uuid.uuid4())
        p = _write_playground(tmp_path, "test-review.html", active="")
        before = p.read_text()
        devserver.write_active_session(p, sid)
        after = p.read_text()
        # Everything outside the ACTIVE_SESSION value should be byte-identical.
        # We replace just the value in `before` and compare.
        expected = re.sub(
            r'(const ACTIVE_SESSION = ")[^"]*(")',
            lambda m: m.group(1) + sid + m.group(2),
            before,
        )
        assert after == expected

    def test_raises_when_constant_missing(self, tmp_path: Path) -> None:
        """Older HTML without ACTIVE_SESSION is a hard error — we can't
        invent a place to write the SID. The caller surfaces this to the
        UI via the `session_persist_failed` message; future opens will
        keep fork-fresh-ing, which is acceptable for pre-QUE-226 HTMLs."""
        p = tmp_path / "old-review.html"
        p.write_text("<html><script>const CLAUDE_SESSION = \"x\";</script></html>")
        with pytest.raises(RuntimeError, match="ACTIVE_SESSION"):
            devserver.write_active_session(p, str(uuid.uuid4()))

    def test_no_lingering_tmp_file_on_success(self, tmp_path: Path) -> None:
        sid = str(uuid.uuid4())
        p = _write_playground(tmp_path, "test-review.html", active="")
        devserver.write_active_session(p, sid)
        leftovers = [child for child in tmp_path.iterdir() if child.suffix == ".tmp"]
        assert leftovers == []

    def test_round_trip(self, tmp_path: Path) -> None:
        """Write then read must yield the same SID — catches any encoding
        or whitespace drift between the two functions."""
        sid = str(uuid.uuid4())
        p = _write_playground(tmp_path, "test-review.html", active="")
        devserver.write_active_session(p, sid)
        assert devserver.read_active_session(p) == sid


# ---------------------------------------------------------------------------
# End-to-end: the lifecycle the WS bridge actually walks through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "html_dir,html_name",
    [
        (".design-review",    "QUE-1-x-design-review.html"),
        (".plan-review",      "QUE-2-y-review.html"),
        (".architecture-map", "QUE-3-z-architecture-map.html"),
    ],
)
def test_resolve_write_read_lifecycle(
    tmp_path: Path, html_dir: str, html_name: str
) -> None:
    """Walk the whole lifecycle the WS bridge actually performs:

    1. Browser sends WS with `?playground=<rel>`; resolver maps it to an
       absolute HTML path under spawn_cwd.
    2. First open: read_active_session returns None (empty constant) →
       fork would happen → write_active_session persists new SID.
    3. Subsequent open: read_active_session returns the stored SID →
       attach mode fires.
    """
    (tmp_path / html_dir).mkdir()
    _write_playground(tmp_path / html_dir, html_name, active="")
    rel = f"{html_dir}/{html_name}"

    # Step 1: resolve
    target = devserver.resolve_safe_html_target(rel, str(tmp_path))
    assert target is not None
    assert target.name == html_name

    # Step 2: first-open state — no stored SID
    assert devserver.read_active_session(target) is None

    # Step 2 (continued): persist a new fork SID
    sid = str(uuid.uuid4())
    devserver.write_active_session(target, sid)

    # Step 3: subsequent-open state — stored SID emerges
    assert devserver.read_active_session(target) == sid
