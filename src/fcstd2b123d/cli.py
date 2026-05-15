"""CLI: fcstd2b123d input.FCStd [-o output.py].

Prints to stdout when -o is omitted, so the output can be piped or captured.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .translator import translate


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
    args = parser.parse_args(argv)

    source = translate(args.input)
    if args.output is None:
        sys.stdout.write(source)
    else:
        args.output.write_text(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
