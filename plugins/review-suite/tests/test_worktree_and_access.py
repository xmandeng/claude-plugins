"""Worktree-aware session resolution, playground fork resume, and access control."""

import http.client
import os
import socket
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import devserver  # type: ignore[import-not-found]  # added to sys.path by conftest


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _make_transcript(home: Path, cwd: str, sid: str) -> Path:
    d = home / ".claude" / "projects" / devserver.project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    f.write_text('{"type":"summary"}\n')
    return f


def test_project_slug_flattens_every_non_alphanumeric() -> None:
    assert devserver.project_slug("/home/u/repo/app_v2.x") == "-home-u-repo-app-v2-x"


class TestWorktreeResolution:
    def test_ensure_resumable_links_transcript_from_main_checkout(
        self, home: Path, tmp_path: Path
    ) -> None:
        main, worktree = str(tmp_path / "repo"), str(tmp_path / "repo-QUE-1")
        sid = str(uuid.uuid4())
        src = _make_transcript(home, main, sid)
        assert not devserver.transcript_exists(sid, worktree)

        assert devserver.ensure_resumable(sid, worktree) is True

        linked = home / ".claude" / "projects" / devserver.project_slug(worktree) / f"{sid}.jsonl"
        assert linked.is_symlink() and linked.resolve() == src.resolve()
        # Idempotent on a second call.
        assert devserver.ensure_resumable(sid, worktree) is True

    def test_ensure_resumable_false_when_transcript_nowhere(
        self, home: Path, tmp_path: Path
    ) -> None:
        assert devserver.ensure_resumable(str(uuid.uuid4()), str(tmp_path / "wt")) is False

    def test_find_transcript_rejects_path_like_ids(self, home: Path) -> None:
        assert devserver.find_transcript("../x") is None
        assert devserver.find_transcript("") is None


class TestColdStartResume:
    @staticmethod
    def _spawn(cwd: str, sid: str, key: str | None):
        a, b = socket.socketpair()
        try:
            return devserver._cold_start_spawn(b, cwd, sid, key)
        finally:
            a.close()
            b.close()

    def test_forks_then_resumes_recorded_fork(self, home: Path, tmp_path: Path) -> None:
        cwd = str(tmp_path / "proj")
        _make_transcript(home, cwd, "authoring")
        key = ".plan-review/X-review.html"

        first = self._spawn(cwd, "authoring", key)
        assert first is not None and first[2] == "fork"
        fork_sid = first[1]

        # Fork never wrote a transcript (no turn yet) -> fork again, not a dead resume.
        again = self._spawn(cwd, "authoring", key)
        assert again is not None and again[2] == "fork"
        fork_sid = again[1]

        _make_transcript(home, cwd, fork_sid)
        resumed = self._spawn(cwd, "authoring", key)
        assert resumed == (["claude", "--resume", fork_sid], fork_sid, "resume")

    def test_regenerated_playground_with_new_authoring_forks_fresh(
        self, home: Path, tmp_path: Path
    ) -> None:
        cwd = str(tmp_path / "proj")
        _make_transcript(home, cwd, "old-authoring")
        _make_transcript(home, cwd, "new-authoring")
        key = ".plan-review/X-review.html"
        first = self._spawn(cwd, "old-authoring", key)
        assert first is not None
        _make_transcript(home, cwd, first[1])

        spawn = self._spawn(cwd, "new-authoring", key)
        assert spawn is not None and spawn[2] == "fork"
        assert spawn[0][:3] == ["claude", "--resume", "new-authoring"]

    def test_forks_from_authoring_recorded_in_other_project(
        self, home: Path, tmp_path: Path
    ) -> None:
        main, worktree = str(tmp_path / "repo"), str(tmp_path / "repo-wt")
        _make_transcript(home, main, "authoring")
        spawn = self._spawn(worktree, "authoring", ".plan-review/X-review.html")
        assert spawn is not None and spawn[2] == "fork"


def test_child_env_drops_parent_session_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in devserver._INHERITED_SESSION_VARS:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    env = devserver.child_env()
    assert not any(n in env for n in devserver._INHERITED_SESSION_VARS)
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"


def test_pid_script_is_not_current_version_for_non_devserver() -> None:
    assert devserver.is_current_version(os.getpid()) is False


class TestOpenAccess:
    @pytest.fixture
    def server(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".plan-review").mkdir()
        (tmp_path / ".plan-review" / "X-review.html").write_text("<html>ok</html>")
        monkeypatch.chdir(tmp_path)

        class Handler(devserver.DevHandler):
            spawn_cwd = str(tmp_path)

            def log_message(self, *a: object) -> None:
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield srv.server_address[1]
        srv.shutdown()

    @staticmethod
    def _req(port: int, method: str, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp

    def test_plain_urls_are_served(self, server: int) -> None:
        assert self._req(server, "GET", "/.plan-review/X-review.html").status == 200
        assert self._req(server, "HEAD", "/.plan-review/X-review.html").status == 200
        assert self._req(server, "GET", "/.plan-review/").status == 200
        assert self._req(server, "GET", "/").status == 200

    def test_wildcard_cors(self, server: int) -> None:
        resp = self._req(server, "GET", "/.plan-review/X-review.html")
        assert resp.getheader("Access-Control-Allow-Origin") == "*"

    def test_find_or_start_url_has_no_token(self, tmp_path: Path) -> None:
        assert devserver.devserver_url("10.0.0.5", 8765, tmp_path) == "http://10.0.0.5:8765/"


def test_state_dir_gitignores_devserver_files(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    devserver.write_port_file(tmp_path, 8765)
    devserver.ensure_state_dir(tmp_path)  # idempotent: no duplicate block
    state = tmp_path / ".plan-review"
    assert (state / ".gitignore").read_text().count(".devserver[-.]*") == 1
    for name in (".devserver-port", ".devserver.log", ".playground-sessions.json"):
        r = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "-q", f".plan-review/{name}"])
        assert r.returncode == 0, name
    r = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "-q", ".plan-review/X-review.html"])
    assert r.returncode == 1
