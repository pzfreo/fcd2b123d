"""Introspect a .FCStd file and summarize its parametric content.

Runs in the FreeCAD env. Used once to pick which bundled examples (and which
Parts-Library files) make good test fixtures, per the project's tier plan.

Output: one JSON record per file, plus a human-readable summary table when run
with --table.

Usage:
    PYTHONPATH=$CONDA_PREFIX/lib python tools/analyze_fcstd.py FILE [FILE ...] [--table]
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

import FreeCAD


# TypeIds we explicitly support per the tier plan. Expanded after analysing
# the FreeCAD Parts Library — see SPEC.md §12.6.
TIER_TYPEIDS = {
    1: {  # primitives (Part workbench + PartDesign primitives)
        "Part::Box", "Part::Cylinder", "Part::Sphere", "Part::Cone", "Part::Torus",
        "Part::Plane", "Part::Wedge", "Part::Prism", "Part::Ellipsoid", "Part::Helix",
        "PartDesign::AdditiveBox", "PartDesign::AdditiveCylinder",
        "PartDesign::AdditiveSphere", "PartDesign::AdditiveCone",
        "PartDesign::AdditiveTorus", "PartDesign::AdditiveEllipsoid",
        "PartDesign::AdditivePrism", "PartDesign::AdditiveWedge",
    },
    2: {  # core PartDesign + Sketcher + Part-workbench feature ops
        "PartDesign::Body", "Sketcher::SketchObject",
        "PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Revolution",
        "PartDesign::Groove", "PartDesign::Hole",
        "PartDesign::Plane", "PartDesign::Line", "PartDesign::Point",
        "PartDesign::ShapeBinder", "PartDesign::SubShapeBinder",
        # Sweep / loft / helix as feature operations
        "PartDesign::AdditivePipe", "PartDesign::SubtractivePipe",
        "PartDesign::AdditiveLoft", "PartDesign::SubtractiveLoft",
        "PartDesign::AdditiveHelix", "PartDesign::SubtractiveHelix",
        # Part workbench equivalents
        "Part::Extrusion", "Part::Revolution", "Part::Loft", "Part::Sweep",
        "Part::Mirroring", "Part::Compound",
    },
    3: {  # dress-up features
        "PartDesign::Fillet", "PartDesign::Chamfer", "PartDesign::Draft",
        "PartDesign::Thickness",
        "Part::Fillet", "Part::Chamfer", "Part::Thickness", "Part::Offset",
    },
    4: {  # patterns
        "PartDesign::LinearPattern", "PartDesign::PolarPattern", "PartDesign::Mirrored",
        "PartDesign::MultiTransform",
    },
    5: {  # booleans + generic imported shapes
        "Part::Cut", "Part::Fuse", "Part::Common", "Part::MultiFuse", "Part::MultiCommon",
        "PartDesign::Boolean",
        # Files with bare Part::Feature objects have no parametric history.
        # The translator handles these via the shape-import path (STEP round
        # trip) — geometry survives, parametric editability is lost.
        "Part::Feature",
    },
    6: {  # parametric drivers
        "Spreadsheet::Sheet", "App::VarSet",
    },
}

# Structural infrastructure that appears in valid documents but isn't an
# operation — never blocks scope.
INFRASTRUCTURE_TYPES = {
    "App::Origin", "App::Line", "App::Plane", "App::Part",
    "App::DocumentObjectGroup", "App::DocumentObjectGroupPython",
    "App::GeoFeatureGroupExtensionPython",
    "App::TextDocument", "App::Annotation", "App::GeometryPython",
    "App::MaterialObjectPython",
    "PartDesign::CoordinateSystem",
    "PartDesign::FeatureBase",
    "Measure::MeasureDistanceDetached",
    "Image::ImagePlane",
}

# Generic Python-extension wrappers — appear in many real files (gear
# generators, fasteners-library parts). Flagged but not auto-rejecting; we'd
# need to look at each one's wrapped behaviour.
EXTENSION_TYPES = {
    "Part::FeaturePython", "App::FeaturePython", "Part::Part2DObjectPython",
    "PartDesign::FeaturePython",
}

# Workbenches that are explicitly out of scope per SPEC.md.
OUT_OF_SCOPE_PREFIXES = (
    "Assembly::",       # Assembly workbench
    "Fem::",            # FEM
    "Arch::",           # Arch (incl. BIM)
    "Draft::",          # Draft (2D)
    "Path::",           # CAM/Path
    "TechDraw::",       # Drawing
    "Sheetmetal::",     # Sheet Metal
    "Surface::",        # Surface
    "Mesh::",           # Mesh workbench (different from BRep)
)

# Out-of-scope individual types that don't share a clean prefix.
OUT_OF_SCOPE_TYPES = {
    # Cross-document linking is assembly-adjacent; defer with Assembly.
    "App::Link", "App::LinkElement", "App::LinkGroup",
}


def _tier_of(typeid: str) -> int | None:
    for tier, ids in TIER_TYPEIDS.items():
        if typeid in ids:
            return tier
    return None


def analyze(path: Path) -> dict:
    try:
        doc = FreeCAD.openDocument(str(path))
    except Exception as exc:
        return {"file": str(path), "error": f"open failed: {exc}"}

    try:
        type_counts: Counter = Counter()
        out_of_scope: Counter = Counter()
        unknown: Counter = Counter()
        constraint_count = 0
        expression_count = 0
        spreadsheet_aliases = 0
        bodies = 0

        for obj in doc.Objects:
            tid = obj.TypeId
            type_counts[tid] += 1

            if tid == "PartDesign::Body":
                bodies += 1

            if tid == "Sketcher::SketchObject":
                try:
                    constraint_count += len(obj.Constraints)
                except Exception:
                    pass

            if tid == "Spreadsheet::Sheet":
                try:
                    # Cell properties prefixed with "alias_" indicate aliased cells.
                    for prop in obj.PropertiesList:
                        if prop.startswith("alias"):
                            spreadsheet_aliases += 1
                except Exception:
                    pass

            try:
                expression_count += len(obj.ExpressionEngine)
            except Exception:
                pass

            tier = _tier_of(tid)
            if tier is None:
                if (
                    any(tid.startswith(p) for p in OUT_OF_SCOPE_PREFIXES)
                    or tid in OUT_OF_SCOPE_TYPES
                ):
                    out_of_scope[tid] += 1
                elif tid in INFRASTRUCTURE_TYPES or tid in EXTENSION_TYPES:
                    pass  # not blocking
                else:
                    unknown[tid] += 1

        extensions = Counter({t: c for t, c in type_counts.items() if t in EXTENSION_TYPES})

        tiers_present = sorted({_tier_of(t) for t in type_counts if _tier_of(t) is not None})
        max_tier_required = max(tiers_present) if tiers_present else 0

        # In scope if: no explicit out-of-scope workbenches, no unknown types,
        # and there's at least one operation we recognise. Extension types
        # (FeaturePython) downgrade to "needs investigation" but don't reject.
        in_scope = (
            len(out_of_scope) == 0
            and len(unknown) == 0
            and max_tier_required > 0
        )
        needs_investigation = in_scope and len(extensions) > 0

        return {
            "file": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "object_count": len(doc.Objects),
            "bodies": bodies,
            "tiers_present": tiers_present,
            "max_tier_required": max_tier_required,
            "in_scope": in_scope,
            "needs_investigation": needs_investigation,
            "type_counts": dict(type_counts),
            "out_of_scope_types": dict(out_of_scope),
            "unknown_types": dict(unknown),
            "extension_types": dict(extensions),
            "sketch_constraint_count": constraint_count,
            "expression_count": expression_count,
            "spreadsheet_aliases": spreadsheet_aliases,
        }
    finally:
        FreeCAD.closeDocument(doc.Name)


def _print_table(records: list[dict]) -> None:
    in_scope = [r for r in records if r.get("in_scope")]
    out_of_scope = [r for r in records if not r.get("in_scope") and "error" not in r]
    errors = [r for r in records if "error" in r]

    print(f"\n{'IN SCOPE':<45} {'tier':>4} {'objs':>5} {'sketch':>6} {'expr':>5}")
    print("-" * 75)
    for r in sorted(in_scope, key=lambda x: (x["max_tier_required"], x["object_count"])):
        print(
            f"  {r['name']:<43} {r['max_tier_required']:>4} {r['object_count']:>5} "
            f"{r['sketch_constraint_count']:>6} {r['expression_count']:>5}"
        )

    print(f"\n{'OUT OF SCOPE':<45} reason")
    print("-" * 75)
    for r in sorted(out_of_scope, key=lambda x: x["name"]):
        oos = list(r["out_of_scope_types"].keys())
        unk = list(r["unknown_types"].keys())
        reason = ", ".join(oos[:3] + unk[:3])
        if len(oos) + len(unk) > 6:
            reason += " ..."
        print(f"  {r['name']:<43} {reason}")

    if errors:
        print(f"\nERRORS:")
        for r in errors:
            print(f"  {r['file']}: {r['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="Files to analyse (or use --input-list)")
    parser.add_argument("--input-list", type=Path, default=None,
                        help="Read newline-separated file paths from this file (handles spaces).")
    parser.add_argument("--table", action="store_true", help="Print human-readable table")
    parser.add_argument("--json", type=Path, default=None, help="Write JSON records to this path")
    args = parser.parse_args()

    files = list(args.files)
    if args.input_list:
        files.extend(Path(line) for line in args.input_list.read_text().splitlines() if line.strip())
    if not files:
        parser.error("Provide files as positional args or via --input-list.")

    records = []
    for i, f in enumerate(files, 1):
        try:
            records.append(analyze(f))
        except Exception:
            records.append({"file": str(f), "error": traceback.format_exc(limit=2)})
        if i % 100 == 0:
            print(f"  [{i}/{len(files)}] ...", flush=True)

    if args.json:
        args.json.write_text(json.dumps(records, indent=2) + "\n")
        print(f"Wrote {args.json}")

    if args.table or not args.json:
        _print_table(records)


if __name__ == "__main__":
    main()
