---
name: code-diagram
description: Generate a Graphviz diagram of software components — call graphs, object/class models, module dependency graphs, component diagrams — or, secondarily, a described process (state machine, workflow, data flow). Authors a .dot source plus rendered SVG / PNG / PDF via the local `dot` utility, with layout and styling chosen for the target consumption (browser vs print). Usage - /code-diagram [<scope>]
allowed-tools: Read Write Edit Glob Grep Bash(mkdir:*) Bash(command:*) Bash(dot:*) Bash(ls:*) Bash(test:*)
argument-hint: "[<scope>]"
---

# Code Diagram Skill

Turns a slice of code (primary use case) or a described process into a Graphviz diagram. The skill is **flexible by design** — it does not pre-commit to one graph type. The agent picks the shape that best fits the material, then authors DOT and renders it through the local `dot` utility.

**Primary use case: software components.** Most invocations will produce call graphs, object/class models, module dependency graphs, or component diagrams of a subsystem under discussion. Process-style workflows (CI pipelines, business flows, deploy steps) are supported but secondary.

The output is **arranged for its consumption target**. A diagram destined for a browser is laid out differently than one destined for an A4/letter PDF — engine, rankdir, font sizes, edge routing, and clustering all change accordingly.

## Invocation Forms

| Invocation | Behavior |
|---|---|
| `/code-diagram` | Infer the scope from recent conversation context. Ask only when context is thin. |
| `/code-diagram <scope>` | User supplies a freeform scope hint — file path, function name, subsystem, or a one-line process description. |

## How It Works

1. **Pre-flight: `dot` must exist.** Check `command -v dot`. If missing, surface the install hint and stop:
   - macOS: `brew install graphviz`
   - Debian/Ubuntu: `sudo apt install graphviz`
   - Other: see https://graphviz.org/download/

2. **Gather material.** Two input modes — the scope hint disambiguates which:
   - **Code** (primary) — file paths, function names, module names, class names. Read the named files; use Grep to locate definitions and call sites; trace the relevant relationships within scope. Examples:
     - *Call graph from one entry function*: read the function, find its callees with Grep, recurse to a sane depth (typically 2–3) or until you hit a stable boundary (external lib, I/O, framework).
     - *Object/class model*: read the named class file, list its fields, base classes, and types referenced in field annotations / constructor params. Cross-link to other classes in the same scope.
     - *Module dependency graph*: parse imports across the named directory; treat external packages as boundary nodes (single ellipse on the edge of the graph).

     Stay within the named scope; do not balloon into the rest of the repo. When the boundary is fuzzy, ask before expanding.
   - **Process** (secondary) — a freeform description ("login flow", "order pipeline", "deploy steps"). Extract the entities and transitions from the description plus prior conversation. Do not invent steps the user did not describe.

3. **Pick the graph type.** The shape of the material dictates this — do not force everything into a call graph:
   - **Call graph** — code, "who calls whom" within a module/file/function. `dot` engine, `rankdir=LR` typically, function names in `Menlo`.
   - **Object / class model** — nodes are types; edges are has-a (open diamond `arrowtail=odiamond, dir=back` for composition with `arrowtail=diamond`), is-a (empty triangle `arrowhead=empty`), or plain refs. UML-ish; one box per class with a horizontal rule for fields if useful (`shape=record` or HTML labels).
   - **Module / dependency graph** — code, "what imports what". `dot` engine, `rankdir=TB`. Cluster by package; collapse external libs into a boundary node.
   - **State machine** — code or process where nodes are *states* and edges are *transitions* with conditions. Self-loops are common; label edges with the trigger.
   - **Component diagram** — boxes are services/modules/processes, edges are runtime calls or data flows. Use `subgraph cluster_*` heavily to show process boundaries.
   - **Sequence-as-DAG** — a workflow where order matters but parallel branches exist (CI pipeline, deploy steps). `dot` engine, `rankdir=TB`, label edges with conditions only when non-trivial.
   - **Data flow** — nodes are data stores / transforms / sinks; edges are data movement. Strongly directional, often layered.
   - **Workflow / process** — nodes are activities, edges are handoffs. Decision points are diamond-shaped; terminals are oval.

4. **Pick the layout engine.** Graphviz ships several:
   - `dot` — hierarchical, good default for DAGs (call graphs, dependencies, workflows, data flows).
   - `neato` / `fdp` — force-directed, good for cyclic / undirected (object reference graphs, peer relationships).
   - `sfdp` — scalable force-directed, large graphs.
   - `twopi` — radial; one central node with concentric rings.
   - `circo` — circular; cyclic structures, ring buses.

