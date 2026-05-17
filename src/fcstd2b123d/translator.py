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


def _auto_select_style(doc) -> str:
    """Pick ``"algebra"`` or ``"builder"`` based on document structure.

    Builder mode (``with BuildPart() as body: ...``) suits a single
    PartDesign body with a Pad/Pocket/Fillet/etc. chain. It reads worse
    for documents that have a fundamentally different shape — multi-body
    assemblies, top-level Part workbench booleans, atomic Pads/Pockets
    without an enclosing Body, or tier-6 parametric models that already
    emit as ``def make_part(...):``.

    This is the auto-fallback that runs when ``--style=auto`` (the new
    default after #117). Returning ``"algebra"`` lets a category that
    builder mode can't improve emit the same way it always did.
    """
    bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
    if len(bodies) > 1:
        return "algebra"  # multi-body — algebra composes bodies honestly

    boolean_types = {"Part::Cut", "Part::Fuse", "Part::Common"}
    if any(o.TypeId in boolean_types for o in doc.Objects):
        return "algebra"  # top-level Part booleans read better as a - b

    # Atomic Pads/Pockets/Revolutions not enclosed in a Body. These get
    # emitted as ``var = base - extrude(...)`` style; a BuildPart wrap
    # would add indentation without clarity.
    owned = _names_owned_by_bodies(doc)
    atomic_pd_types = {
        "PartDesign::Pad", "PartDesign::Pocket",
        "PartDesign::Revolution", "PartDesign::Groove",
    }
    if any(
        o.TypeId in atomic_pd_types and o.Name not in owned
        for o in doc.Objects
    ):
        return "algebra"

    # Tier-6 spreadsheet-driven models already render as
    # ``def make_part(width=..., ...):`` — the function wrapper makes
    # the body short enough that algebra reads fine.
    if any(o.TypeId == "Spreadsheet::Sheet" for o in doc.Objects):
        return "algebra"

    # Sketches containing ellipses with non-zero center or rotation are
    # not yet supported by builder-mode sketch emit — the BuildSketch
    # wrapper loses the Pos/Rot placement (#117 future work). Fall back
    # to algebra for these until builder-mode ellipse handling lands.
    for o in doc.Objects:
        if o.TypeId != "Sketcher::SketchObject":
            continue
        for g in getattr(o, "Geometry", []):
            if type(g).__name__ != "Ellipse":
                continue
            import math as _math
            cx, cy = g.Center.x, g.Center.y
            ang = _math.degrees(getattr(g, "AngleXU", 0.0))
            if abs(cx) > 1e-6 or abs(cy) > 1e-6 or abs(ang) > 1e-6:
                return "algebra"

    return "builder"


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
    style: str | None = None,
    body_style: str | None = None,
    emit: str = "script",
) -> tuple[str, TranslationContext]:
    """Translate an .FCStd file. Return (build123d_source, context).

    ``shared_helpers``: when True, the emit imports helpers from
    ``fcstd2b123d.runtime`` instead of inlining them.

    ``body_style``: API style inside the body — ``"auto"`` (default,
    picks builder/algebra per document via ``_auto_select_style``),
    ``"algebra"``, or ``"builder"``. See the family-extraction design
    doc for the rationale on splitting body-style from emit shape.

    ``emit``: module top-level shape — ``"script"`` (default; emits
    ``result = …``), ``"function"`` (``def make_part(...)``),
    ``"class"`` (``class Foo(BasePartObject)``). Phase 1 of the
    family-extraction work flips the default to ``"class"``.

    ``style``: deprecated alias for ``body_style``. Accepted for
    back-compat; will be removed in a future major version.
    """
    # Back-compat: --style maps to body_style if body_style isn't set.
    if body_style is None:
        body_style = style if style is not None else "auto"

    path = Path(fcstd_path)
    units: list[TranslationUnit] = []
    doc_description: str | None = None
    with open_document(path) as doc:
        # Resolve auto inside the open_document block so we don't pay
        # the ~700ms FreeCAD doc-load cost twice.
        if body_style == "auto":
            body_style = _auto_select_style(doc)
        ctx = TranslationContext(
            source_path=path, freecad_version=freecad_version(), style=body_style
        )
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

    if emit not in ("script", "function", "class"):
        raise ValueError(f"unknown emit value: {emit!r}")

    source = render_module(
        units,
        path,
        parameters=ctx.parameters,
        doc_description=doc_description,
        shared_helpers=shared_helpers,
        emit=emit,
    )
    return source, ctx


def translate(
    fcstd_path: Path | str,
    shared_helpers: bool = False,
    style: str = "auto",
) -> str:
    """Translate an .FCStd file to build123d Python source (compat shim)."""
    source, _ctx = translate_with_context(
        fcstd_path, shared_helpers=shared_helpers, style=style
    )
    return source
