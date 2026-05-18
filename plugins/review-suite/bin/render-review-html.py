#!/usr/bin/env python3
"""Render a review-suite HTML playground from a JSON spec.

Why this script exists
----------------------
The plan-review / design-review / architecture-map skills used to instruct
calling agents to inline-patch the template HTML by hand. Two common traps
broke that workflow silently:

1. Using ``re.sub(pat, replacement_string, text)`` to inject data: Python's
   ``re.sub`` interprets backslash escapes (``\\n``, ``\\1``, ``\\g<1>``) in
   the *replacement string*. If the agent's JS ``code:`` field contained
   literal ``\\n`` escape sequences, they would be turned into real newline
   characters in the output -- and real newlines inside single-quoted JS
   strings are a syntax error. The page renders blank.

2. Crafting multi-line strings as single-quoted JS literals. JS single-quoted
   strings can't span lines. Agents who didn't realize template literals
   (backticks) were required, or who weren't careful to write ``\\n`` escapes,
   would emit broken JS.

This script eliminates both traps by construction:
- Data is serialized via ``json.dumps``, which guarantees correct JS string
  escaping for any input.
- Placeholders are replaced via ``str.replace``, which doesn't interpret
  backslash sequences in the replacement.
- After rendering, the inline ``const X = ...;`` declarations are extracted
  and validated with ``node --check``. If parsing fails, the broken output
  is deleted and the script exits non-zero with the syntax error.

Usage
-----
::

    python3 render-review-html.py --spec spec.json
    python3 render-review-html.py < spec.json     # spec piped on stdin

Spec schema
-----------
::

    {
      "kind":         "design-review" | "plan-review" | "architecture-map",
      "title":        "Brief title",
      "session_id":   "uuid of authoring claude session",
      "output_path":  "where to write the HTML",

      "ticket":         "QUE-123"     (optional, for <title> prefix),
      "plugin_root":    "/abs/path"   (optional, defaults to script's ../),
      "skip_validation": false        (optional, default false),

      # plan-review only
      "doc_sections":     [ {id, title, content, revised?}, ... ],
      "prior_approvals":  { "section-id": "approved", ... }       (optional),

      # design-review and architecture-map
      "before_nodes": [ ... ],
      "before_edges": [ ... ],
      "after_nodes":  [ ... ],
      "after_edges":  [ ... ],
      "layouts_file": "QUE-123-foo-layouts.json",

      # architecture-map only
      "scope_header": "free-form description shown in header"
    }
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Template descriptors
# ----------------------------------------------------------------------------

# Each kind maps to its template asset and the set of placeholders we will
# substitute. The script-side contract is intentionally tiny: read template,
# do a fixed set of string replacements, write file, validate. No regex.

# Templates live under ``<plugin_root>/assets/``. Kept relative so the script
# is portable across install locations.
TEMPLATES = {
    "plan-review":      "review-template.html",
    "design-review":    "design-review-template.html",
    "architecture-map": "map-template.html",
}

# Placeholders shared by every template.
COMMON_PLACEHOLDERS = {
    "plan_name":     'const PLAN_NAME = "TITLE HERE";',
    "session":       'const CLAUDE_SESSION = "SESSION_ID_HERE";',
}

# Inline-script extraction marker. The validator pulls every ``const NAME = ...;``
# top-level declaration matching one of the known data variables for the kind.
KIND_DATA_VARS = {
    "plan-review":      ["docSections", "priorApprovals"],
    "design-review":    ["BEFORE_NODES", "BEFORE_EDGES", "AFTER_NODES", "AFTER_EDGES"],
    "architecture-map": ["BEFORE_NODES", "BEFORE_EDGES", "AFTER_NODES", "AFTER_EDGES"],
}


# ----------------------------------------------------------------------------
# Spec validation
# ----------------------------------------------------------------------------


class SpecError(ValueError):
    """Raised when the input spec is missing required fields or malformed."""


def validate_spec(spec: dict[str, Any]) -> None:
    """Raise SpecError if the spec is missing fields required for its kind.

    Kept deliberately strict: a missing field is always an authoring bug,
    never something we want to silently default. Surfacing it early beats
    rendering a broken page.
    """
    kind = spec.get("kind")
    if kind not in TEMPLATES:
        raise SpecError(
            f"spec.kind must be one of {sorted(TEMPLATES)}; got {kind!r}"
        )

    required_common = ["kind", "title", "session_id", "output_path"]
    for field in required_common:
        if not spec.get(field):
            raise SpecError(f"spec.{field} is required")

    if kind == "plan-review":
        if not isinstance(spec.get("doc_sections"), list):
            raise SpecError("spec.doc_sections must be a list")
        for i, s in enumerate(spec["doc_sections"]):
            for f in ("id", "title", "content"):
                if not s.get(f):
                    raise SpecError(f"spec.doc_sections[{i}].{f} is required")
    else:  # design-review or architecture-map
        for f in ("before_nodes", "before_edges", "after_nodes", "after_edges"):
            if not isinstance(spec.get(f), list):
                raise SpecError(f"spec.{f} must be a list")
        if not spec.get("layouts_file"):
            raise SpecError("spec.layouts_file is required for this kind")
        if kind == "architecture-map" and not spec.get("scope_header"):
            raise SpecError("spec.scope_header is required for architecture-map")


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------


def js_string(value: str) -> str:
    """Return a JS double-quoted string literal for ``value``.

    Delegates to ``json.dumps`` because JSON strings are valid JS strings --
    same quoting, same escapes. Handles embedded quotes, newlines, backslashes,
    and unicode uniformly.
    """
    return json.dumps(value)


def js_array_or_object(value: Any) -> str:
    """Return ``value`` serialized as a JS array/object literal.

    Same rationale as ``js_string``: JSON arrays and objects are valid JS
    literal syntax. Indented for readability when a human opens the rendered
    HTML to debug.
    """
    return json.dumps(value, indent=2)


def replace_declaration(text: str, var_name: str, new_value_literal: str) -> str:
    """Replace ``const VAR = ...;`` (anywhere in ``text``) with the new value.

    Matches the *first* declaration of ``var_name`` -- the templates only
    declare each data variable once. Uses a non-regex slice-and-splice
    approach to avoid Python's ``re.sub`` backslash interpretation in
    replacement strings (the bug this script was written to prevent).
    """
    anchor = f"const {var_name} = "
    start = text.find(anchor)
    if start == -1:
        raise RuntimeError(
            f"template does not contain 'const {var_name} =' -- "
            "template asset and skill are out of sync"
        )
    # Walk forward from the anchor to find the matching ``;`` at depth 0.
    # We can't just grep for the next ``;`` because object/array bodies often
    # contain semicolons inside string literals (unlikely for our templates,
    # but defensive).
    depth = 0
    in_string = None  # None or the opening quote char
    escape = False
    i = start + len(anchor)
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif in_string:
            if ch == in_string:
                in_string = None
        elif ch in ("'", '"', "`"):
            in_string = ch
        elif ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        elif ch == ";" and depth == 0:
            end = i + 1
            return text[:start] + f"const {var_name} = {new_value_literal};" + text[end:]
        i += 1
    raise RuntimeError(
        f"template declaration 'const {var_name} = ...;' is unterminated "
        "(no matching semicolon at depth 0)"
    )


def render(spec: dict[str, Any]) -> str:
    """Build the HTML for ``spec`` and return it as a string."""
    kind = spec["kind"]
    plugin_root = Path(spec.get("plugin_root") or _default_plugin_root())
    template_path = plugin_root / "assets" / TEMPLATES[kind]
    if not template_path.exists():
        raise RuntimeError(f"template not found: {template_path}")

    text = template_path.read_text()

    # 1. Title/heading -------------------------------------------------------
    title = spec["title"]
    ticket = spec.get("ticket") or ""
    full_title = f"{ticket}: {title}" if ticket else title

    # Each template has a different <title> placeholder pattern, so use the
    # exact strings rather than a regex. These are stable per-template.
    title_replacements = {
        "plan-review": [
            ("<title>Plan Review: TITLE HERE</title>",
             f"<title>Plan Review: {full_title}</title>"),
            ("<h1>Plan Review: TITLE HERE</h1>",
             f"<h1>{full_title}</h1>"),
        ],
        "design-review": [
            ("<title>TICKET — TITLE: Before / After Architecture</title>",
             f"<title>Design Review: {full_title}</title>"),
            ("<h1>TICKET TITLE HERE</h1>",
             f"<h1>{full_title}</h1>"),
        ],
        "architecture-map": [
            ("<title>Architecture Map: TICKET TITLE HERE</title>",
             f"<title>Architecture Map: {full_title}</title>"),
            ("<h1>TICKET TITLE HERE</h1>",
             f"<h1>{full_title}</h1>"),
        ],
    }
    for old, new in title_replacements[kind]:
        if old not in text:
            raise RuntimeError(
                f"template placeholder not found: {old!r} -- "
                "template asset has drifted from generator"
            )
        text = text.replace(old, new, 1)

    # 2. Shared constants ---------------------------------------------------
    text = replace_declaration(text, "PLAN_NAME", js_string(full_title))
    text = replace_declaration(text, "CLAUDE_SESSION", js_string(spec["session_id"]))

    # 3. Kind-specific data --------------------------------------------------
    if kind == "plan-review":
        text = replace_declaration(text, "docSections",
                                   js_array_or_object(spec["doc_sections"]))
        prior = spec.get("prior_approvals") or {}
        text = replace_declaration(text, "priorApprovals", js_array_or_object(prior))
    else:
        text = replace_declaration(text, "BEFORE_NODES",
                                   js_array_or_object(spec["before_nodes"]))
        text = replace_declaration(text, "BEFORE_EDGES",
                                   js_array_or_object(spec["before_edges"]))
        text = replace_declaration(text, "AFTER_NODES",
                                   js_array_or_object(spec["after_nodes"]))
        text = replace_declaration(text, "AFTER_EDGES",
                                   js_array_or_object(spec["after_edges"]))
        text = replace_declaration(text, "LAYOUTS_FILE",
                                   js_string(spec["layouts_file"]))
        if kind == "architecture-map":
            text = replace_declaration(text, "SCOPE_HEADER",
                                       js_string(spec["scope_header"]))

    return text


# ----------------------------------------------------------------------------
# Validation (node --check)
# ----------------------------------------------------------------------------


def validate_inline_js(html: str, kind: str) -> None:
    """Extract our injected declarations and feed them to ``node --check``.

    Catches the failure mode this script was designed to prevent: an invalid
    JS literal making it into the rendered page. Raises ``RuntimeError`` if
    node parsing fails. Silently no-ops if ``node`` isn't on PATH -- we'd
    rather render than refuse to render in that case, since the JSON path
    is already safer by construction.
    """
    if shutil.which("node") is None:
        return  # validator unavailable; trust json.dumps

    extracted_blocks: list[str] = []
    for var in KIND_DATA_VARS[kind] + [
        "PLAN_NAME",
        "CLAUDE_SESSION",
        "ACTIVE_SESSION",
        "LAYOUTS_FILE",
        "SCOPE_HEADER",
    ]:
        # Pull the first ``const VAR = ...;`` for each variable. This uses
        # the same depth-tracking walk as replace_declaration so it stops at
        # the actual end of the literal, not the first stray semicolon.
        anchor = f"const {var} = "
        start = html.find(anchor)
        if start == -1:
            continue
        end = _find_decl_end(html, start + len(anchor))
        if end is not None:
            extracted_blocks.append(html[start:end + 1])

    if not extracted_blocks:
        return

    scratch = _scratch_dir()
    js_path = scratch / "validate.js"
    js_path.write_text("\n".join(extracted_blocks) + "\n")
    result = subprocess.run(
        ["node", "--check", str(js_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Don't reveal the temp path -- the script's caller cares about the
        # logical failure, not where we staged the validator's input.
        raise RuntimeError(
            "rendered HTML contains invalid inline JavaScript:\n"
            + (result.stderr or result.stdout).strip()
        )


def _find_decl_end(text: str, start: int) -> int | None:
    """Return index of the ``;`` terminating the declaration that started at ``start``."""
    depth = 0
    in_string = None
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif in_string:
            if ch == in_string:
                in_string = None
        elif ch in ("'", '"', "`"):
            in_string = ch
        elif ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        elif ch == ";" and depth == 0:
            return i
        i += 1
    return None


# ----------------------------------------------------------------------------
# I/O and CLI
# ----------------------------------------------------------------------------


def _default_plugin_root() -> Path:
    """Return ``<repo>/plugins/review-suite/`` based on this script's location.

    The script lives at ``<plugin_root>/bin/render-review-html.py``.
    """
    return Path(__file__).resolve().parent.parent


def _scratch_dir() -> Path:
    """Return a scratch directory for temp files, preferring ``$CLAUDE_JOB_DIR``.

    Falls back to a per-process tempdir under ``/tmp`` when the env var is
    unset (typical for interactive sessions). The fallback is collision-safe
    and OS-cleaned on reboot; the env var, when present, lets the harness
    manage lifecycle.

    The result is cached for the life of the process so repeated calls
    share one dir (and the OS only needs to mkdir once).
    """
    cached = getattr(_scratch_dir, "_cached", None)
    if cached is not None and cached.exists():
        return cached
    env = os.environ.get("CLAUDE_JOB_DIR")
    if env and Path(env).is_dir():
        path = Path(env)
    else:
        path = Path(tempfile.mkdtemp(prefix="claude-job-"))
    _scratch_dir._cached = path  # type: ignore[attr-defined]
    return path


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via a tempfile + rename.

    Ensures readers never see a half-written file. Critical here because the
    devserver may already be serving ``path`` -- a partial write would break
    a tab refresh mid-render.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a review-suite HTML playground from a JSON spec."
    )
    parser.add_argument(
        "--spec",
        type=Path,
        help="Path to JSON spec file. If omitted, reads spec from stdin.",
    )
    args = parser.parse_args(argv)

    if args.spec:
        spec_text = args.spec.read_text()
    else:
        if sys.stdin.isatty():
            parser.error("--spec not given and stdin is a TTY (nothing to read)")
        spec_text = sys.stdin.read()

    try:
        spec = json.loads(spec_text)
    except json.JSONDecodeError as e:
        print(f"render-review-html: spec is not valid JSON: {e}", file=sys.stderr)
        return 2

    try:
        validate_spec(spec)
    except SpecError as e:
        print(f"render-review-html: invalid spec: {e}", file=sys.stderr)
        return 2

    try:
        html = render(spec)
    except (RuntimeError, KeyError) as e:
        print(f"render-review-html: render failed: {e}", file=sys.stderr)
        return 3

    output_path = Path(spec["output_path"]).expanduser()
    _atomic_write(output_path, html)

    if not spec.get("skip_validation"):
        try:
            validate_inline_js(html, spec["kind"])
        except RuntimeError as e:
            # Refuse to leave a broken file in place -- the caller would
            # otherwise return a URL pointing at JS that won't parse.
            output_path.unlink(missing_ok=True)
            print(f"render-review-html: {e}", file=sys.stderr)
            return 4

    # On success, echo the absolute output path for easy capture in shell.
    print(str(output_path.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
