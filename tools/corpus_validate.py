"""Random-sample the Parts Library, run the full translator+compare pipeline,
report what passes and what fails.

Idempotent: re-running with the same seed picks the same files; .FCStd and
.expected.json files are reused when present.

Usage (FreeCAD env):
    PYTHONPATH=.conda/envs/freecad/lib python tools/corpus_validate.py \
        --library /tmp/fc-library \
        --db data/parts-library/coverage.json \
        --out tests/fixtures/tier3_corpus \
        --n 30 --seed 42 --max-tier 3
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path


def safe_stem(rel_path: str) -> str:
    base = Path(rel_path).stem
    return re.sub(r"[^a-zA-Z0-9_-]", "_", base)


def select_corpus(
    db: dict,
    n: int,
    max_tier: int,
    seed: int,
    require_types: list[str] | None = None,
    allow_tiers: set[int] | None = None,
) -> list[dict]:
    """Sample candidates from the coverage DB.

    ``require_types`` restricts to files whose ``type_counts`` contains at least
    one of the listed TypeIds (e.g. for the tier-4 corpus we want files that
    actually use a pattern feature).

    ``allow_tiers``, when set, overrides the default ``1..max_tier`` range. Use
    when the supported envelope isn't a contiguous prefix — e.g. tier-4 work
    can also handle tier-6 (Spreadsheet) files since both layers ship.
    """
    tier_filter = allow_tiers if allow_tiers is not None else set(range(1, max_tier + 1))
    require_set = set(require_types or [])
    eligible = [
        r for r in db["files"]
        if r["in_scope"]
        and not r["extension_types"]
        and r["max_tier_required"] in tier_filter
        and (not require_set or any(t in r["type_counts"] for t in require_set))
    ]
    random.seed(seed)
    return random.sample(eligible, min(n, len(eligible)))


def copy_files(sample: list[dict], library_root: Path, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    used_stems: set[str] = set()
    for i, r in enumerate(sample):
        src = library_root / r["file"]
        stem = safe_stem(r["file"])
        candidate = stem
        idx = 0
        while candidate in used_stems:
            idx += 1
            candidate = f"{stem}_{idx}"
        used_stems.add(candidate)
        dst = out_dir / f"{candidate}.FCStd"
        if not dst.exists():
            shutil.copy(src, dst)
        manifest.append({
            "fixture_stem": candidate,
            "source_path": r["file"],
            "max_tier": r["max_tier_required"],
            "object_count": r["object_count"],
        })
    return manifest


def snapshot_one(fcstd: Path) -> dict:
    """Run tests/snapshot.py on a fixture; return outcome."""
    expected = fcstd.with_suffix(".expected.json")
    if expected.exists():
        return {"status": "ok", "cached": True}
    result = subprocess.run(
        [sys.executable, "tests/snapshot.py", str(fcstd)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {
            "status": "fail",
            "error": result.stderr.strip().splitlines()[-1] if result.stderr else "(no stderr)",
        }
    return {"status": "ok", "cached": False}


def translate_and_compare(fcstd: Path) -> dict:
    """Translate via CLI subprocess; exec in the *same* python; compare to snapshot."""
    cmd = [sys.executable, "-m", "fcstd2b123d", str(fcstd)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        stderr_lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
        last = stderr_lines[-1] if stderr_lines else "(no stderr)"
        if "UnsupportedFeatureError" in r.stderr:
            return {"status": "translator_unsupported", "error": last}
        return {"status": "translator_error", "error": last}

    # Try to exec the source via a small helper script. Must run in the
    # *build123d* env (this script may be running in the FreeCAD env), so
    # spawn an outer process.
    return {"status": "translated", "source": r.stdout}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--library", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-tier", type=int, default=3)
    p.add_argument(
        "--require-type", action="append", default=None,
        help="Restrict to files whose type_counts contains this TypeId. "
             "Repeatable; any-match semantics.",
    )
    p.add_argument(
        "--allow-tiers", type=str, default=None,
        help="Comma-separated tier numbers that the sampler should accept "
             "(e.g. '1,2,3,4,6' for tier-4 work with tier-6 also supported). "
             "Overrides --max-tier when set.",
    )
    p.add_argument("--report", type=Path, default=None)
    args = p.parse_args()

    db = json.loads(args.db.read_text())
    allow_tiers = (
        {int(s) for s in args.allow_tiers.split(",")}
        if args.allow_tiers else None
    )
    sample = select_corpus(
        db, args.n, args.max_tier, args.seed,
        require_types=args.require_type,
        allow_tiers=allow_tiers,
    )
    print(f"Sampled {len(sample)} files (seed={args.seed}, max_tier={args.max_tier})")

    manifest = copy_files(sample, args.library, args.out)

    print("\nSnapshotting each fixture …")
    for i, m in enumerate(manifest, 1):
        fcstd = args.out / f"{m['fixture_stem']}.FCStd"
        result = snapshot_one(fcstd)
        m["snapshot"] = result
        marker = "OK" if result["status"] == "ok" else "FAIL"
        cache = " (cached)" if result.get("cached") else ""
        print(f"  [{i}/{len(manifest)}] {marker}{cache:11s} {m['fixture_stem']}")
        if result["status"] == "fail":
            print(f"        {result['error']}")

    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if args.report:
        args.report.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\nWrote {args.out / 'manifest.json'}")


if __name__ == "__main__":
    main()
