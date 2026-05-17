"""Tests for the family manifest schema, loader, and validator.

Pure-Python tests — no FreeCAD subprocess required. The Phase 2 work
(#129) ships *just* the schema and validator; Phase 3 (#130) will use
the parsed manifests to generate parametric classes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcstd2b123d.family import (
    FamilyManifest,
    ManifestError,
    ParameterDecl,
    StandardRef,
    load_manifest,
    parse_manifest_text,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Loading + parsing
# ---------------------------------------------------------------------------


def test_load_canonical_en_10058_manifest() -> None:
    """The canonical EN 10058 manifest loads cleanly."""
    manifest = load_manifest(REPO_ROOT / "families" / "en_10058.yaml")
    assert manifest.family == "en_10058_flat_bar"
    assert manifest.class_name == "EN10058FlatBar"
    assert manifest.standard.ref == "EN 10058:2018"
    assert manifest.standard.title is not None
    assert manifest.standard.url is not None
    assert manifest.fixture_glob == "**/Flat_Bar_*_EN10058_S235JR.FCStd"
    assert manifest.filename_pattern is not None
    assert {p.name for p in manifest.parameters} == {"width", "thickness", "length"}
    length_param = next(p for p in manifest.parameters if p.name == "length")
    assert length_param.source == "extrude_amount"
    assert length_param.default == 50


def test_load_minimal_test_manifest() -> None:
    """The synthetic minimal manifest loads cleanly."""
    manifest = load_manifest(
        REPO_ROOT / "tests" / "fixtures" / "manifests" / "minimal.yaml"
    )
    assert manifest.family == "minimal_box"
    assert manifest.parameters[0].name == "size"
    assert manifest.parameters[0].source == "constant"
    assert manifest.parameters[0].default == 10


def test_parse_manifest_text_round_trip() -> None:
    """``parse_manifest_text`` works for inline YAML."""
    text = """
family: test_family
class_name: TestFamily
standard:
  ref: ISO 9999
fixture_glob: "*.FCStd"
parameters:
  - name: width
    source: constant
    default: 1.0
"""
    manifest = parse_manifest_text(text)
    assert manifest.family == "test_family"
    assert manifest.source_path is None  # text-loaded manifests have no source


# ---------------------------------------------------------------------------
# Validation failures — each error path
# ---------------------------------------------------------------------------


def _bad(text: str) -> str:
    """Return the ManifestError message for a malformed manifest."""
    with pytest.raises(ManifestError) as exc_info:
        parse_manifest_text(text)
    return str(exc_info.value)


def test_top_level_must_be_mapping() -> None:
    msg = _bad("- just\n- a\n- list\n")
    assert "mapping" in msg.lower()


def test_missing_family_field() -> None:
    msg = _bad("class_name: Foo\nstandard:\n  ref: X\nfixture_glob: x\nparameters:\n  - name: w\n    source: constant\n    default: 1\n")
    assert "family" in msg


def test_family_must_be_snake_case() -> None:
    msg = _bad("""
family: MyFamily
class_name: Foo
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
""")
    assert "snake_case" in msg


def test_class_name_must_be_pascal_case() -> None:
    msg = _bad("""
family: my_family
class_name: my_class
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
""")
    assert "PascalCase" in msg


def test_missing_standard_field() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
""")
    assert "standard" in msg.lower()


def test_filename_source_without_pattern() -> None:
    """A parameter with ``source: filename`` requires ``filename_pattern``."""
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters:
  - name: width
    source: filename
""")
    assert "filename_pattern" in msg


def test_filename_param_must_match_named_group() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
filename_pattern: "Bar_(?P<width>\\\\d+)"
parameters:
  - name: thickness
    source: filename
""")
    assert "named group" in msg


def test_constant_source_requires_default() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
""")
    assert "default" in msg


def test_unknown_source_type() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: notreal
""")
    assert "notreal" in msg or "source" in msg


def test_unknown_param_type() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
    type: matrix
""")
    assert "matrix" in msg or "type" in msg


def test_duplicate_parameter_name() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
  - name: w
    source: constant
    default: 2
""")
    assert "duplicate" in msg


def test_invalid_filename_pattern_regex() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
filename_pattern: "[unclosed"
parameters:
  - name: w
    source: constant
    default: 1
""")
    assert "regex" in msg


def test_empty_parameters_list() -> None:
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters: []
""")
    assert "parameters" in msg


def test_unknown_top_level_key_rejected() -> None:
    """Typos in top-level keys must surface — they otherwise get silently
    ignored."""
    msg = _bad("""
family: my_family
class_name: MyFamily
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
typo_key: oops
""")
    assert "typo_key" in msg


# ---------------------------------------------------------------------------
# Successful round-trips covering each parameter source
# ---------------------------------------------------------------------------


def test_all_param_sources_load() -> None:
    """Each declared source kind round-trips through validation."""
    text = """
family: all_sources
class_name: AllSources
standard:
  ref: X
fixture_glob: "*.FCStd"
filename_pattern: "Foo_(?P<a>\\\\d+)_(?P<b>\\\\d+)"
parameters:
  - name: a
    source: filename
  - name: b
    source: filename
  - name: c
    source: extrude_amount
  - name: d
    source: spreadsheet
  - name: e
    source: constant
    default: 42
"""
    manifest = parse_manifest_text(text)
    sources = {p.name: p.source for p in manifest.parameters}
    assert sources == {
        "a": "filename", "b": "filename", "c": "extrude_amount",
        "d": "spreadsheet", "e": "constant",
    }


def test_manifest_dataclasses_are_immutable() -> None:
    """FamilyManifest, StandardRef, ParameterDecl are frozen dataclasses."""
    manifest = parse_manifest_text("""
family: test
class_name: Test
standard:
  ref: X
fixture_glob: x
parameters:
  - name: w
    source: constant
    default: 1
""")
    with pytest.raises(Exception):  # FrozenInstanceError or similar
        manifest.family = "other"  # type: ignore[misc]
