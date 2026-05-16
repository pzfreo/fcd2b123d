# STATUS

Live progress of autonomous work. The agent updates this file after
every merged PR while running in `/loop` mode. When you (the human)
come back, read this first — it summarises what changed and what's
still in flight without needing to scan every PR.

**Last update**: 2026-05-16 (during `/loop`, just finished #75 implementation, about to PR)

## Currently working on

**#75 Locations contexts for uniform patterns** — implementation done locally on `feature/locations-contexts-75`.
PR not yet open. Full suite 237 passed, 4 skipped, 2 xfailed (the #43 and #76 trackers — expected).

## Recently merged (last 5)

- PR #82 — `docs+tools: autonomous runway` (STATUS, decisions log, preflight, count gate)
- PR #81 — `test: emit-source assertions` (xfail gates for #43, #75, #76)
- PR #80 — `docs: prioritised roadmap`
- PR #79 — `docs: emit code-quality review + style guide + project CLAUDE.md`
- PR #74 — `test: wire sample_813 into corpus`

## Open / WIP (mine)

- `feature/locations-contexts-75` — #75 implementation, ready to PR after this STATUS update lands.

## Abandoned / deferred (with reason)

*none*

## Next planned (per docs/roadmap.md)

1. **#76** — FreeCAD Labels for variable names (top-3, 1-2 days). Next up after #75 lands.
2. **#36** — Fillet face-adjacency (top-3, 2-3 days deep refactor).
3. **#43** — Coherent snap (tier 2).
4. **#77** — Shared helpers module (tier 2, half day).
5. **#33** — Part::Helix (tier 2, half day).

## Stop conditions

The agent stops and waits when:

- It would need to add a fixture to `EXCLUDED_FROM_TEST` (CLAUDE.md
  requires explicit approval).
- It would need to edit `CLAUDE.md`, `SPEC.md`, `docs/adr/`, or
  `.github/`.
- A deep refactor has failed twice with the same class of error —
  revert and skip the issue (logged to `docs/autonomous-decisions.md`).
- The corpus-running-count regression gate trips (someone landed a
  PR that drops it — the agent waits rather than fight the gate).

## How to read this file when you're back

1. **Recently merged** — what shipped. Each PR's body has the detail.
2. **Open / WIP** — anything I left mid-flight. Usually because CI
   was still running when the loop ended.
3. **Abandoned / deferred** — issues I tried but couldn't finish.
   `docs/autonomous-decisions.md` has the why-and-what-I-tried.
4. **Next planned** — what I'd pick up if `/loop`-ed again.
