"""End-to-end translator tests for tier-3 fixtures (fillet, chamfer).

Same pattern as tier-1 and tier-2: translate in a FreeCAD subprocess, exec
the emitted Python in the test process, compare extracted properties to the
committed snapshot.

The crucial validation: FreeCAD references edges by index ('Edge8'), which
is only meaningful in FreeCAD's evaluated BRep. The translator emits a
build123d edge filter based on the edge's geometric midpoint (read out of
FreeCAD at translation time). If that selection matches the right edges,
ADR-0001's "use FreeCAD-runtime" decision is validated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from fcstd2b123d.verify import assert_equivalent, extract_build123d
from fcstd2b123d.properties import Properties

FIXTURE_DIR = Path("tests/fixtures/tier3_filletchamfer")
FIXTURES = sorted(FIXTURE_DIR.glob("*.FCStd"))


@pytest.mark.parametrize("fcstd_path", FIXTURES, ids=lambda p: p.stem)
def test_tier3_translation(fcstd_path: Path):
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
