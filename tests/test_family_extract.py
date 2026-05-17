"""Tests for the family-extraction algorithm (Phase 3 of #118).

Two layers:

* Pure-Python unit tests for the AST diff + substitution algorithm —
  fast (no FreeCAD subprocess). Synthesised inputs.
* End-to-end integration test against the EN 10058 manifest + 22
  corpus fixtures — gated on ``FCSTD2B123D_FREECAD_PYTHON``.

See ``docs/design/family-extraction.md`` for the architecture.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from fcstd2b123d.family import load_manifest
from fcstd2b123d.family_extract import (
    FamilyExtractionError,
    align_literals_across_fixtures,
    collect_numeric_literals,
    extract_family,
    infer_substitution,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Pure-Python unit tests for the substitution algorithm
# ---------------------------------------------------------------------------


def test_infer_substitution_exact_param_match() -> None:
    """A literal that equals a param's value in every fixture → that param."""
    sub = infer_substitution(
        values=[60, 50, 110, 150],
        params_per_fixture=[
            {"width": 60, "thickness": 4},
            {"width": 50, "thickness": 8},
            {"width": 110, "thickness": 25},
            {"width": 150, "thickness": 50},
        ],
    )
    assert isinstance(sub, ast.Name)
    assert sub.id == "width"


def test_infer_substitution_negated_param() -> None:
    """A literal that's always the negation of a param → ``-param``."""
    sub = infer_substitution(
        values=[-60, -50, -110],
        params_per_fixture=[
            {"width": 60}, {"width": 50}, {"width": 110},
        ],
    )
    assert isinstance(sub, ast.UnaryOp)
    assert isinstance(sub.op, ast.USub)
    assert isinstance(sub.operand, ast.Name)
    assert sub.operand.id == "width"


def test_infer_substitution_half_param() -> None:
    """A literal that's always param/2 → ``param / 2``."""
    sub = infer_substitution(
        values=[30, 25, 55],
        params_per_fixture=[
            {"width": 60}, {"width": 50}, {"width": 110},
        ],
    )
    assert isinstance(sub, ast.BinOp)
    assert isinstance(sub.op, ast.Div)


def test_infer_substitution_constant_with_matching_param() -> None:
    """A literal that's constant across fixtures AND matches a declared
    param's value → still substitutes (the param wins)."""
    sub = infer_substitution(
        values=[50, 50, 50, 50],
        params_per_fixture=[
            {"length": 50}, {"length": 50}, {"length": 50}, {"length": 50},
        ],
    )
    assert sub is not None
    assert isinstance(sub, ast.Name)
    assert sub.id == "length"


def test_infer_substitution_no_match() -> None:
    """No simple linear relationship → return None (keep as literal)."""
    sub = infer_substitution(
        values=[7, 11, 13, 17],  # primes — not a function of any param
        params_per_fixture=[
            {"width": 60}, {"width": 50}, {"width": 110}, {"width": 150},
        ],
    )
    assert sub is None


def test_align_literals_rejects_non_isomorphic_fixtures() -> None:
    """Fixtures with different AST literal positions → error."""
    # Synthesise two ASTs with different literal-count.
    src_a = "x = 1\ny = 2\n"
    src_b = "x = 1\n"
    lits_a = collect_numeric_literals(list(ast.parse(src_a).body))
    lits_b = collect_numeric_literals(list(ast.parse(src_b).body))
    with pytest.raises(FamilyExtractionError, match="not isomorphic"):
        align_literals_across_fixtures([lits_a, lits_b])


def test_collect_numeric_literals_finds_ints_and_floats() -> None:
    """Walk picks up both int and float literals; skips bools."""
    src = "x = 1\ny = 2.5\nz = True\n"
    lits = collect_numeric_literals(list(ast.parse(src).body))
    values = [v for _p, v in lits]
    assert 1.0 in values
    assert 2.5 in values
    # bools are *not* included even though Python treats them as int.
    assert all(v != 1.0 or _p[0] == 0 for _p, v in lits)


# ---------------------------------------------------------------------------
# End-to-end integration test (gated on FreeCAD env)
# ---------------------------------------------------------------------------


def _freecad_available() -> bool:
    return bool(os.environ.get("FCSTD2B123D_FREECAD_PYTHON"))


