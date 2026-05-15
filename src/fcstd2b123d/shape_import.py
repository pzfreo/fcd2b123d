"""Shape-import fallback for Part::Feature and FeaturePython objects.

Per SPEC §13.5 and ADR-0004's graceful-degradation strategy: objects whose
parametric history can't be translated (community-authored FeaturePython,
imported BRep wrappers) export their evaluated shape to a STEP sidecar.
The emitted Python imports the STEP at exec time via build123d's
``import_step``. The parametric story is lost — geometry survives.

Highest-ROI single feature outside the existing tier set: ~28% of the
Parts Library uses one of these TypeIds.
"""

from __future__ import annotations

from .context import TranslationContext
from .emitter import TranslationUnit, _snake_case
from .errors import UnsupportedFeatureError
from .freecad_properties import extract_properties


SHAPE_IMPORT_TYPE_IDS = {
    "Part::Feature",
    "Part::FeaturePython",
    "App::FeaturePython",
    "Part::Part2DObjectPython",
}


def translate_shape_import(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    """Export the FreeCAD shape to a STEP sidecar; emit ``import_step``.

    Each shape-import object becomes a single named variable that loads the
    saved STEP at exec time. The sidecar path is resolved via
    ``Path(__file__).parent`` so the emitted .py stays portable as long as
    its sidecar STEP files travel with it.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} (no Shape — cannot fall back to shape-import)",
        )
    if ctx.assets_dir is None:
        raise UnsupportedFeatureError(
            obj.TypeId,
            f"{obj.Label} ({obj.TypeId} requires shape-import fallback; "
            f"pass -o <output.py> so the translator can write the STEP "
            f"sidecar alongside)",
        )

    # Pre-snake_case the variable + filename so they stay in sync — the
    # global snake_case post-pass would otherwise rename the assignment
    # target but leave the matching quoted filename in the import_step
    # call alone (or vice versa). Doing it here keeps both identical.
    var = _snake_case(obj.Name)

    ctx.assets_dir.mkdir(parents=True, exist_ok=True)
    stem = ctx.output_stem or "shape"
    sidecar_name = f"{stem}.{var}.step"
    sidecar_path = ctx.assets_dir / sidecar_name
    shape.exportStep(str(sidecar_path))

    line = (
        f'{var} = import_step('
        f'str(_HERE / "{sidecar_name}"))'
    )
    unit = TranslationUnit(
        var_name=var,
        imports={"import_step"},
        lines=[line],
        comment=(
            f"{obj.TypeId} {obj.Label!r}: shape-import fallback "
            f"(parametric history not preserved; geometry from STEP sidecar)"
        ),
        helpers={"_HERE"},
    )
    ctx.add_step(
        feature_type="shape_import",
        feature_name=obj.Name,
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(shape),
    )
    return [unit]


SHAPE_IMPORT_HANDLERS = {tid: translate_shape_import for tid in SHAPE_IMPORT_TYPE_IDS}
