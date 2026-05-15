"""Random-sample N files from the FreeCAD Parts Library, run the full
translate-and-verify pipeline on each, write a report.

Unlike ``corpus_validate.py`` this samples *uniformly* from the entire
library without any tier filter — the result is an honest measurement of
what fraction of real-world FreeCAD files the translator can handle today.

For files already present in another fixture directory the run reuses the
existing snapshot rather than re-snapshotting (per the experiment's
"don't retest known cases" rule).

Outputs:
  - tests/fixtures/sample_<seed>/<stem>.FCStd       (copied)
  - tests/fixtures/sample_<seed>/<stem>.expected.json (snapshot)
  - tests/fixtures/sample_<seed>/<stem>.pointcloud.json (snapshot)
  - tests/fixtures/sample_<seed>/manifest.json      (path, status, error)
  - tests/fixtures/sample_<seed>/REPORT.md          (human-readable summary)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from pathlib import Path


def safe_stem(rel_path: str) -> str:
    base = Path(rel_path).stem
    return re.sub(r"[^a-zA-Z0-9_-]", "_", base)


def build_existing_map(fixtures_root: Path) -> dict[str, tuple[str, str]]:
    """Map source_path → (corpus_dir, fixture_stem) across all manifests.

    Skip entries that have no ``fixture_stem`` (those came from a prior
    sample run where the fixture itself was a cached reference rather than
    a freshly-copied source file).
    """
    m: dict[str, tuple[str, str]] = {}
    for manifest in fixtures_root.glob("*/manifest.json"):
        for fx in json.loads(manifest.read_text()):
            src = fx.get("source_path")
            stem = fx.get("fixture_stem")
            if src and stem:
                m[src] = (manifest.parent.name, stem)
    return m


def read_excluded(test_file: Path) -> set[str]:
    text = test_file.read_text()
    m = re.search(r"EXCLUDED_FROM_TEST = \{(.*?)\}", text, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def snapshot_one(fcstd: Path, timeout: int = 90) -> dict:
    """Run tests/snapshot.py via the FreeCAD-enabled python with a hard timeout."""
    expected = fcstd.with_suffix(".expected.json")
    if expected.exists():
        return {"status": "ok", "cached": True}
    fcpy = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not fcpy:
        return {"status": "fail", "error": "FCSTD2B123D_FREECAD_PYTHON not set"}
    pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src = str(Path(__file__).parent.parent / "src")
    env = {**os.environ, "PYTHONPATH": ":".join(p for p in (src, pp) if p)}
    try:
        r = subprocess.run(
            [fcpy, "tests/snapshot.py", str(fcstd)],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"snapshot exceeded {timeout}s"}
    if r.returncode != 0:
        last = next(
            (l for l in reversed(r.stderr.strip().splitlines()) if l.strip()),
            "(no stderr)",
        )
        return {"status": "fail", "error": last}
    return {"status": "ok", "cached": False}


def translate_and_run(fcstd: Path, timeout_t: int = 30, timeout_x: int = 30) -> dict:
    """Translate via subprocess, exec result here, compare to snapshot.

    Mirrors ``tools/corpus_run_translation.py``'s evaluate() but adds:
      - timeouts per phase
      - shape-import support (writes STEP sidecars next to the .py output)
    """
    py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not py:
        return {"status": "skipped", "error": "FCSTD2B123D_FREECAD_PYTHON not set"}

    out_py = fcstd.with_suffix(".py")
    pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src = str(Path(__file__).parent.parent / "src")
    env = {**os.environ, "PYTHONPATH": ":".join(p for p in (src, pp) if p)}

    try:
        r = subprocess.run(
            [py, "-m", "fcstd2b123d", str(fcstd), "-o", str(out_py)],
            capture_output=True, text=True, env=env, timeout=timeout_t,
        )
    except subprocess.TimeoutExpired:
        return {"status": "translator_timeout", "error": f">{timeout_t}s"}

    if r.returncode != 0:
        last = next(
            (l for l in reversed(r.stderr.strip().splitlines()) if l.strip()),
            "(no stderr)",
        )
        if "UnsupportedFeatureError" in r.stderr:
            return {"status": "unsupported", "error": last}
        return {"status": "translator_error", "error": last}

    source = out_py.read_text()
    # Exec the emit in this build123d process. cwd to fixture dir so the
    # _HERE fallback (used when __file__ isn't bound) resolves any STEP
    # sidecars correctly.
    cwd = os.getcwd()
    os.chdir(fcstd.parent)
    namespace: dict = {}
    try:
        try:
            exec(compile(source, str(out_py), "exec"), namespace)
        except Exception as e:
            return {
                "status": "exec_error",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc().splitlines()[-3:],
            }
    finally:
        os.chdir(cwd)

    if "result" not in namespace:
        return {"status": "no_result_var"}

    from fcstd2b123d.verify import compare, extract_build123d
    from fcstd2b123d.properties import Properties
    try:
        actual = extract_build123d(namespace["result"])
    except Exception as e:
        return {"status": "extract_error", "error": f"{type(e).__name__}: {e}"}

    expected_path = fcstd.with_suffix(".expected.json")
    if not expected_path.exists():
        return {"status": "no_snapshot"}

    expected = Properties.from_file(expected_path)
    res = compare(actual, expected)
    if res.passed:
        return {"status": "pass"}
    return {
        "status": "props_mismatch",
        "errors": {r.name: r.detail for r in res.results if not r.passed},
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--library", type=Path, default=Path("/tmp/fc-library"))
    p.add_argument("--db", type=Path, default=Path("data/parts-library/coverage.json"))
    p.add_argument("--seed", type=int, default=813)
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    out_dir = args.out or Path(f"tests/fixtures/sample_{args.seed}")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Sampling {args.n} files at seed={args.seed} from {args.db}")
    db = json.loads(args.db.read_text())
    all_files = [r["file"] for r in db["files"]]
    random.seed(args.seed)
    sample = random.sample(all_files, args.n)

    existing = build_existing_map(Path("tests/fixtures"))
    excluded = read_excluded(Path("tests/test_translator_corpus.py"))

    results = []
    used_stems: set[str] = set()
    for i, src in enumerate(sample, 1):
        rec = {"source_path": src}

        # Cached path: file is already in another fixture dir.
        if src in existing:
            cdir, cstem = existing[src]
            if cstem in excluded:
                rec.update({
                    "status": "cached_excluded",
                    "cached_fixture": f"{cdir}/{cstem}",
                })
            else:
                rec.update({
                    "status": "cached_pass",
                    "cached_fixture": f"{cdir}/{cstem}",
                })
            results.append(rec)
            print(f"  [{i:3}/{args.n}] CACHED  {rec['status']:18s} {src[-60:]}")
            continue

        # Fresh path: copy, snapshot, translate, compare.
        stem = safe_stem(src)
        candidate = stem
        n = 0
        while candidate in used_stems:
            n += 1
            candidate = f"{stem}_{n}"
        used_stems.add(candidate)
        rec["fixture_stem"] = candidate

        srcp = args.library / src
        if not srcp.exists():
            rec["status"] = "source_missing"
            results.append(rec)
            print(f"  [{i:3}/{args.n}] MISSING {src}")
            continue

        dst = out_dir / f"{candidate}.FCStd"
        shutil.copy(srcp, dst)

        t0 = time.time()
        snap = snapshot_one(dst)
        rec["snapshot_time_s"] = round(time.time() - t0, 1)
        rec["snapshot"] = snap
        if snap["status"] != "ok":
            rec["status"] = f"snapshot_{snap['status']}"
            results.append(rec)
            print(f"  [{i:3}/{args.n}] {rec['status']:18s} {candidate}  ({snap.get('error', '')[:60]})")
            # Drop the FCStd for un-snapshottable files so we don't bloat the repo.
            dst.unlink(missing_ok=True)
            continue

        t1 = time.time()
        trn = translate_and_run(dst)
        rec["translate_time_s"] = round(time.time() - t1, 1)
        rec["translate"] = trn
        rec["status"] = trn["status"]
        results.append(rec)
        marker = "PASS" if trn["status"] == "pass" else trn["status"].upper()
        print(f"  [{i:3}/{args.n}] {marker:18s} {candidate}")
        if trn["status"] != "pass":
            err = trn.get("error") or trn.get("errors") or ""
            if isinstance(err, dict):
                err = "; ".join(f"{k}={v[:60]}" for k, v in err.items())
            print(f"        {str(err)[:200]}")

    # Manifest + report
    (out_dir / "manifest.json").write_text(json.dumps(results, indent=2) + "\n")

    summary = Counter(r["status"] for r in results)
    report = [
        f"# sample_{args.seed} — true-random library audit",
        "",
        f"Random sample of **{args.n}** files at seed `{args.seed}` from the "
        f"full {len(db['files'])}-file FreeCAD Parts Library coverage DB. "
        f"No tier filter — out-of-scope, FeaturePython, multi-body, the lot.",
        "",
        "## Outcome distribution",
        "",
        "| Status | Count | % |",
        "|---|---:|---:|",
    ]
    for status, n in summary.most_common():
        pct = 100 * n / args.n
        report.append(f"| `{status}` | {n} | {pct:.0f}% |")
    report.append("")
    pass_count = summary.get("pass", 0) + summary.get("cached_pass", 0)
    report.append(
        f"**Accurate translation rate: {pass_count}/{args.n} = {pass_count}% "
        "** (PASS or cached-PASS — geometry matches FreeCAD's BRep within "
        "the four-scalar + Hausdorff tolerances)."
    )
    report.append("")
    report.append("## Per-file results (manifest.json has the full data)")
    report.append("")
    report.append("| # | source path | status |")
    report.append("|---:|---|---|")
    for i, r in enumerate(results, 1):
        report.append(f"| {i} | `{r['source_path'][-80:]}` | `{r['status']}` |")

    (out_dir / "REPORT.md").write_text("\n".join(report) + "\n")
    print(f"\nWrote {out_dir / 'REPORT.md'}")
    print(f"\nSummary:")
    for status, n in summary.most_common():
        print(f"  {status:25s} {n:>3}")


if __name__ == "__main__":
    main()
