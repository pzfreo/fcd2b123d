# Derivative libraries strategy

**Status**: strategy doc — concrete architecture in `docs/design/family-extraction.md`
**Last updated**: 2026-05-17
**Audience**: project maintainers and future agents working on follow-on libraries

> **Update (2026-05-17):** the steady-state workflow for generating
> parametric build123d classes from the FreeCAD library is described
> in [`docs/design/family-extraction.md`](../design/family-extraction.md)
> (manifest-driven, deterministic, class-based output matching
> `bd_warehouse`). This strategy doc remains the **strategic framing**
> (what to build, why, what scope); the design doc covers the **how**.
>
> The hand-written-Python workflow described later in this doc was the
> `bd-freecad-library` v0.x bootstrap. The steady state replaces
> step 3 ("hand-write parametric Python") with translator output from
> manifests.

## What this document is

`fcstd2b123d` translates FreeCAD `.FCStd` files into build123d Python. As
of writing, the corpus has 310 working translations against a ~3,194-file
FreeCAD Parts Library, with a 60–69% accurate-translation rate on
truly-random library samples.

That is an engineering milestone, but it isn't yet a *dataset* milestone
nor a *library* milestone. This document records the strategic framing
for what we'd build *next*, on top of the translator, to compound the
work into something durable for the build123d ecosystem.

The discussion that produced this document was had over voice +
keyboard; the framing is owned by the project maintainer (Paul Fremantle).

## The FreeCAD Parts Library, categorised

Measured composition (from `data/parts-library/coverage.json`):

| Top-level category | Files | % of library |
|---|---:|---:|
| Mechanical Parts | 2,498 | 78.2% |
| Electronics Parts | 241 | 7.5% |
| Architectural Parts | 121 | 3.8% |
| Generic objects | 72 | 2.3% |
| Electrical Parts | 70 | 2.2% |
| Logistics | 50 | 1.6% |
| Industrial Design | 46 | 1.4% |
| HVAC, Medical, Pipes, etc. | ~96 | ~3.0% |

Within Mechanical Parts: **Fasteners** (1,034), **Profiles EN** (909),
**Chains** (462), **Bearings** (26), **Pulleys** (7), and others
account for the bulk.

### Four-category framing (the strategic view)

The category breakdown that matters for deciding *what to build on top
of the translator* is value-driven, not directory-driven:

```
~48%  bd_warehouse covers it             (stock fasteners/bearings/chains)
~25%  bd_warehouse should cover it       (EN profiles → curation candidates)
 ~5%  parametric, application-specific
~10%  individual exemplary patterns
 ~7%  individual catalog reproductions
 ~5%  out of scope (mannequins, scripted features)
```

The original three-category model proposed by the maintainer was:

1. **Stock parts already covered by bd_warehouse.**
2. **Other parametric components not yet covered.**
3. **Individual non-parametric parts useful to designers.**

Refined here to:

* **Category 1 (~48%) — bd_warehouse-equivalent.** Stock parts where
  bd_warehouse offers a parametric function (`SocketHeadCapScrew(...)`).
  Translator's value: fidelity reference, educational. Designers will
  reach for the bd_warehouse function for new work.
* **Category 2 — parametric components not yet in bd_warehouse.** Split:
  * **2a (~25%) — bd_warehouse candidates.** Standardised,
    internationally specified, uncontroversial as new stdlib modules.
    EN steel sections are the canonical example. *Highest strategic
    leverage of any category.*
  * **2b (~5%) — application-specific parametrics.** Custom phone case
    parametric in screen size, custom mount parametric in hole pattern.
    Parametric in their own world but never going to be stdlib.
* **Category 3 — individual designed parts.** Split:
  * **3a (~10%) — exemplary patterns.** Typical L-bracket, typical
    housing. Reference for *how* to design something like this.
  * **3b (~7%) — specific commercial parts.** A particular Hammond
    enclosure, a Nema-17 bracket. Dimensions fixed by manufacturer
    spec — value is catalog accuracy, not pattern transfer.
* **Category 4 (~5%) — out of scope.** Mannequins (mesh-based People
  figures), scripted-feature parts (`Part::FeaturePython` with custom
  Python plugging into FreeCAD), and other category mismatches with
  build123d's solid-CAD domain. We correctly refuse these today per
  the "do it properly or not at all" rule.

The 2a/2b split is the load-bearing one. **2a is where translator
output compounds into ecosystem leverage**; 2b is per-part work that
doesn't.

## bd_warehouse overlap

