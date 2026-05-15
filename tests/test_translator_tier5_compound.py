"""Tier-5 Part-workbench composition: Part::Compound, Part::Mirroring.

These differ from the boolean ops in that they aggregate or transform
whole shapes rather than fuse / intersect them. Translator emits
``Compound([...])`` and ``mirror(src, about=Plane(...))`` respectively;
properties round-trip through the multi-solid aggregation pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from fcstd2b123d.properties import Properties
from fcstd2b123d.verify import assert_equivalent, extract_build123d

FIXTURE_DIR = Path("tests/fixtures/tier5_compound")
FIXTURES = sorted(FIXTURE_DIR.glob("*.FCStd"))


@pytest.mark.parametrize("fcstd_path", FIXTURES, ids=lambda p: p.stem)
def test_tier5_compound(fcstd_path: Path):
    source = _translate(fcstd_path)

    namespace: dict = {}
    try:
        exec(source, namespace)
    except Exception as exc:
        pytest.fail(f"Generated source failed to execute: {exc}\n\nSource:\n{source}")

    assert "result" in namespace, (
        "Translator must bind the final shape to `result`.\n\nSource:\n" + source
    )

    part = namespace["result"]
    actual = extract_build123d(part)
    expected = Properties.from_file(fcstd_path.with_suffix(".expected.json"))
    assert_equivalent(
        actual, expected,
        actual_part=part,
        pointcloud_path=fcstd_path.with_suffix(".pointcloud.json"),
    )
