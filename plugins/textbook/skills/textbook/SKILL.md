---
name: textbook
description: Typeset technical content (a design, a plan, a subsystem explainer, a research report) as a polished PDF engineering reference with a title page, linked contents, numbered sections, styled tables, callouts, and inline SVG diagrams. Pages are sized for tablets and e-ink readers (reMarkable, iPad), so they read at full size without zooming. Use when the user asks for a "textbook", a formatted or printable PDF, or a design reference rather than a plain markdown file.
allowed-tools: Write, Edit, Read, Bash
---

# Textbook Skill

Turn technical content into a PDF that reads like a typeset engineering
reference. The house style has:

- a centered title page with gold rules
- a contents page whose entries are links, with page numbers
- numbered sections with underlined headings
- justified serif body text
- navy-header tables, blue/red/green callouts, and dark code blocks
- inline SVG diagrams

Structural accents (section and contents numbers, kicker overlines, the quote
border) are steel. Gold appears only in the title-page rules.

The pipeline is **HTML + CSS rendered to PDF with WeasyPrint**, run through an
ephemeral `uv` environment so nothing is installed into the project.

## Page size

The page is **158 x 210 mm**, the 4:3 shape of a reMarkable 2 screen. A
reMarkable Paper Pro and a 13" iPad are also 4:3, and an 11" iPad fits the page
by width. Each device shows the page at about its printed size, so the type sizes
in `textbook.css` are what the reader sees: 11.5pt body text, with no zooming or
pinching. The margins are narrow because the device bezel already frames the
page.

For a paper copy, add one line to the document's `<head>`:

```html
<style>@page { size: A4; margin: 2cm; }</style>
```

The type sizes suit paper unchanged.

## When to use

- The user asks for a "textbook", a formatted or printable PDF, a design
  reference, or a document to read on a tablet or e-reader.
- There is substantive content to typeset: a plan, a design review, a subsystem
  walkthrough, a research report. For a quick note, a markdown file is fine.

## Prerequisites

- `uv` on PATH.
- WeasyPrint's native libraries (pango, cairo, harfbuzz). Check once:
  `ldconfig -p | grep -E 'libpango-1|libcairo|libharfbuzz'`.
- Serif, sans, and mono fonts. The stylesheet targets Nimbus Roman / Liberation /
  DejaVu, which most Linux installs include. Check with
  `fc-list | grep -iE 'nimbus|liberation'`.

## Assets

The skill's `assets/` directory (`${CLAUDE_PLUGIN_ROOT}/skills/textbook/assets/`)
holds three files:

- `textbook.css` - the canonical stylesheet. Reuse it as is; do not fork it per
  document. Besides the base elements it defines these components (see
  "Document components" near the end of the file):
  - `.pipe-label` - an uppercase kicker line above a section heading
  - `.paper` - a green callout for a cited source
  - `.callout` - a labelled callout, blue by default, red with `.warn`
  - `.pillrow` / `.pill` - a row of small tags
  - `.wcard` - a three-column worked-example card
  - `img.render` - an embedded screenshot
- `template.html` - a skeleton document: title page, linked contents, one
  sample section (table, quote, note, code block), and a gallery section with
  copy-paste markup for every component above.
- `diagrams.html` - copy-paste inline-SVG figure patterns:
  - A: a vertical pipeline of small left-aligned nodes with captions to the right
  - B: before/after panels with a dashed cut line and external braces
  - C: a two-outcome branch
  - D: a vertical timeline with node markers and ID badges

## Workflow

1. **Gather and verify content.** Pull the substance from the source (a plan,
   a review HTML, a design doc, the conversation). **Check every code symbol you
   cite** (function, class, and file names) against the codebase before writing.
   A reference that names a function that does not exist is worse than no
   document.

2. **Set up a build directory** in a scratch location, with the HTML and CSS side
   by side so the stylesheet link resolves:
   ```bash
   BUILD=$(mktemp -d)
   cp "${CLAUDE_PLUGIN_ROOT}/skills/textbook/assets/textbook.css" "$BUILD/"
   cp "${CLAUDE_PLUGIN_ROOT}/skills/textbook/assets/template.html" "$BUILD/book.html"
   ```

