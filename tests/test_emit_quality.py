"""Emit-source assertion tests — verify the *shape* of generated code,
not just that the resulting geometry is correct.

The corpus suite proves geometry equivalence; these tests prove the
emit is *good code*. They surface the open code-quality issues
(#43, #75, #76, #77, #78) as concrete failing assertions, so closing
those issues means removing the ``xfail`` marker — the test then
becomes a regression gate.

`xfail(strict=True)` is intentional: when the feature lands, the test
*passing* becomes a hard failure that forces the implementer to remove
the marker. That keeps the test honest and prevents "feature shipped
but the gate test still has xfail" drift.

Tests in this file are deliberately gated on the same FreeCAD-runtime
flag as `test_translator_tier1`; they exec the translator subprocess
and inspect its stdout.
"""

from __future__ import annotations

import pytest

from tests.test_translator_tier1 import _translate


# ---------------------------------------------------------------------------
# #75 — uniform patterns should emit `with PolarLocations` / `GridLocations`
# ---------------------------------------------------------------------------


def test_polar_pattern_uses_polar_locations() -> None:
    """A 6-fold uniform polar pattern should emit ONE ``PolarLocations(``
    term (algebra-mode multiplier), not six chained ``Rot(Z=k·60) * ...``
    terms.

    After PR #111 (algebra-mode absorption), this fixture's emit should
    use the absorbed ``PolarLocations(R, N)`` form (where R is the
    sketch's offset radius and N is the total occurrences) — not the
    pre-absorption ``PolarLocations(0, N-1, start_angle=θ, ...)`` form.
    """
    source = _translate("tests/fixtures/tier4_patterns/polar_pattern_holes.FCStd")
    assert "PolarLocations(" in source, (
        "expected a PolarLocations(...) term; emit still spells out copies"
    )
    # And no spelled-out Rot(Z=k) chain should remain for this single-Original
    # uniform pattern.
    rot_z_lines = sum(1 for line in source.splitlines() if "Rot(Z=" in line)
    assert rot_z_lines == 0, (
        f"expected no Rot(Z=) terms in polar-pattern emit; found {rot_z_lines}"
    )
    # PR #111 absorption: the emit should use the ``PolarLocations(18, 6)``
    # form. Detect the absorbed shape by checking for ``PolarLocations(18``
    # (R=18) — and confirm the unabsorbed ``PolarLocations(0,`` form is gone.
    assert "PolarLocations(18" in source, (
        "expected the absorbed `PolarLocations(R, N)` form (R=18 lifted "
        "from the sketch's Pos(18, 0)); emit still uses the unabsorbed "
        "`PolarLocations(0, N-1, start_angle=...)` workaround form"
    )
    assert "PolarLocations(0," not in source, (
        "the pre-absorption `PolarLocations(0, N-1, ...)` form should be "
        "gone — pattern absorption (#111) should collapse it"
    )


def test_linear_pattern_uses_locations() -> None:
    """A uniform linear pattern should emit ``GridLocations(dx, 0, N, 1)``
    (the absorbed form after PR #112), not a chain of ``Pos(i·dx, 0, 0)``
    factors or the raw ``Locations((x1,0,0), (x2,0,0), ...)`` enumeration."""
    source = _translate("tests/fixtures/tier4_patterns/linear_pattern_holes.FCStd")
    # PR #112 absorption: the emit should use the absorbed
    # ``GridLocations(dx, 0, N, 1)`` form.
    assert "GridLocations(" in source, (
        "expected GridLocations(dx, 0, N, 1) form after PR #112 absorption; "
        "emit still uses raw Locations enumeration"
    )
    # And the chained `Pos(i*dx, 0, 0) * extrude(...)` form should be gone.
    extrude_terms = source.count(" * extrude(")
    assert extrude_terms <= 1, (
        f"emit still chains extrudes via Pos; found {extrude_terms} ` * extrude(` terms"
    )


# ---------------------------------------------------------------------------
# Builder-mode body wrapping (#78 phase 2a) — regression gates
# ---------------------------------------------------------------------------


