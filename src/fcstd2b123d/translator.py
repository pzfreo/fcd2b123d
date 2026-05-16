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
from .tier5_boolean import TIER5_BOOLEAN_HANDLERS

# Single dispatch table across all tiers. Objects whose TypeId isn't here
# raise UnsupportedFeatureError -- the translator's contract is to produce
# readable build123d Python or to refuse, never to emit shape-import or
# similar wrapper-style output that the user could already get by hand.
HANDLERS = {
    **TIER1_HANDLERS,
    **TIER2_HANDLERS,
    **TIER5_BOOLEAN_HANDLERS,
}

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


def _names_used_as_sweep_spines(doc) -> set[str]:
    """Names of objects referenced as ``Spine`` by a ``Part::Sweep``.

    Spine sketches are typically open (a single line / arc / spline)
    rather than closed loops — they describe a path, not a face. The
    sketch translator only handles closed loops, so we skip these and
    let the Sweep translator inline the spine geometry directly.
    """
    spines: set[str] = set()
    for o in doc.Objects:
        if o.TypeId != "Part::Sweep":
            continue
        spine = getattr(o, "Spine", None)
        if isinstance(spine, (list, tuple)):
            spine = spine[0] if spine else None
        if spine is not None:
            spines.add(spine.Name)
    return spines


def translate_with_context(
    fcstd_path: Path | str,
    shared_helpers: bool = False,
) -> tuple[str, TranslationContext]:
    """Translate an .FCStd file. Return (build123d_source, context).

    When ``shared_helpers`` is True, the emit imports helpers from
    ``fcstd2b123d.runtime`` instead of inlining them — see
    ``emitter.render_module``.
    """
    path = Path(fcstd_path)
    ctx = TranslationContext(
        source_path=path, freecad_version=freecad_version()
    )
    units: list[TranslationUnit] = []
    doc_description: str | None = None
    with open_document(path) as doc:
        # Tier-6: pull parameters from Spreadsheet(s) before geometry walk.
        # Handlers consult ctx.parameters when emitting property values.
        ctx.parameters = extract_parameters(doc)

        # Promote a non-default Document.Label or Document.Comment to the
        # module docstring when set. Most library files leave Label as the
        # filename — only surface it when the user typed something
        # meaningful ("M5 socket head cap screw, ISO 4762").
        doc_label = (getattr(doc, "Label", "") or "").strip()
        doc_comment = (getattr(doc, "Comment", "") or "").strip()
        if doc_comment:
            doc_description = doc_comment
        elif doc_label and doc_label != path.stem:
            doc_description = doc_label

        owned = _names_owned_by_bodies(doc)
        spines = _names_used_as_sweep_spines(doc)
        for obj in doc.Objects:
            if (
                obj.Name in owned
                or obj.Name in spines
                or obj.TypeId in INFRASTRUCTURE_TYPES
            ):
                continue
            handler = HANDLERS.get(obj.TypeId)
            if handler is None:
                raise UnsupportedFeatureError(obj.TypeId, obj.Label)
            units.extend(handler(obj, ctx))

    source = render_module(
        units,
        path,
        parameters=ctx.parameters,
        doc_description=doc_description,
        shared_helpers=shared_helpers,
    )
    return source, ctx


def translate(fcstd_path: Path | str, shared_helpers: bool = False) -> str:
    """Translate an .FCStd file to build123d Python source (compat shim)."""
    source, _ctx = translate_with_context(fcstd_path, shared_helpers=shared_helpers)
    return source