3. **Write `book.html`.** Fill in the title page, then write the sections. The
   stylesheet expects these conventions:
   - Section heading: `<h2 class="section" id="s3"><span class="snum">3</span> Title</h2>` (the space keeps "3 Title" readable in the PDF bookmarks).
   - Subsection: `<h3 class="sub" id="s3-2">3.2&nbsp;&nbsp;Title</h3>`.
   - Each top-level section is a `<section class="new-page">`, which starts a
     new page.
   - Each contents entry links to its heading's id:
     `<div class="l1"><a href="#s3"><span class="num">3</span>Title</a></div>`.
     The stylesheet fills in the dot leader and page number. Keep the numbers
     and ids in sync with the headings.

4. **Diagrams are inline SVG only.** Never ship ASCII art or HTML/CSS box stacks
   as a figure; they read as unprofessional. Copy a pattern from `diagrams.html`
   and adapt it, following the rules in that file's header: a unique marker id
   per figure, inline styling, and label sizes large enough to read on a tablet.

5. **Rules-editor pass.** Before rendering, spawn an editor subagent. Give it the
   drafted prose and the "Prose style" rules below. Its task: tighten the prose to
   those rules, keep every factual claim, and return the revised HTML plus a
   one-paragraph note on what it cut. Apply its edits. A first draft comes out
   denser and more ornamented than the reader wants, and its author cannot see
   that.

   The editor has two duties, in order:
   - **Concrete definitions.** For every sentence that introduces an object,
     field, or mechanism, ask: does it say plainly what the thing is? Rewrite
     each one that describes it through abstraction (see "Definitions are
     concrete" below).
   - **Word sweep.** Search the draft for every entry in the "Commonly abused
     words" table and replace each hit.

   A pass that only trims adjectives has failed.

6. **Topic-blind reader pass.** Spawn a second subagent that receives the
   document text and nothing else: no session context, no rules, no summary of
   the system. Brief it as a competent engineer who knows the field but nothing
   about this project. It reports every comprehension break:
   - a sentence or paragraph whose point arrives at the end instead of the start
   - a term used before it is defined
   - a passage that had to be re-read
   - a definition that could describe many different things

   It reports; it does not rewrite. Apply the fixes yourself. This pass exists
   because the author and the rules editor both know the material, so a slow
   build-up does not feel slow to them. Only a reader without that context finds
   where the document fails.

7. **Render the PDF** with an ephemeral uv environment (no project install).
   Write to `docs/<NAME>.pdf` unless the user names another path:
   ```bash
   uv run --no-project --with weasyprint python -c "
   from weasyprint import HTML
   HTML('$BUILD/book.html').write_pdf('docs/NAME.pdf')
   "
   ```
   WeasyPrint prints font-substitution warnings on stderr; they are harmless.

8. **Check the render as images.** Read the output PDF with the Read tool and a
   `pages` range. Look at the title page, the contents, every diagram page, and
   every table-heavy page. Check that diagram labels are legible at page size
   and that nothing overflows the narrow column. Do not trust the markup. Edit
   `book.html` and re-render until it is right.

9. **Deliver the PDF.** The PDF is the deliverable. Do not also leave a parallel
   `.md` unless asked, because two sources drift apart. If the user wants the
   build to be reproducible from the repo, offer to commit `book.html` and
   `textbook.css` next to the PDF.

## Research reports

These conventions apply on top of the base workflow when the document is
research: a survey of an external tool, a vendor evaluation, a release-gap
study. They come from three vendor-research papers, and were revised after a
review rejected one of them for an unverified lead finding. The examples below
are from that project; the rules apply to any codebase.

Structure:

- **An "Audience and Scope" block** directly after the title material. It names
  the intended reader, the source classes used (official docs, changelog,
  source code, our own measurements), the as-of date of every fetch, and how
  claims about our own installation were verified.
- **An opening "The Findings That Matter" section**: the few findings that
  organize the document, each with a pointer to the section holding its
  support.
- **A numbered Sources table** as the final section, one row per source with
  its URL. Body text cites by bracketed number. State the fetch date.
- **Evidence tiers are labeled in place.** Strong claims cite a source or a
  measurement. Weaker evidence is visibly marked as such, in one of three
  forms: a note box, the word "unresolved", or the phrase "our synthesis".
  A reader must be able to tell, sentence by sentence, which tier they are on.

Accuracy rules — each of these caused a real revision when broken:

- **Findings are ranked by evidence, and an unmeasured claim is never the
  lead.** A capability nobody has run is an open question, not a finding. If
  the corpus and ground truth to measure it exist in the repo, run the
  measurement and report numbers; a paper that says "we have not tested it
  but ..." where a test was available will be sent back.
- **Never assert what the project has or has not done without checking the
  repo's own history.** "We never enabled X" or "we have not tried Y" must be
  grounded in a search of git log, the ticket branches, and any experiment
  directories —
  a single counterexample kills the claim and the paper's credibility with
  it. Example: a rejected paper led with "we have never turned chart
  extraction on" while a merged ticket had exercised the parser's chart
  handling and documented its failures.
- **Claims about our own code come from reading the code that session,** not
  from memory or from what a default would imply. Example: the same rejected
  paper stated that a library's OCR default applied to the project, while the
  project's parser module explicitly set a different engine.
- **Numbers are transcribed, never recalled.** Every benchmark figure, version
  number, and date is checked against its source before the render; a
  verification pass by independent subagents (one per section, each re-fetching
  that section's cited sources) is the standard before delivery.

### The research team workflow

The revision that rebuilt a rejected paper into an accepted one ran this
team; reuse the shape. Stages 1–3 gather evidence and run before any prose
is written; stages 4–5 are the base workflow's editor passes.

1. **Evidence team, in one parallel fan-out.** One verifier agent per paper
   section, each re-fetching that section's cited sources and returning
   per-claim verdicts (confirmed / contradicted / unverifiable, with the
   correction). Alongside them: a repo-history agent that assembles what this
   project has actually done on the topic (git log, merged branches,
   experiment directories), and a ground-truth reader that turns any existing verified
   artifact into a scoring table. Structured output for every agent — the
   orchestrator gets data, not prose.
2. **Run the measurement yourself.** Where a claim can be measured with
   assets on hand (a corpus document plus ground truth), run it before
   writing: one variable per run, the production configuration as control,
   scored per value.
3. **Independent scorer with source access.** A separate agent scores the
   measurement against ground truth and — critically — checks every extra
   value the tool emitted against the rendered source pages. Recall against
   ground truth misses fabrication; only reading the page catches it. The
   author also cross-checks the scorer with a deterministic comparison;
   the two must agree before a number enters the draft.
4. **Rules-editor pass** (base workflow step 5), with every measured figure
   listed as a hard constraint the editor may not alter.
5. **Topic-blind reader pass** (base workflow step 6). Expect it to catch
   framing sentences the measurements made false — it did.

## Prose style (hard-won)

The register is clinical and exact, like a well-written spec - never
descriptive or literary. The reader is catching up, not admiring the writing.
Meandering prose is the writing equivalent of high cyclomatic complexity: the
reader holds unresolved ideas across branches before any of them lands.

- **Lead with the point.** State the claim in the first sentence, then support
  it. Never build a passage backwards through framing toward its conclusion.
- **No "mystical prose" — takeaway first, narrative second.** The owner's name
  for the failure where a sentence or paragraph opens with narrative buildup
  and lands its concrete point as a closing payoff. It reads like an adventure
  novel and forces the reader to hold suspense to learn what the passage is
  about. Flagged live: "Its one absolute constraint is value preservation: it
  may rearrange, never invent" — the actual rule arrives last, behind an
  abstraction. Reversed: "The agent may rearrange cells; it may never invent a
  value. Two checks enforce this. The grounding guard rejects ... The
  coordinate-frame check rejects ...". The order at every level is: what you
  found / the rule / the takeaway, then the mechanism, then the story. The
  reader can then stop reading at any point and lose only detail, never the
  point. Signatures to hunt: an abstract noun announced before the concrete
  rule ("its one absolute constraint is X: ..."), a colon holding the real
  content to the end of the sentence, and a paragraph whose last sentence is
  the one the reader needed first.
- **Definitions are concrete, in everyday words.** When introducing an object
  or mechanism, say plainly what it is and what it contains - never gesture at
  it through couched abstraction. Flagged live, all from one section: "The
  subsystem is built on four models" (name them and say what each is); "One
  cell paired with its geometry and its evaluation" (say: the cell's text,
  where it sits on the page, and what the evaluation concluded); "holds both
  views of the grid plus everything a consumer needs" (list what it actually
  holds); "CellMerge is what the agent says. MergedCellBox is what survives
  validation." (a clever parallel where a definition should be). Test: if the
  first sentence could describe many different things, it has not defined this
  one.