def test_builder_mode_wraps_body_in_buildpart() -> None:
    """``--style=builder`` should wrap a body's feature chain in a
    ``with BuildPart() as <body>:`` block, not emit the SSA cascade
    ``pad = extrude(...); pocket = pad - extrude(...); ...``."""
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_root = str(__import__("pathlib").Path(__file__).parent.parent / "src")
    out = subprocess.run(
        [fc_py, "-m", "fcstd2b123d", "--style", "builder",
         "tests/fixtures/sample_2026/IgnusNutMount.FCStd"],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": ":".join(p for p in (src_root, fc_pp) if p)},
    )
    assert out.returncode == 0, f"translate failed:\n{out.stderr}"
    source = out.stdout
    assert "with BuildPart() as" in source, (
        "expected `with BuildPart() as <body>:` wrapping the body's "
        "feature chain in builder mode"
    )
    # And the SSA-style cascade should NOT be present for the multi-
    # feature body — no ``fillet_001 = fillet(_edges_at(fillet_0, ...))``
    # rebinding.
    assert "fillet_001 = fillet(_edges_at(fillet_0" not in source, (
        "builder mode should NOT emit the SSA `fillet_NNN = fillet(_edges_at"
        "(<prev>, ...)) ...` cascade — features should operate in place "
        "on `<body>.part` inside the BuildPart context"
    )


def test_arc_start_angle_normalised_to_0_360() -> None:
    """``CenterArc(start_angle=...)`` should be normalised to [0, 360).

    FreeCAD's ArcOfCircle stores FirstParameter as a raw radian value that
    can exceed 2π (e.g. 514°). The start point depends only on
    ``cos(start_angle)`` / ``sin(start_angle)`` (360-periodic), so wrapping
    is geometry-preserving — and ``start_angle=154`` reads much better
    than ``start_angle=514``.
    """
    source = _translate("tests/fixtures/sample_2026/DIN463_M14TabWasher.FCStd")
    import re
    for m in re.finditer(r"start_angle=(-?\d+(?:\.\d+)?)", source):
        v = float(m.group(1))
        assert 0 <= v < 360, (
            f"start_angle={v} is outside [0, 360) — should be normalised "
            f"for readability"
        )


def test_polar_pattern_absorbs_pocket_in_builder() -> None:
    """Polar absorption in builder mode (PRs #107/#108/#110/#111).

    Algebra absorption (#111) runs first and produces a single
    ``polar_pattern = pad - PolarLocations(R, N) * extrude(Sketch()
    + Circle(r), ...)`` line. Because that line contains an inline
    ``Circle(...)`` (a context-aware build123d primitive), the
    builder-mode body transform correctly bails on this specific
    body — emitting the absorbed algebra form. Sketches in the rest
    of the module still get BuildSketch treatment (the disc sketch
    is the visible witness).
    """
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_root = str(__import__("pathlib").Path(__file__).parent.parent / "src")
    out = subprocess.run(
        [fc_py, "-m", "fcstd2b123d", "--style", "builder",
         "tests/fixtures/tier4_patterns/polar_pattern_holes.FCStd"],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": ":".join(p for p in (src_root, fc_pp) if p)},
    )
    assert out.returncode == 0, f"translate failed:\n{out.stderr}"
    source = out.stdout
    # Absorbed form must appear (R lifted to PolarLocations, N+1 instead
    # of N-1 with start_angle workaround).
    assert "PolarLocations(18, 6)" in source, (
        "expected absorbed `PolarLocations(18, 6)` form; absorption "
        "(PRs #107/#108/#110/#111) didn't fire"
    )
    # Sketches still get BuildSketch treatment.
    assert "with BuildSketch() as disc:" in source, (
        "Disc sketch should still emit as BuildSketch in builder mode "
        "even when the body bails to algebra"
    )
    # The opaque unabsorbed form should NOT be present.
    assert "PolarLocations(0, 5" not in source, (
        "unabsorbed `PolarLocations(0, N-1, start_angle=...)` form "
        "should be gone after absorption"
    )
    # Geometry equivalence is asserted by
    # test_builder_and_algebra_emit_same_geometry — no need to re-exec
    # here.