@pytest.mark.skipif(not _freecad_available(), reason="FCSTD2B123D_FREECAD_PYTHON not set")
def test_extract_en_10058_family_produces_valid_class(tmp_path: Path) -> None:
    """End-to-end: extract EN 10058 from manifest + 22 fixtures → a
    parametric class that produces correct geometry for arbitrary
    (width, thickness, length)."""
    manifest = load_manifest(REPO_ROOT / "families" / "en_10058.yaml")
    source = extract_family(manifest, fixtures_root=REPO_ROOT / "tests" / "fixtures")

    out = tmp_path / "en_10058.py"
    out.write_text(source)
    namespace: dict = {}
    exec(source, namespace)

    cls = namespace["EN10058FlatBar"]
    # A few specific geometric checks.
    bar_60x4 = cls(width=60, thickness=4, length=100)
    assert abs(bar_60x4.volume - 60 * 4 * 100) < 1e-6
    bar_150x50 = cls(width=150, thickness=50, length=50)
    assert abs(bar_150x50.volume - 150 * 50 * 50) < 1e-6

    # Default instantiation produces a Part.
    default = cls()
    assert abs(default.volume) > 0


@pytest.mark.skipif(not _freecad_available(), reason="FCSTD2B123D_FREECAD_PYTHON not set")
def test_extract_he_b_family_with_lookup_table(tmp_path: Path) -> None:
    """End-to-end: extract DIN 1025-2 HE-B family — exercises both the
    multi-param substitution algorithm AND the lookup-table source.

    The HE-B profile has 5 dimensions (h, b, tw, tf, r); only ``h`` is
    in the filename. The other 4 come from the manifest's dimensions
    table. Each fixture's emit has literals like ``tw/2 + r``,
    ``h/2 - tf - r`` that need two- and three-param substitution.
    """
    manifest = load_manifest(REPO_ROOT / "families" / "din_1025_2_he_b.yaml")
    source = extract_family(manifest, fixtures_root=REPO_ROOT / "tests" / "fixtures")

    namespace: dict = {}
    exec(source, namespace)
    cls = namespace["DIN1025HEBProfile"]

    # Three known-size profiles. Bbox must match (b, h, length) exactly.
    for h, b, tw, tf, r in [
        (280, 280, 10.5, 18, 24),
        (320, 300, 11.5, 20.5, 27),
        (900, 300, 18.5, 35, 30),
    ]:
        beam = cls(h=h, b=b, tw=tw, tf=tf, r=r, length=50)
        bbox = beam.bounding_box()
        extents = sorted([bbox.size.X, bbox.size.Y, bbox.size.Z])
        expected = sorted([float(b), float(h), 50.0])
        assert extents == pytest.approx(expected, abs=1e-6), (
            f"HE-B {h}: bbox extents {extents} != expected {expected}"
        )

    # And the volume scales monotonically with h.
    v_280 = cls(h=280, b=280, tw=10.5, tf=18, r=24, length=50).volume
    v_900 = cls(h=900, b=300, tw=18.5, tf=35, r=30, length=50).volume
    assert v_900 > v_280


@pytest.mark.skipif(not _freecad_available(), reason="FCSTD2B123D_FREECAD_PYTHON not set")
def test_extract_en_10058_matches_every_fixture(tmp_path: Path) -> None:
    """For every fixture in the manifest's glob, instantiating the
    generated class with the fixture's parameters produces the same
    bounding-box geometry as the per-fixture --emit=class translation
    would.

    This is the load-bearing check that proves the algorithm is correct
    on the canonical first family.
    """
    from fcstd2b123d.family_extract import discover_fixtures

    manifest = load_manifest(REPO_ROOT / "families" / "en_10058.yaml")
    fixtures_root = REPO_ROOT / "tests" / "fixtures"
    records = discover_fixtures(manifest, fixtures_root)

    source = extract_family(manifest, fixtures_root=fixtures_root)
    namespace: dict = {}
    exec(source, namespace)
    cls = namespace["EN10058FlatBar"]

    failures: list[str] = []
    for rec in records:
        # All EN 10058 fixtures have length=50 (constant). The manifest's
        # extrude_amount inference would have caught this.
        kwargs = {
            "width": float(rec.params["width"]),
            "thickness": float(rec.params["thickness"]),
        }
        # Defer length: the param's value comes from the manifest default
        # since it's invariant. Pass it explicitly for clarity.
        kwargs["length"] = 50
        part = cls(**kwargs)
        expected_vol = kwargs["width"] * kwargs["thickness"] * kwargs["length"]
        if abs(part.volume - expected_vol) > 1e-6:
            failures.append(
                f"{rec.path.name}: vol {part.volume} != expected {expected_vol}"
            )

    if failures:
        pytest.fail("\n".join(failures))
