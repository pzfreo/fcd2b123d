"""Render side-by-side isometric PNGs of a FreeCAD shape and the build123d
translation, for the README hero example. Also exports both as STL so a
reader can drop them into any STL viewer to confirm by eye.

Two-step internally: a FreeCAD-env subprocess exports the source shape to
STL; this script (in the build123d env) translates + exports its own STL,
then renders both with VTK from the same camera.

Pre-issue-#18 placeholder — the long-term render gallery will likely
replace this with a richer comparison pipeline.

Usage:
    FCSTD2B123D_FREECAD_PYTHON=.conda/envs/freecad/bin/python \\
    FCSTD2B123D_FREECAD_PYTHONPATH=.conda/envs/freecad/lib \\
        .venv/bin/python tools/render_example.py \\
            tests/fixtures/.../file.FCStd \\
            docs/images/file --stl-dir docs/examples
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


_FREECAD_EXPORT = """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[3]).resolve()))
import FreeCAD
from fcstd2b123d.snapshot import select_target

doc = FreeCAD.openDocument(sys.argv[1])
doc.recompute()
target = select_target(doc)
shape = target.Shape
if shape.ShapeType == 'Compound':
    shape = shape.Solids[0]
shape.exportStl(sys.argv[2])
"""


def _freecad_export_stl(fcstd_path: Path, stl_path: Path) -> None:
    py = os.environ["FCSTD2B123D_FREECAD_PYTHON"]
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_path = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (src_path, fc_pp) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}

    subprocess.run(
        [py, "-c", _FREECAD_EXPORT, str(fcstd_path), str(stl_path), src_path],
        check=True, capture_output=True, text=True, env=env,
    )


def _build123d_translate_and_export(fcstd_path: Path, stl_path: Path) -> str:
    """Translate the .FCStd to build123d Python (FreeCAD env subprocess), exec
    it in this process (build123d env), and export the result to STL.
    Returns the emitted source.
    """
    py = os.environ["FCSTD2B123D_FREECAD_PYTHON"]
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    src_path = str(Path(__file__).parent.parent / "src")
    pythonpath = ":".join(p for p in (src_path, fc_pp) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    r = subprocess.run(
        [py, "-m", "fcstd2b123d", str(fcstd_path)],
        check=True, capture_output=True, text=True, env=env,
    )
    source = r.stdout

    from build123d import export_stl

    ns: dict = {}
    exec(compile(source, str(fcstd_path) + ".py", "exec"), ns)
    export_stl(ns["result"], str(stl_path))
    return source


def _render_stl(stl_path: Path, png_path: Path, *, title: str, colour: tuple[float, float, float]) -> None:
    """Render an STL to PNG via VTK off-screen."""
    import vtk

    reader = vtk.vtkSTLReader()
    reader.SetFileName(str(stl_path))
    reader.Update()

    # Smooth normals for nicer shading on curved surfaces.
    normals = vtk.vtkPolyDataNormals()
    normals.SetInputConnection(reader.GetOutputPort())
    normals.SetFeatureAngle(45)
    normals.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(normals.GetOutputPort())

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*colour)
    actor.GetProperty().SetSpecular(0.3)
    actor.GetProperty().SetSpecularPower(20)
    actor.GetProperty().SetDiffuse(0.85)
    actor.GetProperty().SetAmbient(0.25)

    renderer = vtk.vtkRenderer()
    renderer.SetBackground(1.0, 1.0, 1.0)
    renderer.AddActor(actor)

    # Isometric camera: position the camera along (1,1,1) and aim at the centroid.
    bounds = actor.GetBounds()
    cx = (bounds[0] + bounds[1]) / 2
    cy = (bounds[2] + bounds[3]) / 2
    cz = (bounds[4] + bounds[5]) / 2
    span = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
    cam = renderer.GetActiveCamera()
    d = span * 2.4
    cam.SetPosition(cx + d, cy - d, cz + d * 0.7)
    cam.SetFocalPoint(cx, cy, cz)
    cam.SetViewUp(0, 0, 1)
    renderer.ResetCameraClippingRange()

    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.SetSize(900, 900)
    win.AddRenderer(renderer)
    win.Render()

    grabber = vtk.vtkWindowToImageFilter()
    grabber.SetInput(win)
    grabber.SetScale(1)
    grabber.SetInputBufferTypeToRGBA()
    grabber.Update()

    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(png_path))
    writer.SetInputConnection(grabber.GetOutputPort())
    writer.Write()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("fcstd", type=Path, help=".FCStd source")
    p.add_argument(
        "stem", type=Path,
        help="Image output stem; writes <stem>.freecad.png + <stem>.build123d.png",
    )
    p.add_argument(
        "--stl-dir", type=Path, default=None,
        help="If set, also commit the .stl files there (alongside or distinct from images).",
    )
    args = p.parse_args()

    stl_dir = args.stl_dir or args.stem.parent
    stl_dir.mkdir(parents=True, exist_ok=True)
    args.stem.parent.mkdir(parents=True, exist_ok=True)

    fc_stl = stl_dir / f"{args.stem.name}.freecad.stl"
    bd_stl = stl_dir / f"{args.stem.name}.build123d.stl"
    fc_png = args.stem.with_suffix(".freecad.png")
    bd_png = args.stem.with_suffix(".build123d.png")

    print("Exporting FreeCAD STL …")
    _freecad_export_stl(args.fcstd, fc_stl)
    print(f"  Wrote {fc_stl} ({fc_stl.stat().st_size // 1024} KB)")

    print("Translating + exporting build123d STL …")
    _build123d_translate_and_export(args.fcstd, bd_stl)
    print(f"  Wrote {bd_stl} ({bd_stl.stat().st_size // 1024} KB)")

    print("Rendering FreeCAD STL → PNG …")
    _render_stl(fc_stl, fc_png, title="FreeCAD source", colour=(0.48, 0.65, 0.81))
    print(f"  Wrote {fc_png}")

    print("Rendering build123d STL → PNG …")
    _render_stl(bd_stl, bd_png, title="build123d translation", colour=(0.62, 0.79, 0.62))
    print(f"  Wrote {bd_png}")


if __name__ == "__main__":
    main()
