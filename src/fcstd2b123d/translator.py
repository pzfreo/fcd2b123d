"""Top-level translate(): open an FCStd, dispatch each object, render source.

Returns the build123d Python source. When the caller also wants the
structured TranslationContext (see SPEC §14), use ``translate_with_context``
which returns the (source, context) pair.
"""

from __future__ import annotations

from pathlib import Path

from .context import TranslationContext
from .emitter import TranslationUnit, render_module
from .errors import UnsupportedFeatureError
from .freecad_properties import freecad_version
from .loader import open_document
from .parametric import extract_parameters
from .partdesign import TIER2_HANDLERS
from .primitives import TIER1_HANDLERS
from .shape_import import SHAPE_IMPORT_HANDLERS

# Single dispatch table across all tiers. SHAPE_IMPORT_HANDLERS is the
# graceful-degradation path for Part::Feature and FeaturePython objects
# whose parametric history we can't translate — see SPEC §13.5.
HANDLERS = {**TIER1_HANDLERS, **TIER2_HANDLERS, **SHAPE_IMPORT_HANDLERS}

# Document-level infrastructure types — appear in valid documents but carry
# no translatable content. Silently skipped at the top level.
INFRASTRUCTURE_TYPES = {
    "App::Origin", "App::Line", "App::Plane", "App::Part",
    "App::DocumentObjectGroup",
    # PartDesign datums that appear at document level in some legacy files
    # (e.g. multi-Body mannequins). They're support frames for downstream
    # features and don't translate to anything on their own.
    "PartDesign::CoordinateSystem",
    # Spreadsheets carry the parameter values; the translator extracts them
    # via extract_parameters() before the main loop runs, so they don't
    # need a direct translation.
    "Spreadsheet::Sheet",
}


def _names_owned_by_bodies(doc) -> set[str]:
    """Names of objects that should be processed via their Body."""
    owned: set[str] = set()
    for o in doc.Objects:
        if o.TypeId != "PartDesign::Body":
            continue
        for child in o.Group:
            owned.add(child.Name)
        if getattr(o, "Origin", None) is not None:
            owned.add(o.Origin.Name)
            for d in o.Origin.OutList:
                owned.add(d.Name)
    return owned


def translate_with_context(
    fcstd_path: Path | str,
    assets_dir: Path | str | None = None,
    output_stem: str | None = None,
) -> tuple[str, TranslationContext]:
    """Translate an .FCStd file. Return (build123d_source, context).

    ``assets_dir`` is the directory where shape-import handlers write STEP
    sidecars for Part::Feature / FeaturePython objects. ``output_stem``
    namespaces the sidecar filenames. Both can be left as None when the
    source is known not to contain shape-import objects; the relevant
    handler raises a clear error otherwise.
    """
    path = Path(fcstd_path)
    ctx = TranslationContext(
        source_path=path,
        freecad_version=freecad_version(),
        assets_dir=Path(assets_dir) if assets_dir is not None else None,
        output_stem=output_stem or path.stem,
    )
    units: list[TranslationUnit] = []
    with open_document(path) as doc:
        # Tier-6: pull parameters from Spreadsheet(s) before geometry walk.
        # Handlers consult ctx.parameters when emitting property values.
        ctx.parameters = extract_parameters(doc)

        owned = _names_owned_by_bodies(doc)
        for obj in doc.Objects:
            if obj.Name in owned or obj.TypeId in INFRASTRUCTURE_TYPES:
                continue
            handler = HANDLERS.get(obj.TypeId)
            if handler is None:
                raise UnsupportedFeatureError(obj.TypeId, obj.Label)
            units.extend(handler(obj, ctx))

    source = render_module(units, path, parameters=ctx.parameters)
    return source, ctx


def translate(fcstd_path: Path | str) -> str:
    """Translate an .FCStd file to build123d Python source (compat shim)."""
    source, _ctx = translate_with_context(fcstd_path)
    return source
