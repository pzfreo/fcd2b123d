"""CLI: fcstd2b123d-verify translated.py expected.json [pointcloud.json].

Runs in the build123d-enabled Python environment. Loads the translated
build123d source, exec's it (the module is expected to bind ``result`` to
the final shape), extracts geometric properties, and compares them to the
FreeCAD-side snapshot.

Exits 0 on PASS, 1 on FAIL. Stderr carries a human-friendly summary; stdout
stays clean so the CLI is pipeline-friendly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .properties import Properties
from .verify import assert_equivalent, extract_build123d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fcstd2b123d-verify",
        description="Confirm a translated build123d .py reproduces the "
                    "FreeCAD source's geometric properties.",
    )
    parser.add_argument(
        "source", type=Path,
        help="Translated build123d .py file. Must bind `result` to the shape.",
    )
    parser.add_argument(
        "expected", type=Path,
        help="FreeCAD-side properties snapshot (<input>.expected.json).",
    )
    parser.add_argument(
        "pointcloud", type=Path, nargs="?", default=None,
        help="Optional FreeCAD-side point cloud for Hausdorff backstop "
             "(<input>.pointcloud.json). Defaults to a sibling of `expected` "
             "with .pointcloud.json suffix when present.",
    )
    args = parser.parse_args(argv)

    pointcloud_path = args.pointcloud
    if pointcloud_path is None:
        candidate = args.expected.with_suffix("").with_suffix(".pointcloud.json")
        pointcloud_path = candidate if candidate.exists() else None

    source = args.source.read_text()
    namespace: dict = {}
    try:
        exec(compile(source, str(args.source), "exec"), namespace)
    except Exception as exc:
        sys.stderr.write(f"FAIL: generated source raised {type(exc).__name__}: {exc}\n")
        return 1

    if "result" not in namespace:
        sys.stderr.write(
            "FAIL: translated source did not bind `result`. The translator "
            "always emits `result = <final>`; was this file modified?\n"
        )
        return 1

    actual = extract_build123d(namespace["result"])
    expected = Properties.from_file(args.expected)

    try:
        assert_equivalent(
            actual, expected,
            actual_part=namespace["result"],
            pointcloud_path=pointcloud_path,
        )
    except AssertionError as exc:
        sys.stderr.write(f"FAIL:\n{exc}\n")
        return 1

    sys.stderr.write(
        f"PASS: {args.source.name} matches {args.expected.name}\n"
        f"  volume       = {actual.volume:.6g} (FreeCAD: {expected.volume:.6g})\n"
        f"  surface_area = {actual.surface_area:.6g} (FreeCAD: {expected.surface_area:.6g})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
