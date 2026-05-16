# STATUS

Live progress of autonomous work. The agent updates this file after
every merged PR while running in `/loop` mode. When you (the human)
come back, read this first — it summarises what changed and what's
still in flight without needing to scan every PR.

**Last update**: 2026-05-16 (end of `/loop` iteration 2)

## What this `/loop` iteration accomplished

User re-invoked `/loop` and pointed me to `pzfreo/wormgear` for Helix
examples. Did one productive investigation; no new features shipped
this iteration. The first iteration shipped #75/#76/#77.

**Net result**: one open PR (#89 — docs only) carrying the deeper #33
investigation findings.

## Recently merged (last 5)

- PR #88 — `docs: end-of-loop STATUS + bail logs for #33 and #43`
- PR #86 — `docs: bail decision for #36 + STATUS update`
- PR #87 — `feat: shared runtime helpers via --shared-helpers (closes #77)`
- PR #84 — `feat: FreeCAD Labels for variable names (closes #76)`
- PR #83 — `feat: Locations contexts for uniform patterns (closes #75)`

## Open / WIP (mine)

- **PR #89** — #33 deeper-investigation docs (wormgear empirical
  table). Awaits your review. Docs-only.

## What I did this iteration

1. **Re-ran sample_813 audit** against current main: 0 fixtures
   changed status (PASS=60, TRANSLATE_FAIL=23, VERIFY_FAIL=3).
   Confirms #75/#76/#77 were emit-quality improvements, not new
   feature support — no previously-failing fixtures became unblocked.
   No EXCLUDED list updates needed.

2. **Re-investigated #33** with wormgear reference (per user
   pointer). Empirical table on synthetic helix-sweep fixture:

   | Mode | Volume |
   |---|---|
   | FreeCAD Sweep, Frenet=True (truth) | **7.854** |
   | FreeCAD Sweep, Frenet=False | 3.163 |
   | build123d `sweep(profile, path=Helix(...))` | 3.16 |
   | OCP MakePipeShell, default | 2.44 |
   | OCP MakePipeShell, SetMode(True) [Frenet trihedron] | 5.24 |
   | OCP MakePipeShell, SetMode(gp_Dir(0,0,1)) [wormgear] | 5.24 |

   None of the OCP modes match FreeCAD's Frenet=True. Likely OCCT
   version difference. wormgear works around it by staying entirely
   in build123d — we can't (we translate). Still bailing.

3. Updated `docs/autonomous-decisions.md` and issue #33 with the
   empirical table.

## Open issues remaining (unchanged from last iteration)

- **#18** Render gallery — needs renderer design decision
- **#33** Part::Helix — OCCT version gap; bailed twice
- **#36** Fillet face-adjacency — OCCT capability gap
- **#38** Post-Midplane — per-fixture investigation
- **#43** Coherent snap — deep refactor, needs interactive A/B
- **#60** WallHungBidet Hausdorff — per-fixture investigation
- **#78** Builder-mode emit — alt style, tier 3

## Why I'm stopping the loop

Every remaining open issue falls into one of:

- **Deep refactor needing interactive A/B testing** (#43, #78)
- **Build123d/OCCT capability gap not fixable in translator** (#33, #36)
- **Per-fixture geometric-bisect investigation** (#38, #60)
- **Design decision required** (#18 renderer choice)

None are appropriate for autonomous mode. Continuing the loop would
either burn cycles on speculative work or repeat the previous
iteration's bail decisions.

The translator is in a strong, stable state:

- 239 tests passing
- 4/5 emit-quality assertions PASS (only #43 still xfailed)
- 171 corpus pass-count floor gate green
- 12 fully-resolved features shipped across the prior session + this loop

## Recommended next session

When you have a focused session, the highest-value items are:

1. **#78 Builder-mode emit** — biggest visible polish, fixed scope,
   doable in a focused session. Test stubs already in
   `tests/test_emit_quality.py`.
2. **#43 Coherent snap with interactive A/B** — well-documented
   failure mode, you can spot regressions in the corpus suite as they
   happen.
3. **#38 / #60** — per-fixture bisection. 1-2 days per fixture but
   tractable with focus.
4. **#36 / #33** — only worth revisiting if build123d / OCP gain
   capabilities we currently lack.

## Stop conditions

The agent stops and waits when:

- It would need to add a fixture to `EXCLUDED_FROM_TEST` (CLAUDE.md
  requires explicit approval).
- It would need to edit `CLAUDE.md`, `SPEC.md`, `docs/adr/`, or
  `.github/`.
- A deep refactor has failed twice with the same class of error.
- The corpus-running-count regression gate trips.
- (This iteration:) all remaining work needs human-supervised testing
  or capability changes upstream.
