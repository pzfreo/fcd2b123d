"""Phase 2: run the translator + property comparison on every corpus fixture.

Runs in the build123d env. The translator itself is invoked as a subprocess
in the FreeCAD env (FCSTD2B123D_FREECAD_PYTHON + FCSTD2B123D_FREECAD_PYTHONPATH).

Categorises each result and writes a report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

# Make tests/utils importable
sys.path.insert(0, str(Path(__file__).parent.parent))
from fcstd2b123d.verify import compare, extract_build123d
from fcstd2b123d.properties import Properties


def translate(fcstd: Path) -> tuple[int, str, str]:
    py = os.environ["FCSTD2B123D_FREECAD_PYTHON"]
    pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_path = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (src_path, pp) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    r = subprocess.run(
        [py, "-m", "fcstd2b123d", str(fcstd)],
        capture_output=True, text=True, env=env,
    )
    return r.returncode, r.stdout, r.stderr


def evaluate(fcstd: Path) -> dict:
    rc, stdout, stderr = translate(fcstd)
    if rc != 0:
        last = next(
            (l for l in reversed(stderr.strip().splitlines()) if l.strip()),
            "(no stderr)",
        )
        if "UnsupportedFeatureError" in stderr:
            return {"status": "unsupported", "error": last}
        return {"status": "translator_error", "error": last}

    namespace: dict = {}
    try:
        exec(stdout, namespace)
    except Exception as e:
        return {
            "status": "exec_error",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc().splitlines()[-3:],
        }

    if "result" not in namespace:
        return {"status": "no_result_var"}

    try:
        actual = extract_build123d(namespace["result"])
    except Exception as e:
        return {
            "status": "extract_error",
            "error": f"{type(e).__name__}: {e}",
        }

    expected = Properties.from_file(fcstd.with_suffix(".expected.json"))
    res = compare(actual, expected)
    if res.passed:
        return {"status": "pass"}
    return {
        "status": "props_mismatch",
        "errors": {
            r.name: r.detail
            for r in res.results if not r.passed
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--corpus", type=Path, default=Path("tests/fixtures/tier3_corpus"))
    p.add_argument("--report", type=Path, default=Path("/tmp/corpus_report.json"))
    args = p.parse_args()

    manifest_path = args.corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    results = []
    for i, m in enumerate(manifest, 1):
        fcstd = args.corpus / f"{m['fixture_stem']}.FCStd"
        outcome = evaluate(fcstd)
        m_full = {**m, "result": outcome}
        results.append(m_full)
        status = outcome["status"]
        emoji = {"pass": "PASS"}.get(status, status.upper())
        print(f"  [{i:2}/{len(manifest)}] {emoji:18s} {m['fixture_stem']}")
        if status != "pass":
            err = outcome.get("error") or outcome.get("errors") or ""
            if isinstance(err, dict):
                err = "; ".join(f"{k}={v[:60]}" for k, v in err.items())
            print(f"        {str(err)[:200]}")

    args.report.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nReport: {args.report}")

    # Summary by status
    from collections import Counter
    summary = Counter(r["result"]["status"] for r in results)
    print("\nSummary:")
    for status, n in summary.most_common():
        print(f"  {status:20s} {n:>3}")


if __name__ == "__main__":
    main()
