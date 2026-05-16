"""Tier-5 Part-workbench boolean operations.

Translates Part::Cut / Fuse / Common / MultiFuse / MultiCommon — top-level
boolean combinations of separate shapes. Distinct from PartDesign Body
chaining (which already handles additive/subtractive features inside a
single body via the existing primitives.py / partdesign.py translators):
these features compose *complete shapes* into a new shape.

Library impact: ~430 in-scope files use one of these TypeIds.
"""

from __future__ import annotations

from .context import TranslationContext
from .emitter import TranslationUnit
from .errors import UnsupportedFeatureError
from .freecad_properties import extract_properties


def _input_names(inputs) -> list[str]:
    """Extract a flat list of input object names from a Property value.

    Part::Cut.Base / .Tool are single objects. Part::MultiFuse.Shapes is a
    list. The translator references each input by its FreeCAD Name (which
    the emit-time snake_case post-pass lowercases consistently).
    """
    if inputs is None:
        return []
    if isinstance(inputs, (list, tuple)):
        return [s.Name for s in inputs if s is not None]
    return [inputs.Name]


def _translate_cut(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    """``Part::Cut`` -> ``base - tool``."""
    if obj.Base is None or obj.Tool is None:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} (Base or Tool unset)"
        )
    base_var = obj.Base.Name
    tool_var = obj.Tool.Name
    line = f"{obj.Name} = _pattern_difference({base_var}, {tool_var})"
    unit = TranslationUnit(
        var_name=obj.Name,
        label=obj.Label,
        lines=[line],
        comment=f"Part::Cut {obj.Label!r}: {base_var} - {tool_var}",
        helpers={"_pattern_difference"},
    )
    ctx.add_step(
        feature_type="boolean_cut",
        feature_name=obj.Name,
        depends_on=[base_var, tool_var],
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(getattr(obj, "Shape", None)),
    )
    return [unit]


def _translate_fuse(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    """``Part::Fuse`` -> ``base + tool`` (boolean union)."""
    if obj.Base is None or obj.Tool is None:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} (Base or Tool unset)"
        )
    base_var = obj.Base.Name
    tool_var = obj.Tool.Name
    line = f"{obj.Name} = _pattern_union({base_var}, {tool_var})"
    unit = TranslationUnit(
        var_name=obj.Name,
        label=obj.Label,
        lines=[line],
        comment=f"Part::Fuse {obj.Label!r}: {base_var} + {tool_var}",
        helpers={"_pattern_union"},
    )
    ctx.add_step(
        feature_type="boolean_fuse",
        feature_name=obj.Name,
        depends_on=[base_var, tool_var],
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(getattr(obj, "Shape", None)),
    )
    return [unit]


def _translate_common(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    """``Part::Common`` -> ``base & tool`` (boolean intersection)."""
    if obj.Base is None or obj.Tool is None:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} (Base or Tool unset)"
        )
    base_var = obj.Base.Name
    tool_var = obj.Tool.Name
    line = f"{obj.Name} = _pattern_intersection({base_var}, {tool_var})"
    unit = TranslationUnit(
        var_name=obj.Name,
        label=obj.Label,
        lines=[line],
        comment=f"Part::Common {obj.Label!r}: {base_var} & {tool_var}",
        helpers={"_pattern_intersection"},
    )
    ctx.add_step(
        feature_type="boolean_common",
        feature_name=obj.Name,
        depends_on=[base_var, tool_var],
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(getattr(obj, "Shape", None)),
    )
    return [unit]


def _translate_multi_fuse(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    """``Part::MultiFuse`` -> fold the Shapes list via ``_pattern_union``."""
    names = _input_names(obj.Shapes)
    if len(names) < 2:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} (need ≥ 2 Shapes; got {len(names)})"
        )
    args = ", ".join(names)
    line = f"{obj.Name} = _pattern_union({args})"
    unit = TranslationUnit(
        var_name=obj.Name,
        label=obj.Label,
        lines=[line],
        comment=f"Part::MultiFuse {obj.Label!r}: {' + '.join(names)}",
        helpers={"_pattern_union"},
    )
    ctx.add_step(
        feature_type="boolean_multifuse",
        feature_name=obj.Name,
        depends_on=names,
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(getattr(obj, "Shape", None)),
    )
    return [unit]


def _translate_multi_common(obj, ctx: TranslationContext) -> list[TranslationUnit]:
    """``Part::MultiCommon`` -> fold the Shapes list via ``_pattern_intersection``."""
    names = _input_names(obj.Shapes)
    if len(names) < 2:
        raise UnsupportedFeatureError(
            obj.TypeId, f"{obj.Label} (need ≥ 2 Shapes; got {len(names)})"
        )
    args = ", ".join(names)
    line = f"{obj.Name} = _pattern_intersection({args})"
    unit = TranslationUnit(
        var_name=obj.Name,
        label=obj.Label,
        lines=[line],
        comment=f"Part::MultiCommon {obj.Label!r}: {' & '.join(names)}",
        helpers={"_pattern_intersection"},
    )
    ctx.add_step(
        feature_type="boolean_multicommon",
        feature_name=obj.Name,
        depends_on=names,
        renamed_from_default=(obj.Label != obj.Name),
        build123d_code=line,
        properties=extract_properties(getattr(obj, "Shape", None)),
    )
    return [unit]


TIER5_BOOLEAN_HANDLERS = {
    "Part::Cut": _translate_cut,
    "Part::Fuse": _translate_fuse,
    "Part::Common": _translate_common,
    "Part::MultiFuse": _translate_multi_fuse,
    "Part::MultiCommon": _translate_multi_common,
}