def test_mirror_body_falls_back_to_algebra_in_builder() -> None:
    """Mirror features in builder mode should fall back to algebra-style
    emit for the body chain (PR #114). The exec'd geometry must still
    match the algebra-mode result — that's the load-bearing assertion;
    the form is secondary."""
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_root = str(__import__("pathlib").Path(__file__).parent.parent / "src")
    env = {**os.environ, "PYTHONPATH": ":".join(p for p in (src_root, fc_pp) if p)}
    out = subprocess.run(
        [fc_py, "-m", "fcstd2b123d", "--style", "builder",
         "tests/fixtures/tier4_patterns/mirrored_tab.FCStd"],
        capture_output=True, text=True, check=False, env=env,
    )
    assert out.returncode == 0, f"translate failed:\n{out.stderr}"
    # Exec to confirm correctness (no NameError as in the pre-fix bug).
    exec_out = subprocess.run(
        [".venv/bin/python", "-c",
         out.stdout + "\nprint('VOL:', result.volume)"],
        capture_output=True, text=True, check=False,
    )
    assert exec_out.returncode == 0, (
        f"mirror-in-builder exec failed (regression of PR #114):\n"
        f"{exec_out.stderr[-300:]}"
    )
    for line in exec_out.stdout.splitlines():
        if line.startswith("VOL:"):
            vol = float(line.split()[1])
            assert abs(vol - 3488.0) < 1e-6, (
                f"mirror builder volume {vol} != expected 3488.0"
            )
            break
    else:
        pytest.fail("VOL: line not found in exec output")


# ---------------------------------------------------------------------------
# #76 — FreeCAD Labels should drive variable names + module docstring
# ---------------------------------------------------------------------------


def test_renamed_sketch_uses_label_as_variable_name() -> None:
    """The hex cap screw fixture renamed its hexagon sketch to ``Hexagon`` —
    the emit should reflect that, not call it ``sketch_001``."""
    source = _translate(
        "tests/fixtures/tier3_corpus/ANSI-ASME-B18_2_1_Hex_Head_Cap_Screw_1_4-20x1.FCStd"
    )
    # The default-named ``sketch_001`` should NOT appear; the Label-derived
    # name ``hexagon`` should.
    assert "sketch_001" not in source, (
        "emit still uses FreeCAD's autogenerated Name `sketch_001`; "
        "expected the user's Label `Hexagon`"
    )
    assert "hexagon" in source, (
        "emit doesn't carry the user-set Label `Hexagon` as a variable name"
    )


# ---------------------------------------------------------------------------
# #43 — solver-noise coordinates should be snapped
# ---------------------------------------------------------------------------


def test_partdesign_example_no_solver_noise_digits() -> None:
    """``partdesign_example`` contains user-typed ``55`` and ``270°`` that
    the FreeCAD constraint solver settled at ``54.9999786…`` and
    ``270.00003…``. The emit should snap these to the typed values, with
    geometry preserved via coherent recomputation of the arc's sweep
    extent and the adjacent Line endpoint (see :mod:`sketch_snap`).

    The arc's *sweep extent* itself (``261.78…``) is a geometrically-derived
    value, not user-typed — it stays irrational. Same for the Polyline's
    first point (the arc-end coord), which is computed and not snappable.
    """
    source = _translate("tests/fixtures/tier2_partdesign/partdesign_example.FCStd")
    # The user-typed anchor values must be snapped.
    bad_patterns = ["54.99997866", "270.0000349"]
    for pat in bad_patterns:
        assert pat not in source, (
            f"emit still contains solver-noise literal {pat!r}; expected snap to round value"
        )
    # And the snapped values must actually appear.
    assert "center=(55, 15)" in source, "expected center=(55, 15) after snap"
    assert "start_angle=270" in source, "expected start_angle=270 after snap"


