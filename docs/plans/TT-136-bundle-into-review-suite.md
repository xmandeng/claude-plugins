# TT-136: Bundle into review-suite — Implementation Plan

> **Jira:** [TT-136](https://mandeng.atlassian.net/browse/TT-136)
> **Branch:** `feature/TT-136-bundle-into-review-suite`
> **Repo:** `xmandeng/claude-plugins`

## Problem

The `xmandeng-plugins` marketplace ships four related plugins as separate installable units:

| Plugin | Version | Notes |
|---|---|---|
| `plan-review` | 0.2.3 | Most mature; only one with `REVIEW_TEMPLATE.md` |
| `architecture-review` | 0.1.1 | Adds PUT-for-`*-layouts.json` to devserver |
| `architecture-map` | 0.1.0 | Same devserver feature set as architecture-review |
| `code-diagram` | 0.1.0 | Untracked, in-progress; no devserver/hooks/tests |

The three review plugins each carry their own `bin/devserver.py`, `bin/inject-session-id.sh`, `hooks/hooks.json`, and `tests/` — creating drift, install friction, and no path to a standalone `/devserver` skill.

### Drift findings

- **`bin/devserver.py`:** plan-review is 416 LOC; arch-review and arch-map are 498 LOC (added a PUT handler for `*-layouts.json`). Plan-review is missing that handler. Arch-review vs arch-map diff is purely cosmetic — banner string, default port (8775 vs 8785), env-var prefix (`ARCHITECTURE_REVIEW_` vs `ARCHITECTURE_MAP_`).
- **`bin/inject-session-id.sh`:** all three differ only in the emitted label (`plan-review-session-id`, `architecture-review-session-id`, `architecture-map-session-id`). Each SKILL.md greps for its own label.
- **`hooks/hooks.json`:** byte-identical across all three.
- **`tests/`:** `conftest.py` identical between arch-review and arch-map; plan-review's differs. `test_devserver.py` differs across all three.

## Solution

Consolidate all four plugins into a single `review-suite` plugin in the same `xmandeng-plugins` marketplace.

### Bundle layout

```
plugins/review-suite/
  .claude-plugin/plugin.json          # v0.1.0
  README.md
  bin/
    devserver.py                       # canonical (498 LOC, with PUT handler)
    inject-session-id.sh               # unified label: review-suite-session-id
  hooks/hooks.json                    # registers UserPromptSubmit → bin/inject-session-id.sh
  skills/
    plan-review/SKILL.md
    architecture-review/SKILL.md
    architecture-map/SKILL.md
    code-diagram/SKILL.md
    devserver/SKILL.md                 # NEW: thin wrapper
  assets/
    REVIEW_TEMPLATE.md                 # moved from plan-review
    review-template.html               # moved from plan-review
    architecture-template.html         # moved from architecture-review (if referenced)
    map-template.html                  # moved from architecture-map (if referenced)
    screenshots/
  tests/
    conftest.py
    test_devserver.py                  # plan-review's + arch-review's PUT-handler tests merged
```

### Devserver consolidation

**Source of truth:** `plugins/architecture-review/bin/devserver.py` (has the PUT handler).

| Field | Before | After |
|---|---|---|
| Module docstring | "Devserver for the architecture-review plugin" | "Devserver for the review-suite plugin" |
| Default port | `8775` | `8765` (historical, matches plan-review) |
| Env vars | `ARCHITECTURE_REVIEW_HOST/PORT` | `REVIEW_SUITE_HOST/PORT` |
| Banner | `print(f"architecture-review devserver: ...")` | `print(f"review-suite devserver: ...")` |

### Hook unification

One hook, one label (`review-suite-session-id`):

```bash
#!/usr/bin/env bash
set -euo pipefail
sid=$(jq -r '.session_id')
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"review-suite-session-id: %s"}}\n' "$sid"
```

### SKILL.md edits

- `plan-review/SKILL.md`: replace `plan-review-session-id` → `review-suite-session-id` (2 occurrences).
- `architecture-review/SKILL.md`: replace `architecture-review-session-id` → `review-suite-session-id` (1 occurrence).
- `architecture-map/SKILL.md`: replace `architecture-map-session-id` → `review-suite-session-id` (1 occurrence).
- `code-diagram/SKILL.md`: copied as-is (no hook dependency).
- `devserver/SKILL.md`: NEW. Thin wrapper that launches `${CLAUDE_PLUGIN_ROOT}/bin/devserver.py` from project root with `.devserver-port` reuse and 8765-8799 free-port scan.

### marketplace.json

- **Add:** `review-suite` entry pointing at `./plugins/review-suite`.
- **Mark deprecated:** prefix existing `plan-review`, `architecture-review`, `architecture-map` description fields with `[DEPRECATED — migrate to review-suite]`. (`code-diagram` is not currently in marketplace.json.)
- **Do NOT delete** the existing `plugins/{plan-review,architecture-review,architecture-map,code-diagram}/` directories. They stay for one release.

## Acceptance Criteria

1. `plugins/review-suite/` directory exists in `xmandeng/claude-plugins` with the layout above.
2. One canonical `bin/devserver.py` with PUT-for-`*-layouts.json` support; env vars `REVIEW_SUITE_HOST/PORT`; default port 8765.
3. One unified `bin/inject-session-id.sh` emitting `review-suite-session-id` label.
4. Three review SKILL.md files read the unified label.
5. New `/devserver` skill that starts the bundled devserver from project root with `.devserver-port` reuse.
6. `marketplace.json`: new `review-suite` entry; existing three review entries marked deprecated in description.
7. Old plugin directories preserved untouched.
8. `tests/test_devserver.py` (plan-review's + arch-review's PUT tests merged) passes against the consolidated `bin/devserver.py`.

## Functional Evidence (PR-level)

- Server starts on port 8765
- Serves an HTML file
- Accepts a PUT to a `*-layouts.json` path → file written to disk
- Hook fires emitting `review-suite-session-id` on UserPromptSubmit
- All three review skills (plan-review, architecture-review, architecture-map) successfully read the new label
- `/devserver` skill starts the server cleanly when no review skill has been invoked first

## Migration

After PR merges:

```
/plugin uninstall plan-review
/plugin uninstall architecture-review
/plugin uninstall architecture-map
/plugin install review-suite@xmandeng-plugins
```

Old generated HTMLs in `.plan-review/` keep working (session ID baked in at authoring time).

## Out of Scope

- Removing the four legacy plugin directories (deferred to next release).
- Migration tooling for end-users.
- Versioning bumps to the four legacy plugins (frozen in deprecation).
- Renaming the marketplace itself.

## Decisions Made During Planning

- **Bundle name:** `review-suite`
- **Code-diagram inclusion:** included in the bundle from day one.
- **Migration strategy:** deprecate gracefully (one-release window).
- **Default port:** 8765 (plan-review's; bookmarks at 8775/8785 will need `REVIEW_SUITE_PORT` override).
- **`/devserver` arg shape:** ports-only; serves project root.
- **Tests:** merge plan-review's and arch-review's `test_devserver.py` for full PUT-handler coverage.
