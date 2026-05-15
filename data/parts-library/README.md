# Parts Library coverage database

`coverage.json` is a snapshot of every `.FCStd` file in the FreeCAD Parts Library, classified by tier and feature, produced by `tools/analyze_fcstd.py` and packaged by `tools/build_coverage_database.py`.

Committed so contributors don't need to clone the ~4 GB Parts Library or re-run the analysis just to ask questions like "which files use a fillet?"

## Provenance

The header records exactly what was analysed:

```json
{
  "schema_version": 1,
  "generated": "2026-05-15",
  "library": {
    "source": "https://github.com/FreeCAD/FreeCAD-library",
    "commit": "2e36ae32ba3b360592de93ab0630b1dfe163c3f3"
  },
  "freecad_version": "1.0.0",
  "tier_map_version": "v2-2026-05-15",
  "total_files": 3194
}
```

Bump `tier_map_version` and regenerate when the tier map in `tools/analyze_fcstd.py` changes — old records may classify differently with a new map.

## Format

Pretty-printed header + one JSON object per file, one per line in the `files` array. This is valid JSON for tools that load the whole file, *and* grep-friendly for one-liners:

```bash
# Files with Spreadsheet aliases
grep -E '"Spreadsheet::Sheet":[1-9]' data/parts-library/coverage.json | head

# Files in tier 3 (need fillets/chamfers)
grep '"max_tier_required":3' data/parts-library/coverage.json | wc -l

# Files using PartDesign::AdditivePipe (sweep)
grep '"PartDesign::AdditivePipe"' data/parts-library/coverage.json | wc -l
```

## Schema (per-file record)

| Field | Type | Meaning |
|---|---|---|
| `file` | string | Relative path inside the Parts Library checkout |
| `name` | string | File basename |
| `size_bytes` | int | FCStd file size |
| `object_count` | int | Total `App::DocumentObject` count |
| `bodies` | int | `PartDesign::Body` count |
| `tiers_present` | list[int] | Tiers represented by the file's operations |
| `max_tier_required` | int | Highest tier present (0 if no recognised ops) |
| `in_scope` | bool | True when no out-of-scope or unknown types present and there's at least one recognised op |
| `needs_investigation` | bool | True when `in_scope` but contains FeaturePython extensions |
| `type_counts` | dict | TypeId → count |
| `out_of_scope_types` | dict | TypeIds in out-of-scope workbenches (TechDraw, Mesh, etc.) |
| `unknown_types` | dict | TypeIds the tier map doesn't recognise (gaps to fix) |
| `extension_types` | dict | `Part::FeaturePython` and friends — community parts needing the shape-import fallback |
| `sketch_constraint_count` | int | Total constraints across all sketches |
| `expression_count` | int | Total expressions across all objects' `ExpressionEngine` |
| `spreadsheet_aliases` | int | Aliased cells across all sheets |

## Aggregate stats

For a tier-by-tier breakdown, see `tools/summarize_analysis.py`:

```bash
uv run python tools/summarize_analysis.py data/parts-library/coverage.json --top-n 30
```

Top-level result on this snapshot: **98.7% in scope** (3,151 / 3,194). See `SPEC.md` §13 for the full discussion.

## Regenerating

```bash
# 1. Shallow-clone the library somewhere
git clone --depth 1 https://github.com/FreeCAD/FreeCAD-library /tmp/fc-library

# 2. Build the file list
find /tmp/fc-library \( -name "*.FCStd" -o -name "*.fcstd" \) -type f > /tmp/files.txt

# 3. Run the analyser (FreeCAD env)
PYTHONPATH=.conda/envs/freecad/lib \
  .conda/bin/micromamba run -n freecad \
  python tools/analyze_fcstd.py --input-list /tmp/files.txt --json /tmp/analysis.json

# 4. Build the committed database
LIB_COMMIT=$(git -C /tmp/fc-library rev-parse HEAD)
uv run python tools/build_coverage_database.py \
  --input /tmp/analysis.json \
  --library-root /tmp/fc-library \
  --library-commit "$LIB_COMMIT" \
  --freecad-version 1.0.0 \
  --output data/parts-library/coverage.json
```