`bd_warehouse` (gumyr's curated parametric library for build123d)
provides these modules: `bearing`, `fastener`, `flange`, `gear`,
`open_builds`, `pipe`, `sprocket`, `thread`.

Mapped against the FreeCAD library:

| FreeCAD category | Files | bd_warehouse coverage |
|---|---:|---|
| Mechanical → Fasteners | 1,034 (32%) | ✓ `fastener.py` |
| Mechanical → Chains / Sprockets | 462 (15%) | ✓ `sprocket.py` |
| Mechanical → Bearings | 26 (1%) | ✓ `bearing.py` |
| Mechanical → Profiles EN | 909 (28%) | mostly NO (`open_builds.py` is t-slot only) |
| Mechanical → Pulleys | 7 (0.2%) | partial (timing pulleys via `sprocket.py`) |
| Everything else | ~750 (24%) | NO |

**Direct parametric overlap: ~48%.** For those parts, designers reach
for bd_warehouse's parametric API; our translator's value is fidelity
+ educational reference.

The translator's distinctive value is the **other ~52%** — primarily
the EN steel profile family (28%), plus the long tail of
non-mechanical-stock parts (electronics, architectural, etc.).

## Proposed: two derivative repositories

### Repository 1: `build123d_standards` (working name)

**Scope**: parametric implementations of standardised parts that
bd_warehouse doesn't yet cover (category 2a). Optimised for "could be
a bd_warehouse module someday."

**Inclusion criteria**: a part qualifies if it is

1. Internationally standardised (ISO / DIN / ANSI / EN spec exists).
2. Dimensionally parametric (a small number of named dimensions
   reproduce the whole family).
3. Already translated cleanly by `fcstd2b123d` on at least one fixture
   in the corpus.
4. Has multiple variants in the FreeCAD library (proves it's worth
   parametrising rather than treating as a one-off).

**Starting scope (≤10 families)**, ordered by ROI:

| Family | Corpus variants | Notes |
|---|---:|---|
| ISO 4762 socket head cap screws | ~35 | Well-validated baseline |
| ISO hex nuts (4032/4034) | ~10 | Small family, very standard |
| ISO/DIN flat washers | ~10 | Trivial geometry |
| EN square hollow sections | ~25 | Clean parametric family |
| EN rectangular hollow sections | ~25 | Sibling of above |
| EN flat bars | ~20 | Trivial but common |
| HE-A / HE-B / HE-M steel profiles | ~20 | Higher complexity, high library coverage |

~7 modules cover ~250 FreeCAD library files (~8% of the library) but
do it well. **Better 7 perfect modules than 25 mediocre ones.**

**Per-module layout**:

```
library/iso4762_cap_screw/
├── __init__.py              # the parametric function
├── README.md                # human + LLM doc (rationale, standard ref, examples)
├── DIMENSIONS.md            # full dimension table, cited
├── tests/
│   ├── test_geometry.py     # parameter sweep, compare to fcstd2b123d fixtures
│   └── fixtures/            # symlinks/copies of relevant FreeCAD files
```

**README format** (the load-bearing piece):

* **What** (1 sentence)
* **Standard reference** (ISO/DIN/EN number + link)
* **Parameters** (table with units)
* **Worked example** (canonical call + image when #18 lands)
* **Why these defaults** (the LLM-relevant chain-of-thought section
  — explicit rationale for design choices and tradeoffs, missing from
  most stdlibs)
* **Validated against** (list of FreeCAD library files this matches
  geometrically, via `fcstd2b123d` regression tests)

Length: 200–400 lines per module.

### Repository 2: `build123d_parts_library` (working name)

**Scope**: hand-curated individual designs (categories 3a + 3b + 2b).
Optimised for "designer wants a starting point" or "LLM wants
real-world reference."

**Inclusion criteria**: STRICT — better 100–150 curated designs than
500 marginal ones. A design qualifies if it

1. Represents a class of common design problem (3a) OR is a
   recognisable commercial standard (3b).
2. Has a clear one-sentence "what it is" — if you can't write that,
   it's not ready.
3. Translates cleanly today by `fcstd2b123d` (PASS in the test corpus).
4. The original design is *good* CAD — no over-constrained sketches,
   no degenerate edges, no solver-damaged geometry. We don't enshrine
   bad designs.

**Per-design layout**:

```
designs/nema_17_mount_bracket/
├── nema_17_mount_bracket.py     # build123d code (translated, hand-polished)
├── source.FCStd                  # original FreeCAD file (LGPL, attribution)
├── README.md                     # what it is, when you'd use it, dimensions
├── upstream.json                 # source path, commit hash, sync metadata
└── render.png                    # iso view (when #18 lands)
```

**README format**:

* What it is (1 sentence)
* When you'd use it (1 paragraph)
* Key dimensions (not exhaustive — that's in the source)
* Original FreeCAD file (path + attribution)
* Modifications during translation (usually none)
* LLM-rationale section (why this design, what tradeoffs it makes)

## Licensing

This is non-trivial and worth getting right early.

The FreeCAD Parts Library is **LGPL-2.1+**. A parametric Python rewrite
is not a verbatim copy but is "derived from" in a meaningful sense.

### For `build123d_standards`

* **Implementation (Python code)**: MIT or Apache-2.0. Gives
  consumers maximum freedom, matches the build123d ecosystem
  convention.
* **Dimension data tables**: cite FreeCAD Parts Library as a source.
  Standards-body data (ISO 4762 dimension tables) is generally not
  copyrightable in most jurisdictions — it's measurement, not creative
  expression. Reading dimensions from a FreeCAD file to populate a
  table is fine.
* **Acknowledgement file** at repo root: explicit credit to FreeCAD
  Parts Library + link.

### For `build123d_parts_library`

Here LGPL inheritance is more important — we ship derived works of
*full designs*, not just abstracted dimension tables.

* **Repo license: LGPL-2.1+** to match FreeCAD's upstream. Avoids any
  "did this derivation respect the upstream license?" question.
* **Per-file attribution**: every `.py` file's docstring credits the
  original FCStd path and the FreeCAD Parts Library.
* **NOTICE / AUTHORS file** at repo root with full credit.
* **Don't change the license.** Inheriting LGPL is the lowest-friction
  legal path. Users who can't use LGPL stay with `build123d_standards`
  (MIT) for stock parts.

**Caveat**: this section is pattern-matching, not legal advice. Run
the license model past someone with actual IP knowledge before
publishing either repo.

## Upstream sync model

The translator's output is downstream of the FreeCAD Parts Library.
If upstream fixes a bug or redesigns a part, our derived artefacts
need a story.

### Per-repo impact

**`build123d_standards`**: largely insulated. Parametric code is
written from standards specifications, not from specific FCStd files.
ISO 4762's dimension table doesn't change because someone fixed a
FreeCAD modelling bug. The only coupling is via validation tests that
reference fixtures; if those diverge after an upstream change, re-run
validation, accept legitimate diffs (regenerate snapshot), or report
regressions back to FreeCAD.

**`build123d_parts_library`**: this is where the question bites. Each
design is tied to a source FCStd. Practical governance:

* **Pin the source.** Commit a copy of the source FCStd into the repo
  with an `upstream.json` recording path, commit hash, sync date.
* **Periodic sync window** (annual or semi-annual): pull upstream,
  re-translate the curated subset, diff against committed versions.
  Triage:
  * Trivial diff (FP noise from solver re-run) → ignore
  * Real bug fix → adopt + note in CHANGELOG with credit
  * Geometric redesign → fork decision (stay with version that
    matched our README, or re-curate)
* **Accept some staleness as a feature.** "Curated as of
  FreeCAD-PartsLibrary @ commit abc1234, dated 2026-Q1" gives
  consumers predictability. They opt in to sync; we opt in to maintain.

Maintenance burden estimate: ~7-8 designs to evaluate per year if
upstream touches ~5% per year. A morning's work with the right tooling.

### Bidirectional bug flow

The pessimistic framing assumes upstream is the source of truth and
we passively consume. In practice, our translator already finds bugs
in FreeCAD files:

* The solver-noise snap pass (#43) makes translations *cleaner* than
  the source — catching things FreeCAD's GUI accepted.
* Sketch validity issues surface during translation but were tolerated
  by FreeCAD's loose vertex tolerance.

**We can report those upstream.** The FreeCAD Parts Library accepts
PRs. A workflow where we file issues / PRs on the FreeCAD library for
each translation problem we find makes us an *upstream improver*, not
just a downstream consumer.

### Tooling to build

To make sync sustainable:

1. **`upstream.json` per design** — source path, upstream commit
   hash, sync date, last verified date.
2. **`tools/sync-from-upstream.py`** — fetches latest FreeCAD library,
   identifies which curated designs come from changed files, runs the
   translator, produces a diff report. Human reviews; tool doesn't
   auto-merge.
3. **CHANGELOG discipline** — every sync produces a public CHANGELOG
   entry citing which designs changed and why.
4. **(Later) CI drift watcher** — monitor FreeCAD library repo for
   commits touching files we curate. Auto-file an issue on our side
   when drift detected. Doesn't fix anything, just makes us aware.

### Worst-case scenarios

* **"FreeCAD redesigns a part significantly, our README still
  describes the old design."** Curation failure; the sync window
  catches it. Worst missed-sync downside: users get a 1-year-old
  version. Same situation any pinned dependency creates.
* **"FreeCAD library is abandoned or substantially reorganised."**
  Then we choose: become the de facto maintainer of the curated
  subset, or sunset our library. Worth thinking about now but remote
  — library has been actively maintained for 10+ years.

## bd_warehouse contribution policy

We don't know it. gumyr is the sole maintainer of bd_warehouse;
acceptance policy for LLM-derived contributions is unclear, and many
maintainers in this ecosystem have become more cautious recently
(license provenance, maintenance burden, style drift).

The strategy is deliberately **standalone-first**:

1. Build `build123d_standards` (and later `build123d_parts_library`)
   as standalone repos under our own attribution and licensing.
2. Once there's a real artefact to discuss — clean code, beautiful
   docs, validated geometry — approach gumyr with a direct question:
   *"Would you consider these as new bd_warehouse modules, or is
   standalone the better long-term home?"*
3. Three possible outcomes, all acceptable:
   * **Accepted as bd_warehouse modules**: best case — work compounds
     into the stdlib.
   * **Accepted only as human-authored / human-reviewed** (with LLM
     as drafting tool): also fine. The translator surfaces the
     skeleton; a human writes the final module from scratch.
   * **Stay standalone with cross-linking**: still useful. We
     position as a complementary library, not a competitor.

**Do not invest in a curation pipeline targeted at bd_warehouse
before talking to gumyr.** Asking the policy question upfront would
reduce the design risk to near-zero.

## Ordering of work

The work after the translator stabilises:

1. **Get `fcstd2b123d` to a stable state.** Close remaining
   easy issues (#92 / #94 / #95 / #97 already in flight, #43 done,
   builder mode shipped). Wrap in days, not weeks.
2. **Spin up `build123d_standards`.** Start with one module (ISO 4762)
   to nail the format — README, code, tests, validation harness. Then
   1–2 more to validate the template. Then expand to ~7 modules.
3. **Once `build123d_standards` is mature**, spin up
   `build123d_parts_library`. Same approach: nail the format on 5–10
   designs first, then expand to ~100–150.
4. **Approach gumyr with a concrete repo and concrete examples.**

Rough timing: step 1 wraps in days. Steps 2 and 3 are weeks each
(with the bulk being curation, not coding). Step 4 is one well-written
email plus the repo links.

## Open decisions

Worth resolving before either repo is started:

1. **Repo names.** Proposed: `build123d_standards` (or `bd_standards`)
   and `build123d_parts_library`. Alternative names welcome.
2. **Will `fcstd2b123d` be a dev dependency** of the new repos (used
   by validation tests) or fully decoupled? Recommend dev dependency
   — proving each parametric module matches the original FreeCAD
   source is the strongest possible quality story.
3. **Detailed curation criteria** for `build123d_parts_library`. The
   four criteria above are a starting point; expect to refine after
   the first 10 designs.
4. **Format for the LLM-rationale section** in READMEs. Worth
   prototyping on one module to settle the structure before
   replicating.

## What this strategy is not

* **Not a research dataset.** A useful dataset would need
  deduplication, negative examples, and a broader domain than the
  FreeCAD Parts Library covers. Could be a derivative effort later.
* **Not a competitor to bd_warehouse.** Even in the "stay standalone"
  outcome, we cross-link and complement; we don't replicate.
* **Not a guarantee of upstream contribution.** All three policy
  outcomes (accepted, partial, standalone) are acceptable. The
  strategy is robust to whichever one happens.

## Honest assessment of value at stake

* **Category 1 (~48%, bd_warehouse-overlap)**: necessary infrastructure
  but the *least* strategically interesting bucket. We're not adding
  to the ecosystem here; we're proving the translator works on the
  easy stuff.
* **Category 2a (~25%, parametric / standardised, not yet stdlib)**:
  the highest-leverage work. Every additional EN profile we translate
  is an extra row in an implicit dataset that should become a
  bd_warehouse module (or our own standalone equivalent).
* **Category 2b + 3a + 3b (~22%)**: real per-part value as reference /
  inspiration / migration material. Doesn't compound the way 2a does
  but it's where the long tail of designer needs lives.
* **Category 4 (~5%)**: out of scope; we correctly refuse.

The translator alone is a working translator. The translator plus
`build123d_standards` is a *contribution to the build123d ecosystem*.
The translator plus both repositories is a *contribution to CAD
generally*, useful for human designers and as paired data for LLMs
working with build123d.

The 60% accurate-translation rate is an engineering milestone, not a
dataset milestone. To make it the latter requires the curation work
described above — not more samples.
