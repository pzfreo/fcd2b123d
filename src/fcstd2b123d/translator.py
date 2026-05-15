"""Top-level translate(): open an FCStd, dispatch each object, render source."""

from __future__ import annotations

from pathlib import Path

from .emitter import TranslationUnit, render_module
from .errors import UnsupportedFeatureError
from .loader import open_document
from .partdesign import TIER2_HANDLERS
from .primitives import TIER1_HANDLERS

# Single dispatch table across all tiers. Tier-N handlers register here.
HANDLERS = {**TIER1_HANDLERS, **TIER2_HANDLERS}

# Document-level infrastructure types — appear in valid documents but carry
# no translatable content. Silently skipped at the top level. (Children of a
# Body's Origin are also skipped via the body-ownership filter below.)
INFRASTRUCTURE_TYPES = {
    "App::Origin", "App::Line", "App::Plane", "App::Part",
    "App::DocumentObjectGroup",
}


def _names_owned_by_bodies(doc) -> set[str]:
    """Names of objects that should be processed via their Body, not directly.

    A PartDesign::Body's Group lists its features (sketches, pads, etc.) and
    its Origin holds datum planes/lines. All appear in doc.Objects too — but
    we want the Body handler to compose them, not the top-level loop.
    """
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


def translate(fcstd_path: Path | str) -> str:
    """Translate an .FCStd file to build123d Python source.

    Every top-level object must either:
      - have a registered handler,
      - be infrastructure (silently skipped),
      - or be owned by a Body container (handled by the Body's translator).

    Otherwise UnsupportedFeatureError is raised — no silent passes.
    """
    path = Path(fcstd_path)
    units: list[TranslationUnit] = []
    with open_document(path) as doc:
        owned = _names_owned_by_bodies(doc)
        for obj in doc.Objects:
            if obj.Name in owned or obj.TypeId in INFRASTRUCTURE_TYPES:
                continue
            handler = HANDLERS.get(obj.TypeId)
            if handler is None:
                raise UnsupportedFeatureError(obj.TypeId, obj.Label)
            units.extend(handler(obj))

    return render_module(units, path)
