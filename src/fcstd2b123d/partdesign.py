"""PartDesign feature translators.

Tier-2 v1 scope: ``PartDesign::Body`` containers and ``PartDesign::Pad``
features. Pocket, Revolution, and the rest land in subsequent PRs.

Body strategy: walk ``body.Group`` in order, dispatching each child through
its own translator. The body's children (sketches, pads, datums) are
*not* visited by the top-level translator loop — see
``translator._names_owned_by_bodies`` for the filter.
"""

from __future__ import annotations

from .emitter import TranslationUnit
from .errors import UnsupportedFeatureError
from .sketch import translate_sketch

_TOL = 1e-9

# Datum types owned by a Body's Origin. Skipped silently inside the body —
# they're support geometry, not translatable operations.
_BODY_INFRASTRUCTURE = {
    "App::Origin", "App::Line", "App::Plane", "App::Part",
    "PartDesign::CoordinateSystem", "PartDesign::Plane",
    "PartDesign::Line", "PartDesign::Point",
}


def translate_body(body) -> list[TranslationUnit]:
    """Walk a PartDesign::Body and emit one or more units in feature order."""
    p = body.Placement
    if abs(p.Rotation.Angle) > _TOL or any(
        abs(c) > _TOL for c in (p.Base.x, p.Base.y, p.Base.z)
    ):
        raise UnsupportedFeatureError(
            body.TypeId,
            f"{body.Label} (Body Placement non-identity; only identity-Placement "
            f"bodies in tier-2 v1)",
        )

    units: list[TranslationUnit] = []
    for child in body.Group:
        tid = child.TypeId
        if tid in _BODY_INFRASTRUCTURE:
            continue
        if tid == "Sketcher::SketchObject":
            units.extend(translate_sketch(child))
        elif tid == "PartDesign::Pad":
            units.extend(translate_pad(child))
        else:
            raise UnsupportedFeatureError(
                tid,
                f"{child.Label} (inside body {body.Label!r}; tier-2 v1 handles "
                f"Sketcher::SketchObject and PartDesign::Pad only)",
            )
    return units


def translate_pad(pad) -> list[TranslationUnit]:
    """Emit ``extrude(profile, amount=length)`` for a PartDesign::Pad.

    Tier-2 v1 only handles the default Length-type extrude in the sketch's
    normal direction. Midplane, Reversed, TwoLengths, ThroughAll, UpToFace
    and friends raise UnsupportedFeatureError.
    """
    if str(getattr(pad, "Type", "Length")) != "Length":
        raise UnsupportedFeatureError(
            pad.TypeId,
            f"{pad.Label} (Pad.Type={pad.Type!r}; only 'Length' in tier-2 v1)",
        )
    if bool(getattr(pad, "Midplane", False)):
        raise UnsupportedFeatureError(
            pad.TypeId, f"{pad.Label} (Midplane Pad not yet supported)"
        )
    if bool(getattr(pad, "Reversed", False)):
        raise UnsupportedFeatureError(
            pad.TypeId, f"{pad.Label} (Reversed Pad not yet supported)"
        )

    profile = pad.Profile
    if isinstance(profile, (list, tuple)):
        profile = profile[0]
    sketch_var = profile.Name
    length = float(pad.Length.Value)
    var = pad.Name
    return [
        TranslationUnit(
            var_name=var,
            imports={"extrude"},
            lines=[f"{var} = extrude({sketch_var}, amount={length})"],
            comment=f"PartDesign::Pad {pad.Label!r}: length={length}",
        )
    ]


TIER2_HANDLERS = {
    "PartDesign::Body": translate_body,
}