- **One idea per sentence, in order.** Break asides into their own short
  declarative sentences instead of stacking subclauses. Do not overdo the
  staccato either - vary rhythm naturally; the rule is one *idea*, not one
  clause. Watch opening sentences especially: "each X is either A - defined
  this way - or B - defined that way" is three sentences (the split, then one
  per state), not one. Flagged live as "trying to do too much". Other
  signatures of an overloaded sentence: an em-dash apposition defining a term
  while the sentence is also making a claim, a definition plus a mechanism
  plus a consequence in one breath, and colon lists whose items each have
  their own subject and verb.
- **Enumerations are lists, and parallel things get parallel sections.** When
  a sentence enumerates three or more named things, make it a bulleted list -
  prose enumeration buries the items. Calibrate the bullets to their job: when
  the items get full subsections below, each bullet is a one-line orienting
  gloss ("GroundedCell - one cell and its trace to the page") plus a preview
  sentence ("Each model is described below: why it exists, then what it
  holds") - full why-sentences in the bullets were flagged as "full paragraphs
  pretending to be bullets", and bare labels ("one table cell") as "bland and
  uninformative". When the list IS the whole treatment, the bullets carry the
  information.
- **Why before what, at every level.** Sections, subsections, paragraphs, and
  descriptions all state the need before the thing. At list level this means
  the why lives in each item's subsection, not duplicated into its bullet. When a section covers N parallel objects,
  give each object its own subsection, titled by the object's plain name alone
  ("CellMerge", never "Merge reports: CellMerge and MergedCellBox" - a header
  that packs description stops being a header), and open every subsection with
  the same pattern: why the object exists, then what it holds. And the count
  must match the list: flagged live, an intro said "four models" while the
  section named five.
- **One topic per paragraph, stated in the first sentence.** The same rule one
  level up. A paragraph opens by stating its one topic, the following
  sentences support that topic in order, and it never picks up a second topic
  midway. If a paragraph defines a thing and then narrates its history, or
  states a rule and then introduces the next mechanism, split it at the turn.
  The first sentences of a section's paragraphs, read alone, should form a
  coherent outline of the section.
- **No metaphor chains or coined abstractions.** A plain statement beats an
  elegant abstraction ("the model cannot produce accurate coordinates", not
  "coordinates are a currency it does not hold"). Figurative phrasing survives
  only inside quoted material.
- **Name mechanisms by what they do, never by an image.** "The dropped-text
  check", not "the net"; "the `table_id` join", not "the bridge". A coined
  noun forces the reader to carry a private dictionary through the document,
  and section titles built on one ("The Stacked Seam", "The Review Channel")
  are the worst offenders because they recur in the contents. A recurring term
  must be a code symbol or a standard technical term.
- **Watch for internal deliberation vernacular leaking into the document.**
  Words that evolve as private shorthand while working the problem read as
  jargon to the owner. Before delivering, grep the draft for the words in the
  table below AND for your own most-repeated non-standard words, and replace
  each with the plain operation.
- **No lingo or manager-speak.** Cut any phrase that sounds convincing without
  conveying information ("pulling on axes", "navigable object"). Define every
  non-standard term at first use, in plain words.
- **Facts survive the edit.** Tightening never drops a measurement, a
  condition, or a caveat that changes the claim. If a sentence is neither the
  point nor evidence for it, cut the sentence, not the evidence.
- **Justify designs on principle, never on a repaired break.** "This rule
  exists because its absence failed: <anecdote>" reads as an overfit patch and
  raises technical doubt even when the design is sound. State the information
  need or invariant the design serves; the incident is at most evidence after
  the principle. Flagged live: a chunking rule justified by "a question naming
  a section retrieved the heading fragments" — rewritten as "questions name
  sections; the paragraph that answers never names its own section; the prefix
  puts the question's words on the chunk that can answer it."
- **A forward reference must state its debt.** Never lean on a later section
  to explain why the current sentence matters ("...which the collapse rule in
  section 6.2 uses"). A linear reader will not page forward. State the problem
  here in full, then defer the solution explicitly: name the cost, show it in
  execution terms, and say "that is a ranking problem, deferred to section
  6.2." If the current section has nothing to say about the problem, cut the
  reference instead.
- **Design intent needs its why and its execution shape.** "Deliberately
  redundant" is an assertion until the passage says why the design stores both
  forms (the question's shape is unknown until asked) and what the redundancy
  concretely looks like at run time (the same cells indexed twice, each with
  its own embedding, competing in one ranking). Flagged live; the owner asked
  "what does that look like in execution terms."
- **Prose that describes visual structure becomes an exhibit.** A paragraph
  narrating a shape (a stacked table header, a page layout) is confusing where
  a small figure plus two sentences is clear. Render the real artifact when
  possible — a screenshot of the actual page with the confusion marked beats a
  described example. Define domain terms ("page decoration") in plain words at
  first use in the section that uses them; readers jump into sections cold.
- **Type is sized for the device.** The page is tablet-sized and the body is
  11.5pt, both set in `textbook.css`. Do not shrink type or widen the page
  with per-document overrides. If content does not fit, cut content, not point
  size. The only sanctioned override is the A4 `@page` line for paper copies.

### Commonly abused words

Every entry below was flagged by the document owner in a real review. Grep the
draft for each before rendering. The pattern generalizes: an image standing in
for a mechanism, or one vague verb doing the work of six precise ones.

| Abused word | Why it fails | Write instead |
|---|---|---|
| carry / carries | one verb covering six operations | the specific verb: holds, records, includes, transfers, lists, copies |
| measured (as provenance shorthand) | private vernacular | reconciled; traced back to its printed source |
| boxed / unboxed / unboxable | private vernacular built on a field name | reconciled / unreconciled; a cell with no printed source to trace back to |
| frames / shapes / informs | asserts influence without a mechanism | state the actual effect: forced a revision, determines |
| seam | image for a boundary | the boundary between X and Y |
| net (metaphor) | image for a check | the check, the guard |
| bridge (metaphor) | image for a join | the join on `<key>` |
| channel (non-I/O) | image for a record or path | the artifact, the record |
| doctrine | grandiose for rule | rule, policy |
| lineage | grandiose for history | history, the sequence of changes |
| residue / residuals | statistics metaphor for leftovers | the remaining cells, what is still unmatched |
| throughline | narrative metaphor | the common cause, the shared mechanism |
| plants / planted (a value) | image verb | assigns, places |
| pin / pinned (a location) | image verb | locate |
| home ("measured home") | image noun | source |
| coined (a label) | jargon for invented | invented |
| headline (a number) | journalism | the coverage figure, the reported rate |
| twin (rows/values) | ornament | identical |
| carries weight | idiom | is needed, matters because ... |
| leverages / unlocks | manager-speak | uses, enables - or state the effect |
| handle (metaphor) | image for a field or key | the field, the id, the key the join uses |
| family (grouped records) | image for related rows | the table and its line records; the group sharing `<key>` |

## House-style rules (hard-won)

- **Headers are clean labels.** Never fold inline code or a trailing clause into a
  bold header (write `Output` as a header, then a normal sentence - not
  `Output - always a list[...], one of two shapes` in one bold line).
- **Diagrams are SVG, consistently across the whole document** - not just the
  first one.
- **Flow diagrams:** prefer small left-aligned nodes with caption text to the
  right (pattern A) over large centered text-filled boxes.
- **Before/after figures:** the "before" panel shows the un-annotated state (e.g.
  no cut line if the cut was not identified there); group "after" pieces with
  external braces (pattern B), not inline labels crammed inside the box.
- **Two-outcome logic:** green for the success path, gold for the default/fallback
  path (pattern C). This is the one use of gold outside the title page.
- **History / timelines:** use pattern D (vertical timeline). Color-code node
  badges by state - navy for main milestones, green for spikes/sub-items, grey
  for closed or superseded.
- **Kickers stay with their header.** A `.pipe-label` overline is pinned to the
  heading beneath it (`break-after: avoid`); never let a page break strand it.
- **Color discipline:** navy `#1f3756`, steel `#46647f`, green `#2f6b3f`, gold
  `#bd8b1c` (title-page rules only); light fills `#eef3f9` / `#eef7f0` / `#f3f6f9`.
  Defined as CSS vars in `textbook.css`. Keep gold sparse - structural accents
  (numbers, kickers) are steel, so the palette reads as one calm navy/steel
  hierarchy rather than gold clashing against blue.
- **Fit the narrow column.** The text column is 138 mm wide. Keep tables to four
  columns or fewer; split a wider table, or turn it into one table per entity.
  Code blocks wrap rather than clip (`white-space: pre-wrap`), but a code line
  over about 65 characters wraps mid-token, so break long signatures by hand.
- **Verify before you assert.** Symbols, file paths, and any performance/behavior
  claim must be grounded in something you actually checked this session.
