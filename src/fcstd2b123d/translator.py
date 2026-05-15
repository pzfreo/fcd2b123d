"""Top-level translate(): open an FCStd, dispatch each object, render source."""

from __future__ import annotations

from pathlib import Path

from .emitter import TranslationUnit, render_module
from .errors import UnsupportedFeatureError
from .loader import open_document
from .primitives import TIER1_HANDLERS

# Single dispatch table across all tiers. Tier-N handlers register here.
HANDLERS = {**TIER1_HANDLERS}


def translate(fcstd_path: Path | str) -> str:
    """Translate an .FCStd file to build123d Python source.

    v1 (tier 1): every top-level object in the document must have a registered
    handler, or UnsupportedFeatureError is raised. No silent skipping.
    """
    path = Path(fcstd_path)
    units: list[TranslationUnit] = []
    with open_document(path) as doc:
        for obj in doc.Objects:
            handler = HANDLERS.get(obj.TypeId)
            if handler is None:
                raise UnsupportedFeatureError(obj.TypeId, obj.Label)
            units.append(handler(obj))

    return render_module(units, path)
