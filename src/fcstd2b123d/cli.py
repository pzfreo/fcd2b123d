"""CLI: fcstd2b123d input.FCStd [-o output.py] [--json-out output.features.json] [--verify].

Without -o, prints the build123d source to stdout. With --json-out, also
writes the structured feature record (SPEC §14). With --verify, also writes
``<output>.expected.json`` and ``<output>.pointcloud.json`` sidecars from
the FreeCAD shape so the user can run ``fcstd2b123d-verify`` afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .translator import translate_with_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fcstd2b123d",
        description="Translate a FreeCAD .FCStd file into build123d Python.",
    )
    parser.add_argument("input", type=Path, help="Path to input .FCStd")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output .py path. Omit to write to stdout.",
    )
    parser.add_argument(
        "--json-out", type=Path, default=None, dest="json_out",
        help="Optional structured feature record. SPEC §14.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Also emit .expected.json + .pointcloud.json sidecars from the "
             "FreeCAD shape, so you can confirm the translated build123d "
             "Python matches by running `fcstd2b123d-verify`.",
    )
    args = parser.parse_args(argv)

    source, ctx = translate_with_context(args.input)
    if args.output is None:
        sys.stdout.write(source)
    else:
        args.output.write_text(source)

    if args.json_out is not None:
        ctx.write_json(args.json_out, final_code=source)

    if args.verify:
        _emit_verification_sidecars(args.input, args.output)

    return 0


def _emit_verification_sidecars(input_path: Path, output_path: Path | None) -> None:
    """Open the .FCStd, extract the target's properties + point cloud, and
    write them as siblings of the output .py (or the input .FCStd when no
    -o was given).
    """
    from .snapshot import snapshot_fcstd

    base = output_path if output_path is not None else input_path
    expected = base.with_suffix(".expected.json")
    pointcloud = base.with_suffix(".pointcloud.json")
    expected, pointcloud, vertices = snapshot_fcstd(
        input_path, expected_path=expected, pointcloud_path=pointcloud
    )
    sys.stderr.write(
        f"Wrote {expected} and {pointcloud} ({vertices}-vertex point cloud).\n"
    )
    if output_path is not None:
        sys.stderr.write(
            f"Verify with: fcstd2b123d-verify {output_path} {expected}\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
