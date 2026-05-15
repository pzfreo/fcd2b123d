"""CLI: fcstd2b123d input.FCStd [-o output.py] [--json-out output.features.json].

Without -o, prints the build123d source to stdout. With --json-out, also
writes the structured feature record (SPEC §14).
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
    args = parser.parse_args(argv)

    source, ctx = translate_with_context(args.input)
    if args.output is None:
        sys.stdout.write(source)
    else:
        args.output.write_text(source)

    if args.json_out is not None:
        ctx.write_json(args.json_out, final_code=source)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
