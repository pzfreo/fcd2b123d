"""Wrap a raw analyze_fcstd.py output into the committed coverage database.

Normalises file paths (strips the working-directory prefix used at analysis
time so the DB makes sense regardless of where the library lives) and adds
provenance metadata: FreeCAD version, Parts Library commit, analyser tier-map
version, generation date.

Usage:
    python tools/build_coverage_database.py \
        --input  /tmp/fc-library-expanded.json \
        --library-root /tmp/fc-library \
        --library-commit 2e36ae3 \
        --freecad-version 1.0.0 \
        --output data/parts-library/coverage.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--library-root", type=Path, required=True,
                   help="Absolute path the analysis used; stripped from each record.")
    p.add_argument("--library-commit", type=str, required=True)
    p.add_argument("--library-source", type=str,
                   default="https://github.com/FreeCAD/FreeCAD-library")
    p.add_argument("--freecad-version", type=str, required=True)
    p.add_argument("--tier-map-version", type=str, default="v2-2026-05-15",
                   help="Identifier of the analyze_fcstd.py tier map used. Bump when tier map changes.")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()

    records = json.loads(args.input.read_text())
    root_prefix = str(args.library_root).rstrip("/") + "/"

    normalised = []
    for r in records:
        n = dict(r)
        if "file" in n and n["file"].startswith(root_prefix):
            n["file"] = n["file"][len(root_prefix):]
        normalised.append(n)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Pretty header + compact one-record-per-line for the files array.
    # Keeps the metadata readable on GitHub while making each file entry
    # greppable on a single line.
    header = {
        "schema_version": 1,
        "generated": date.today().isoformat(),
        "library": {
            "source": args.library_source,
            "commit": args.library_commit,
        },
        "freecad_version": args.freecad_version,
        "tier_map_version": args.tier_map_version,
        "total_files": len(normalised),
    }
    header_lines = json.dumps(header, indent=2).rstrip("}\n").rstrip().rstrip(",") + ","
    body_lines = [json.dumps(r, separators=(",", ":")) for r in normalised]

    with args.output.open("w") as f:
        f.write(header_lines + "\n")
        f.write('  "files": [\n')
        for i, line in enumerate(body_lines):
            comma = "," if i < len(body_lines) - 1 else ""
            f.write(f"    {line}{comma}\n")
        f.write("  ]\n}\n")
    print(f"Wrote {args.output} ({len(normalised)} files)")


if __name__ == "__main__":
    main()
