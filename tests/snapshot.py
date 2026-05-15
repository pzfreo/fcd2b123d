"""Snapshot tool: open a .FCStd in FreeCAD, write expected.json + pointcloud.json.

Runs in a FreeCAD-enabled Python environment (conda-forge freecad).
Thin shim around ``fcstd2b123d.snapshot``; the heavy lifting lives in the
package so the translator CLI's ``--verify`` flag can reuse it.

Usage:
    PYTHONPATH=$CONDA_PREFIX/lib python tests/snapshot.py path/to/file.FCStd [output.expected.json]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fcstd2b123d.snapshot import snapshot_fcstd


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", type=Path, help="Path to .FCStd input")
    p.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=None,
        help="Output JSON path (default: <input>.expected.json). The "
             "pointcloud is always written as <input>.pointcloud.json beside "
             "the input.",
    )
    args = p.parse_args()
    expected_path, pointcloud_path, vertices = snapshot_fcstd(
        args.input, expected_path=args.output
    )
    print(
        f"Wrote {expected_path} (+ {vertices}-vertex pointcloud at "
        f"{pointcloud_path})"
    )


if __name__ == "__main__":
    main()
