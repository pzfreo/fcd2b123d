"""Aggregate analyze_fcstd.py JSON output into actionable coverage stats.

Pure Python — runs in any env. Used to answer questions like "what fraction of
real-world FCStd files are in scope?" and "which PartDesign operations are
common in the wild that we haven't tier-mapped yet?"

Usage:
    python tools/summarize_analysis.py path/to/analysis.json [--top-n 25]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("analysis_json", type=Path)
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()

    records = json.loads(args.analysis_json.read_text())
    total = len(records)
    errored = [r for r in records if "error" in r]
    ok = [r for r in records if "error" not in r]

    in_scope = [r for r in ok if r.get("in_scope")]
    needs_inv = [r for r in ok if r.get("needs_investigation")]
    out_of_scope = [r for r in ok if not r.get("in_scope")]

    pct = lambda n: f"{100 * n / total:.1f}%" if total else "0%"

    print(f"=== Coverage summary ===")
    print(f"  Total files:          {total}")
    print(f"  Successfully opened:  {len(ok):>5}  ({pct(len(ok))})")
    print(f"  In scope:             {len(in_scope):>5}  ({pct(len(in_scope))})")
    print(f"    of which 'needs investigation' (FeaturePython present): {len(needs_inv)}")
    print(f"  Out of scope:         {len(out_of_scope):>5}  ({pct(len(out_of_scope))})")
    print(f"  Errored on open:      {len(errored):>5}  ({pct(len(errored))})")

    # Tier distribution among in-scope
    tier_counter = Counter(r["max_tier_required"] for r in in_scope)
    print(f"\n=== Tier distribution (in-scope files only) ===")
    for tier in sorted(tier_counter):
        n = tier_counter[tier]
        print(f"  Tier {tier}:  {n:>5}  ({100*n/len(in_scope):.1f}% of in-scope)")

    # Cumulative: "Translator at tier N handles X% of in-scope files"
    print(f"\n=== Cumulative coverage by translator tier ===")
    cumulative = 0
    for tier in sorted(tier_counter):
        cumulative += tier_counter[tier]
        pct_cum = 100 * cumulative / len(in_scope) if in_scope else 0
        pct_all = 100 * cumulative / total if total else 0
        print(f"  Through tier {tier}:  {cumulative:>5}  ({pct_cum:.1f}% of in-scope, {pct_all:.1f}% of all)")

    # Most common out-of-scope reasons
    oos_types: Counter = Counter()
    for r in out_of_scope:
        for t, c in r.get("out_of_scope_types", {}).items():
            oos_types[t] += c

    print(f"\n=== Out-of-scope blockers (top {args.top_n}, by file occurrence) ===")
    oos_file_counts: Counter = Counter()
    for r in out_of_scope:
        for t in r.get("out_of_scope_types", {}):
            oos_file_counts[t] += 1
    for t, n in oos_file_counts.most_common(args.top_n):
        print(f"  {n:>5} files  {t}")

    # Unknown types — these are real gaps in our tier map
    unknown_file_counts: Counter = Counter()
    for r in out_of_scope:
        for t in r.get("unknown_types", {}):
            unknown_file_counts[t] += 1
    if unknown_file_counts:
        print(f"\n=== Unknown types (TIER-MAP GAPS — these are not workbench rejections) ===")
        for t, n in unknown_file_counts.most_common(args.top_n):
            print(f"  {n:>5} files  {t}")

    # Extension type prevalence
    ext_file_counts: Counter = Counter()
    for r in ok:
        for t in r.get("extension_types", {}):
            ext_file_counts[t] += 1
    if ext_file_counts:
        print(f"\n=== FeaturePython extension prevalence ===")
        for t, n in ext_file_counts.most_common(args.top_n):
            print(f"  {n:>5} files ({100*n/total:.1f}%)  {t}")

    # Spreadsheets and expressions
    with_sheet = sum(1 for r in ok if "Spreadsheet::Sheet" in r.get("type_counts", {}))
    with_expr = sum(1 for r in ok if r.get("expression_count", 0) > 0)
    print(f"\n=== Parametric features ===")
    print(f"  Files with Spreadsheet:    {with_sheet:>5}  ({pct(with_sheet)})")
    print(f"  Files with any expression: {with_expr:>5}  ({pct(with_expr)})")


if __name__ == "__main__":
    main()