5. **Choose the consumption target.** This is **not optional** — it changes the file's layout meaningfully. Defaults to **browser/SVG** unless the user signals print.
   - **Browser (SVG)** — wide canvas OK (scrollable). `rankdir=LR` for sequential pipelines, `TB` for hierarchies. Font size 11–13. `splines=true` (curved). `concentrate=true` to merge parallel edges. Crisp colors are fine; reviewers can zoom.
   - **PDF (print)** — must fit a page. Set `size="10.5,7.5!"` (US letter landscape, with margins) or `size="7.5,10.5!"` (portrait). `ratio=compress` to make it fit, or split into multiple subgraphs if the graph is dense. Font size 9–10. `splines=ortho` or `polyline` (cleaner under print). Use line styles (`dashed`, `dotted`) in addition to color so it survives B&W printing.
   - **PNG** — treat as browser-style sizing but bump `dpi=150` (or 200 for retina). Useful when embedding in docs that don't render SVG.

6. **Author DOT.** Style guidelines:
   - Group related nodes with `subgraph cluster_<name> { label="..."; ... }`. Clusters are the single biggest readability win for non-trivial graphs.
   - Use **shape semantically**: `box` (default), `box rounded` (services/handlers), `ellipse` (terminals/start/end), `diamond` (decisions), `cylinder` (storage), `note` (annotations), `point` (junctions).
   - Use **color by kind, not by decoration** — entry points, external boundaries, error paths, async/out-of-band flows. A 3–5 color palette is plenty.
   - Use `style=dashed` on edges for callbacks, async dispatches, error paths — so meaning isn't lost in B&W.
   - **Edge labels carry information or stay off.** Don't label every edge with "calls"; do label state-machine transitions with their trigger.
   - Set `node [fontname="Helvetica"]` (or `"Inter"` if available) for titles, and use `fontname="Menlo"` for code-y nodes (function names, file paths). Mixed fonts make the kind of node obvious at a glance.
   - Add a small `labelloc="t"; label="..."` graph title with the scope.
   - Leave a blank line between sections (`graph attrs`, `node defaults`, `subgraphs`, `edges`) — humans read DOT files too.

7. **Write outputs to `.code-diagrams/`.** Auto-create with `mkdir -p` if missing. Honor `$CODE_DIAGRAM_DIR` if set. Filename: `<slug>.dot` plus `<slug>.<fmt>` for each requested format. `<slug>` is the kebab-cased scope (e.g., `auth-login-flow`, `ingest-pipeline`, `order-state-machine`). If the file already exists, ask: **Overwrite** / **Suffix with `-v2`** / **Cancel**.

8. **Render via `dot`.** Run once per requested format:

   ```bash
   dot -K<engine> -T<fmt> -o .code-diagrams/<slug>.<fmt> .code-diagrams/<slug>.dot
   ```

   `-K` selects the engine (`dot`, `neato`, etc.); omit it to use `dot`. If `dot` exits non-zero, surface stderr verbatim — most failures are syntax errors in the generated DOT, and the line/column from Graphviz pinpoints the problem.

9. **Report.** Print the paths of the `.dot` and rendered files, plus a one-line summary: graph type, engine, node/edge counts, target. Example:

   > Wrote `.code-diagrams/auth-login-flow.dot` and `.code-diagrams/auth-login-flow.svg` — state machine, `dot` engine, 8 nodes / 11 edges, browser target.

## Default Decisions When Asked Nothing

If the user gives no signal on these, default as follows — but mention each default in the report so the user can redirect:

| Question | Default |
|---|---|
| Graph type | Inferred from material (see step 3). If genuinely ambiguous, ask. |
| Layout engine | `dot`. Switch only if the graph is cyclic or peer-style. |
| Consumption target | Browser (SVG). Switch to PDF only if the user mentions print, A4/letter, sharing as a doc, or "for the deck". |
| Format | SVG. Add PNG if the user mentions embedding in markdown or chat. PDF only if print-targeted. |
| `rankdir` | `LR` for pipelines/sequences; `TB` for trees/hierarchies/state machines. |

## Anti-patterns (don't do these)

- **One mega-graph for everything.** If the scope spans many subsystems, split into one diagram per subsystem and link them with a small overview diagram. Dense graphs are unreadable regardless of layout.
- **Color as decoration.** If a color carries no meaning, use one. A rainbow palette tells the reader nothing.
- **Edges labeled "calls" / "uses" / "→".** The arrow already says that. Label edges only when the *kind* of edge varies (trigger conditions, error vs success, sync vs async).
- **Inventing nodes.** If the user described 6 steps, the diagram has 6 nodes. Do not pad with assumed steps to make it look more thorough — flag the gaps and ask.
- **Skipping clusters.** Any graph with >10 nodes that has natural groupings should use `subgraph cluster_*`. Without clusters the layout collapses into spaghetti.

## Prerequisites

- `dot` (Graphviz) installed locally. Verified by step 1.
- No other runtime. Pure file I/O + shelling to `dot`.

## Environment Variable Reference

| Variable | Default | Purpose |
|---|---|---|
| `CODE_DIAGRAM_DIR` | `.code-diagrams/` | Where generated `.dot` and rendered files are written. |
| `CODE_DIAGRAM_ENGINE` | `dot` | Override the default Graphviz engine. |