# ---------------------------------------------------------------------------
# #94 — atomic Pocket Type='UpToFirst' should resolve to a finite extrude
# ---------------------------------------------------------------------------
#
# Closing this issue means the in-Body UpToFirst resolution
# (BaseFeature.Shape.Volume - Pocket.Shape.Volume / sketch_area) is
# extended to the atomic-Pocket case, where the previous solid in
# document order plays the role of BaseFeature.
#
# The seed-2026 fixture 3pin-female-2_54mm-connector contains the only
# atomic UpToFirst Pocket in the corpus (Pocket002 / 'bottom-pins-cutout').
# The fixture has downstream Part::FeaturePython Clone blockers (out of
# scope per SPEC §13.5) so the *full* fixture stays excluded — but we
# can verify the UpToFirst gap is closed by translating that specific
# pocket in isolation.


def test_atomic_pocket_uptofirst_resolves_to_finite_extrude() -> None:
    """The 3pin-connector's atomic Pocket002 (Type='UpToFirst', no Body)
    should translate to a ``previous_solid - extrude(..., amount=-N)``
    construction with N derived from the volume delta."""
    import os
    import sys
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_root = str(__import__("pathlib").Path(__file__).parent.parent / "src")

    snippet = (
        "import FreeCAD;"
        "from fcstd2b123d.partdesign import _translate_atomic_pocket;"
        "from fcstd2b123d.context import TranslationContext;"
        "from pathlib import Path;"
        "ctx = TranslationContext(source_path=Path('/tmp/x'));"
        "doc = FreeCAD.openDocument("
        "'tests/fixtures/sample_2026/3pin-female-2_54mm-connector.FCStd', hidden=True);"
        "target = doc.getObject('Pocket002');"
        "units = _translate_atomic_pocket(target, ctx);"
        "print(units[0].lines[0])"
    )
    out = subprocess.run(
        [fc_py, "-c", snippet],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": ":".join(p for p in (src_root, fc_pp) if p)},
    )
    assert out.returncode == 0, (
        f"atomic-UpToFirst translator raised:\n{out.stderr}"
    )
    line = out.stdout.strip()
    assert "extrude(" in line, f"expected extrude(...) call; got: {line!r}"
    assert "amount=-" in line, (
        f"expected negative extrude amount (carve direction); got: {line!r}"
    )
    # The previous solid (Pocket001) must be the base of the subtraction.
    assert "Pocket001 -" in line or "pocket_001 -" in line.lower(), (
        f"expected previous-solid (Pocket001) as base; got: {line!r}"
    )


# ---------------------------------------------------------------------------
# #92 / #97 — Part::Chamfer / Part::Fillet (Part workbench dressup)
# ---------------------------------------------------------------------------
#
# Closing these issues means top-level ``Part::Chamfer`` and ``Part::Fillet``
# (distinct from ``PartDesign::Chamfer`` / ``PartDesign::Fillet`` which are
# Body features) translate to build123d ``chamfer(...)`` / ``fillet(...)``
# calls on the parent's shape variable.
#
# The available corpus fixtures with these features (SM-S4303R-2-arms-small-horn,
# 2x5-pin-box-header) have *additional* downstream blockers (edge selection
# drift after Mirror/MultiFuse, or Part::FeaturePython Clone) that keep the
# full fixtures excluded. The translator itself is verified by running the
# handler in isolation on one fixture's Chamfer and asserting reasonable
# output — same pattern as the #94 atomic-Pocket UpToFirst test.


