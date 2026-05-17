"""Tests for the ``bin/render-review-html.py`` generator.

These tests exist primarily to prove that the two failure modes that
motivated QUE-225 (the generator's reason for existing) are caught:

1. Multi-line ``code:`` fields with literal ``\\n`` escapes render to valid
   inline JS -- i.e., the ``\\n`` reaches the file as two characters, not as
   a real newline that would break a single-quoted string.

2. A deliberately-corrupted spec that would emit invalid JS is rejected by
   the post-render ``node --check`` step rather than producing a broken file.

We also exercise the basic success path for each kind, ensure the spec
validator rejects obvious authoring mistakes, and confirm the atomic-write
path doesn't leave broken artifacts behind.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
RENDER_SCRIPT = PLUGIN_ROOT / "bin" / "render-review-html.py"


# Importing the script as a module lets us call its functions directly
# (rather than always shelling out). The .py name has a hyphen, so we use
# importlib rather than ``import``.
def _load_render_module():
    spec = importlib.util.spec_from_file_location("render_review_html", RENDER_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["render_review_html"] = mod
    spec.loader.exec_module(mod)
    return mod


render_module = _load_render_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def plan_review_spec(tmp_path):
    return {
        "kind": "plan-review",
        "ticket": "QUE-123",
        "title": "test plan",
        "session_id": "00000000-0000-0000-0000-000000000000",
        "output_path": str(tmp_path / "out.html"),
        "doc_sections": [
            {
                "id": "intro",
                "title": "Intro",
                "content": "Some markdown content.",
            },
            {
                "id": "next",
                "title": "Next",
                "content": "More content.",
            },
        ],
    }


@pytest.fixture
def design_review_spec(tmp_path):
    return {
        "kind": "design-review",
        "ticket": "QUE-223",
        "title": "test design",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "output_path": str(tmp_path / "out.html"),
        "layouts_file": "out-layouts.json",
        "before_nodes": [
            {
                "id": "a",
                "x": 100,
                "y": 100,
                "layer": "orchestration",
                "label": "A",
                "type": "old",
                "desc": "old node",
            },
        ],
        "before_edges": [],
        "after_nodes": [
            {
                "id": "a",
                "x": 100,
                "y": 100,
                "layer": "orchestration",
                "label": "A",
                "type": "new",
                "desc": "new node",
                "change": "modified",
            },
        ],
        "after_edges": [],
    }


@pytest.fixture
def architecture_map_spec(tmp_path):
    return {
        "kind": "architecture-map",
        "ticket": "QUE-99",
        "title": "test map",
        "session_id": "22222222-2222-2222-2222-222222222222",
        "output_path": str(tmp_path / "out.html"),
        "layouts_file": "out-layouts.json",
        "scope_header": "Test scope",
        "before_nodes": [],
        "before_edges": [],
        "after_nodes": [
            {
                "id": "x",
                "x": 0,
                "y": 0,
                "layer": "data",
                "label": "X",
                "type": "thing",
                "desc": "a thing",
            },
        ],
        "after_edges": [],
    }


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------


def test_validate_rejects_unknown_kind():
    with pytest.raises(render_module.SpecError, match="kind"):
        render_module.validate_spec({"kind": "bogus"})


def test_validate_rejects_missing_session(plan_review_spec):
    plan_review_spec.pop("session_id")
    with pytest.raises(render_module.SpecError, match="session_id"):
        render_module.validate_spec(plan_review_spec)


def test_validate_rejects_plan_section_without_id(plan_review_spec):
    plan_review_spec["doc_sections"][0].pop("id")
    with pytest.raises(render_module.SpecError, match=r"doc_sections\[0\].id"):
        render_module.validate_spec(plan_review_spec)


def test_validate_rejects_design_review_without_layouts_file(design_review_spec):
    design_review_spec.pop("layouts_file")
    with pytest.raises(render_module.SpecError, match="layouts_file"):
        render_module.validate_spec(design_review_spec)


def test_validate_rejects_architecture_map_without_scope_header(architecture_map_spec):
    architecture_map_spec.pop("scope_header")
    with pytest.raises(render_module.SpecError, match="scope_header"):
        render_module.validate_spec(architecture_map_spec)


# ---------------------------------------------------------------------------
# Render success paths -- each kind produces parseable HTML+JS
# ---------------------------------------------------------------------------


def _node_check(html: str, data_vars: list[str]) -> None:
    """Extract ``const VAR = ...;`` for each name and feed to ``node --check``.

    Fails the test if node isn't installed -- in a CI/local-dev environment
    that's a signal we're not actually validating, which would defeat the
    test's purpose.
    """
    assert shutil.which("node"), "node must be installed for validation tests"

    blocks = []
    for var in data_vars + ["PLAN_NAME", "CLAUDE_SESSION", "LAYOUTS_FILE", "SCOPE_HEADER"]:
        anchor = f"const {var} = "
        start = html.find(anchor)
        if start == -1:
            continue
        end = render_module._find_decl_end(html, start + len(anchor))
        assert end is not None, f"unterminated declaration for {var}"
        blocks.append(html[start:end + 1])

    assert blocks, "no inline declarations extracted"
    js = "\n".join(blocks)
    # node --check needs a file; -e is mutually exclusive with --check.
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        js_path = f.name
    try:
        result = subprocess.run(
            ["node", "--check", js_path],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"node parse failed:\n{result.stderr}\n--- script ---\n{js}"
        )
    finally:
        Path(js_path).unlink(missing_ok=True)


def test_render_plan_review(plan_review_spec):
    html = render_module.render(plan_review_spec)
    assert "<title>Plan Review: QUE-123: test plan</title>" in html
    assert "<h1>QUE-123: test plan</h1>" in html
    assert '"id": "intro"' in html
    _node_check(html, render_module.KIND_DATA_VARS["plan-review"])


def test_render_design_review(design_review_spec):
    html = render_module.render(design_review_spec)
    assert "<title>Design Review: QUE-223: test design</title>" in html
    assert "<h1>QUE-223: test design</h1>" in html
    assert '"BEFORE_NODES"' not in html  # the *key* should not leak
    assert "const BEFORE_NODES = [" in html
    _node_check(html, render_module.KIND_DATA_VARS["design-review"])


def test_render_architecture_map(architecture_map_spec):
    html = render_module.render(architecture_map_spec)
    assert "<title>Architecture Map: QUE-99: test map</title>" in html
    assert '"scope_header"' not in html  # not the spec key
    assert 'const SCOPE_HEADER = "Test scope";' in html
    _node_check(html, render_module.KIND_DATA_VARS["architecture-map"])


# ---------------------------------------------------------------------------
# Regression tests for the two failure modes this script was written for
# ---------------------------------------------------------------------------


def test_multiline_code_field_renders_valid_js(design_review_spec):
    """The QUE-225 motivating bug: multi-line ``code:`` field with literal
    ``\\n`` escapes used to be mangled to real newlines by ``re.sub``, breaking
    the single-quoted JS string. With ``json.dumps``, this must Just Work."""
    design_review_spec["after_nodes"][0]["code"] = (
        "def example(x):\n"
        "    return x + 1\n"
        "\n"
        "result = example(2)"
    )
    html = render_module.render(design_review_spec)
    # The newlines in the input must reach the file as ``\n`` escape sequences
    # inside a JSON string, not as real newlines that would split the literal.
    # json.dumps will produce: "code": "def example(x):\n    return x + 1\n\nresult = example(2)"
    assert '"def example(x):\\n    return x + 1\\n\\nresult = example(2)"' in html
    # And the inline JS must parse.
    _node_check(html, render_module.KIND_DATA_VARS["design-review"])


def test_tricky_characters_in_strings_render_valid_js(design_review_spec):
    """Quotes, backticks, backslashes, and unicode in user-supplied strings
    must all survive without breaking the JS literal."""
    design_review_spec["after_nodes"][0]["desc"] = (
        'mixed "double" and \'single\' quotes, '
        "backticks `like this`, "
        "a backslash \\, "
        "unicode — em dash"
    )
    html = render_module.render(design_review_spec)
    _node_check(html, render_module.KIND_DATA_VARS["design-review"])


# ---------------------------------------------------------------------------
# End-to-end via the CLI: spec on stdin, output written, validator catches bad JS
# ---------------------------------------------------------------------------


def _run_script(spec: dict, **kwargs) -> subprocess.CompletedProcess:
    """Run ``render-review-html.py`` with the spec on stdin."""
    return subprocess.run(
        [sys.executable, str(RENDER_SCRIPT)],
        input=json.dumps(spec),
        capture_output=True, text=True,
        **kwargs,
    )


def test_cli_writes_file_and_returns_path(design_review_spec, tmp_path):
    proc = _run_script(design_review_spec)
    assert proc.returncode == 0, proc.stderr
    output_path = Path(proc.stdout.strip())
    assert output_path.exists()
    assert output_path.read_text().startswith("<!DOCTYPE html>")


def test_cli_rejects_invalid_spec(plan_review_spec, tmp_path):
    del plan_review_spec["title"]
    proc = _run_script(plan_review_spec)
    assert proc.returncode == 2
    assert "title" in proc.stderr.lower()


def test_cli_self_validation_catches_broken_js(design_review_spec, tmp_path, monkeypatch):
    """Force the renderer to emit broken JS and confirm the validator catches
    it AND deletes the bad output rather than leaving a broken file."""
    # Patch render() so it returns HTML with a deliberately-broken inline JS
    # declaration (an unterminated string). We do this in-process so we can
    # bypass json.dumps and prove that *if* the renderer ever produced bad
    # JS, the validator step would catch it before the URL is returned.
    bad_html = (
        '<!DOCTYPE html>\n<html><body>\n<script>\n'
        # Use a const name the validator looks for so it gets extracted:
        "const BEFORE_NODES = [{ id: 'broken\nstring' }];\n"
        "const PLAN_NAME = \"x\";\n"
        "const CLAUDE_SESSION = \"y\";\n"
        '</script></body></html>\n'
    )
    monkeypatch.setattr(render_module, "render", lambda spec: bad_html)
    output_path = Path(design_review_spec["output_path"])
    rc = render_module.main([
        "--spec", str(_write_spec(design_review_spec, tmp_path)),
    ])
    assert rc == 4
    assert not output_path.exists(), (
        "validator must delete the broken file so the caller can't return its URL"
    )


def test_cli_skip_validation_bypasses_node(design_review_spec, tmp_path, monkeypatch):
    """``skip_validation`` is an escape hatch for the rare case where the
    user accepts a non-validated render. The file is written either way."""
    design_review_spec["skip_validation"] = True
    monkeypatch.setattr(
        render_module, "validate_inline_js",
        lambda *args, **kwargs: pytest.fail("validator should not be called"),
    )
    rc = render_module.main([
        "--spec", str(_write_spec(design_review_spec, tmp_path)),
    ])
    assert rc == 0
    assert Path(design_review_spec["output_path"]).exists()


def _write_spec(spec: dict, tmp_path: Path) -> Path:
    """Write a spec dict to ``tmp_path/spec.json`` and return the path."""
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    return p


# ---------------------------------------------------------------------------
# $CLAUDE_JOB_DIR fallback behavior
# ---------------------------------------------------------------------------


def test_scratch_dir_uses_env_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_JOB_DIR", str(tmp_path))
    # Bust the per-process cache so we re-evaluate the env var.
    if hasattr(render_module._scratch_dir, "_cached"):
        delattr(render_module._scratch_dir, "_cached")
    assert render_module._scratch_dir() == tmp_path


def test_scratch_dir_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_JOB_DIR", raising=False)
    if hasattr(render_module._scratch_dir, "_cached"):
        delattr(render_module._scratch_dir, "_cached")
    path = render_module._scratch_dir()
    # Fallback must be a real, existing, project-distinct directory.
    assert path.exists()
    assert path.name.startswith("claude-job-")
