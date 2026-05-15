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
from .partdesign import TIER2_HANDLERS
from .primitives import TIER1_HANDLERS

# Single dispatch table across all tiers.
HANDLERS = {**TIER1_HANDLERS, **TIER2_HANDLERS}

# Document-level infrastructure types — appear in valid documents but carry
# no translatable content. Silently skipped at the top level.
INFRASTRUCTURE_TYPES = {
    "App::Origin", "App::Line", "App::Plane", "App::Part",
    "App::DocumentObjectGroup",
    # PartDesign datums that appear at document level in some legacy files
    # (e.g. multi-Body mannequins). They're support frames for downstream
    # features and don't translate to anything on their own.
    "PartDesign::CoordinateSystem",
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
) -> tuple[str, TranslationContext]:
    """Translate an .FCStd file. Return (build123d_source, context)."""
    path = Path(fcstd_path)
    ctx = TranslationContext(
        source_path=path, freecad_version=freecad_version()
    )
    units: list[TranslationUnit] = []
    with open_document(path) as doc:
        owned = _names_owned_by_bodies(doc)
        for obj in doc.Objects:
            if obj.Name in owned or obj.TypeId in INFRASTRUCTURE_TYPES:
                continue
            handler = HANDLERS.get(obj.TypeId)
            if handler is None:
                raise UnsupportedFeatureError(obj.TypeId, obj.Label)
            units.extend(handler(obj, ctx))

    source = render_module(units, path)
    return source, ctx


def translate(fcstd_path: Path | str) -> str:
    """Translate an .FCStd file to build123d Python source (compat shim)."""
    source, _ctx = translate_with_context(fcstd_path)
    return source