def test_part_chamfer_emits_chamfer_call() -> None:
    """SM-S4303R's first Part::Chamfer should translate to a
    ``chamfer(_edges_at(...), length=...)`` line."""
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_root = str(__import__("pathlib").Path(__file__).parent.parent / "src")
    snippet = (
        "import FreeCAD;"
        "from fcstd2b123d.partdesign import _translate_part_chamfer;"
        "from fcstd2b123d.context import TranslationContext;"
        "from pathlib import Path;"
        "ctx = TranslationContext(source_path=Path('/tmp/x'));"
        "doc = FreeCAD.openDocument("
        "'tests/fixtures/sample_813/SM-S4303R-2-arms-small-horn.FCStd', hidden=True);"
        "target = doc.getObject('Chamfer');"
        "print(_translate_part_chamfer(target, ctx)[0].lines[0])"
    )
    out = subprocess.run(
        [fc_py, "-c", snippet], capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": ":".join(p for p in (src_root, fc_pp) if p)},
    )
    assert out.returncode == 0, f"Part::Chamfer translator raised:\n{out.stderr}"
    line = out.stdout.strip()
    assert "chamfer(_edges_at(" in line, (
        f"expected chamfer(_edges_at(...)) call; got: {line!r}"
    )
    assert "length=" in line, (
        f"expected length= keyword on chamfer; got: {line!r}"
    )


def test_part_fillet_emits_fillet_call() -> None:
    """SM-S4303R's Part::Fillet should translate to a
    ``fillet(_edges_at(...), radius=...)`` line."""
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_root = str(__import__("pathlib").Path(__file__).parent.parent / "src")
    snippet = (
        "import FreeCAD;"
        "from fcstd2b123d.partdesign import _translate_part_fillet;"
        "from fcstd2b123d.context import TranslationContext;"
        "from pathlib import Path;"
        "ctx = TranslationContext(source_path=Path('/tmp/x'));"
        "doc = FreeCAD.openDocument("
        "'tests/fixtures/sample_813/SM-S4303R-2-arms-small-horn.FCStd', hidden=True);"
        "target = doc.getObject('Fillet');"
        "print(_translate_part_fillet(target, ctx)[0].lines[0])"
    )
    out = subprocess.run(
        [fc_py, "-c", snippet], capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": ":".join(p for p in (src_root, fc_pp) if p)},
    )
    assert out.returncode == 0, f"Part::Fillet translator raised:\n{out.stderr}"
    line = out.stdout.strip()
    assert "fillet(_edges_at(" in line, (
        f"expected fillet(_edges_at(...)) call; got: {line!r}"
    )
    assert "radius=" in line, (
        f"expected radius= keyword on fillet; got: {line!r}"
    )


# ---------------------------------------------------------------------------
# #77 — shared-helpers CLI flag should switch from inlining to importing
# ---------------------------------------------------------------------------


def test_shared_helpers_flag_emits_import_not_inline() -> None:
    """With ``--shared-helpers``, the emit should ``from fcstd2b123d.runtime
    import _edges_at, ...`` rather than inlining the helper definitions."""
    import os
    import subprocess

    env = {**os.environ, "PYTHONPATH":
           os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "") + ":"
           + str((__import__('pathlib').Path(__file__).parent.parent / "src"))}
    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    out = subprocess.run(
        [
            fc_py, "-m", "fcstd2b123d", "--shared-helpers",
            "tests/fixtures/tier3_corpus/ANSI-ASME-B18_2_1_Hex_Head_Cap_Screw_1_4-20x1.FCStd",
        ],
        capture_output=True, text=True, env=env, check=False,
    )
    assert out.returncode == 0, f"--shared-helpers translation failed:\n{out.stderr}"
    source = out.stdout
    assert "from fcstd2b123d.runtime import" in source, (
        "expected `from fcstd2b123d.runtime import ...` line"
    )
    # The inlined `def _edges_at` block should NOT appear when sharing.
    assert "def _edges_at(" not in source, (
        "emit still inlines _edges_at — flag had no effect"
    )


# ---------------------------------------------------------------------------
# #78 — builder-mode CLI flag should produce `with BuildSketch()` output
# ---------------------------------------------------------------------------
#
# Phase 1: sketches only — the ``--style=builder`` flag dispatches sketch
# translation through ``_translate_sketch_builder``, producing
# ``with BuildSketch(plane) as <var>: ...`` blocks followed by
# ``<var> = <var>.sketch`` so downstream Pad/Pocket/etc. still reference
# the variable name unchanged. Bodies remain algebra-style for now —
# phase 2 (BuildPart wrapping) is a separate piece of work.


