"""Corpus validation: 30 random tier-1-to-3 files from the FreeCAD Parts Library.

Each file translates end-to-end and matches FreeCAD's geometry. This is the
"prove the percentage" test — sampled at fixed seed from the eligible pool
(in scope, no FeaturePython extensions, max_tier_required ≤ 3) to catch
regressions and surface unknown edge cases.

Re-sample with: ``python tools/corpus_validate.py --library <path> ...``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_translator_tier1 import _translate
from tests.utils.compare import assert_equivalent, extract_build123d
from tests.utils.properties import Properties

FIXTURE_DIR = Path("tests/fixtures/tier3_corpus")
FIXTURES = sorted(FIXTURE_DIR.glob("*.FCStd"))


@pytest.mark.parametrize("fcstd_path", FIXTURES, ids=lambda p: p.stem)
def test_corpus_fixture(fcstd_path: Path):
    source = _translate(fcstd_path)

    namespace: dict = {}
    try:
        exec(source, namespace)
    except Exception as exc:
        pytest.fail(f"Generated source failed to execute: {exc}\n\nSource:\n{source}")

    assert "result" in namespace, (
        "Translator must bind the final shape to `result`.\n\nSource:\n" + source
    )

    actual = extract_build123d(namespace["result"])
    expected = Properties.from_file(fcstd_path.with_suffix(".expected.json"))
    assert_equivalent(actual, expected)