def test_builder_mode_uses_with_buildsketch() -> None:
    """With ``--style=builder``, every sketch should emit a
    ``with BuildSketch(...) as <var>:`` block (phase 1)."""
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    env = {**os.environ, "PYTHONPATH":
           os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "") + ":"
           + str((__import__('pathlib').Path(__file__).parent.parent / "src"))}
    out = subprocess.run(
        [
            fc_py, "-m", "fcstd2b123d", "--style", "builder",
            "tests/fixtures/tier2_partdesign/simple_pad.FCStd",
        ],
        capture_output=True, text=True, env=env, check=False,
    )
    assert out.returncode == 0, f"--style=builder translation failed:\n{out.stderr}"
    source = out.stdout
    assert "with BuildSketch(" in source, (
        "expected `with BuildSketch(...)` block in builder-mode emit"
    )
    assert "BuildSketch" in source, "BuildSketch should be in the imports"
    # Rebinding pattern lets the rest of the body chain stay algebra-style
    # without renaming the variable.
    assert ".sketch" in source, (
        "expected `<var> = <var>.sketch` rebind so downstream extrude / "
        "revolve calls can use the sketch variable unchanged"
    )


def test_builder_and_algebra_emit_same_geometry() -> None:
    """Both ``--style=algebra`` (default) and ``--style=builder`` must
    produce shapes with the same volume on a representative slice of
    tier-2 fixtures (the corpus suite already gates the full set)."""
    import os
    import subprocess

    fc_py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fc_py:
        pytest.skip("FCSTD2B123D_FREECAD_PYTHON not set")
    env = {**os.environ, "PYTHONPATH":
           os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "") + ":"
           + str((__import__('pathlib').Path(__file__).parent.parent / "src"))}

    fixtures = [
        "tests/fixtures/tier2_partdesign/simple_pad.FCStd",
        "tests/fixtures/tier2_partdesign/pad_with_hole.FCStd",
        "tests/fixtures/tier2_partdesign/pad_with_ellipse.FCStd",
        # Pattern fixtures — exposed a correctness bug in #78 phase 2a
        # where the BuildPart-wrapped body had a side effect on
        # ``extrude(...)`` calls inside ``add(Locations * extrude(...), ...)``
        # expressions, producing 4/6 (polar) and 3/4 (linear) carves
        # instead of the correct counts. Now these bodies bail to algebra
        # mode for correctness — these tests gate that.
        "tests/fixtures/tier4_patterns/polar_pattern_holes.FCStd",
        "tests/fixtures/tier4_patterns/linear_pattern_holes.FCStd",
    ]
    for fx in fixtures:
        vols: dict[str, float] = {}
        for style in ("algebra", "builder"):
            translate = subprocess.run(
                [fc_py, "-m", "fcstd2b123d", "--style", style, fx],
                capture_output=True, text=True, env=env, check=False,
            )
            assert translate.returncode == 0, (
                f"{style} translate failed on {fx}:\n{translate.stderr}"
            )
            exec_out = subprocess.run(
                [".venv/bin/python", "-c",
                 translate.stdout + "\nprint('VOL:', result.volume)"],
                capture_output=True, text=True, check=False,
            )
            assert exec_out.returncode == 0, (
                f"{style} exec failed on {fx}:\n{exec_out.stderr}"
            )
            for line in exec_out.stdout.splitlines():
                if line.startswith("VOL:"):
                    vols[style] = float(line.split()[1])
                    break
        rel_err = abs(vols["algebra"] - vols["builder"]) / max(
            abs(vols["algebra"]), 1e-9
        )
        assert rel_err < 1e-6, (
            f"{fx}: algebra={vols['algebra']} builder={vols['builder']} "
            f"(rel.err={rel_err})"
        )
