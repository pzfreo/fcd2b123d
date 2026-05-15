"""Tier-6 parametric support: Spreadsheet aliases + ExpressionEngine rewriting.

FreeCAD models a parametric design by:
1. A ``Spreadsheet::Sheet`` with named cells (aliases like ``width``, ``height``).
2. Object properties bound to those aliases via ``setExpression()``. The
   binding is recorded in ``obj.ExpressionEngine`` as a list of
   ``(property_path, expression_string)`` tuples.

The translator's job in tier 6 is to:
- Discover the parameter set (and current values) from spreadsheets in the doc.
- For each handler emit, check whether the property in question is bound to
  one of those parameters. If so, emit the variable name instead of the
  literal value.
- Surface the parameter set to the emitter so the resulting module is wrapped
  in a ``def make_part(...)`` with the parameters as kwargs.

v1 scope for expressions: a property bound to ``<<Sheet>>.alias`` directly,
or ``<<Sheet>>.alias`` with optional unit suffix (``mm``, ``deg``, etc.) and
trivial arithmetic (``* literal``, ``/ literal``, ``+ literal``, ``- literal``).
Anything more complex falls back to the literal value (translation still
works, just loses parametricity for that specific property).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Match a single-alias reference: <<Sheet>>.alias_name with optional whitespace
# and trailing unit/arithmetic. Captures the alias name.
_SHEET_REF = re.compile(
    r"<<(?P<sheet>[^>]+)>>\.(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\b"
)


@dataclass
class ParameterSet:
    """Parameter table extracted from a document's Spreadsheet(s)."""

    # alias -> (current value, sheet label). Sheet label kept for diagnostics.
    aliases: dict[str, tuple[float, str]] = field(default_factory=dict)
    referenced: set[str] = field(default_factory=set)

    def value_of(self, alias: str) -> float | None:
        entry = self.aliases.get(alias)
        return entry[0] if entry is not None else None

    def mark_referenced(self, alias: str) -> None:
        self.referenced.add(alias)

    def used_parameters(self) -> list[tuple[str, float]]:
        """Aliases actually referenced by translated properties, in stable order."""
        return [
            (alias, self.aliases[alias][0])
            for alias in sorted(self.referenced)
            if alias in self.aliases
        ]


def extract_parameters(doc) -> ParameterSet:
    """Walk a FreeCAD document and collect all spreadsheet aliases."""
    params = ParameterSet()
    for obj in doc.Objects:
        if obj.TypeId != "Spreadsheet::Sheet":
            continue
        for prop in obj.PropertiesList:
            # Aliased cells expose the alias as a direct attribute on the
            # Sheet object. PropertiesList includes a hidden 'alias_xxx'
            # bookkeeping prop too — skip those and read the actual aliased
            # attribute instead.
            if prop.startswith("cell") or prop.startswith("alias"):
                continue
        for cell in obj.getUsedCells():
            try:
                alias = obj.getAlias(cell)
            except Exception:
                alias = None
            if not alias:
                continue
            value = getattr(obj, alias, None)
            if value is None:
                continue
            # Spreadsheet cell values can be FreeCAD Quantity, int, float, str.
            try:
                if hasattr(value, "Value"):
                    value = float(value.Value)
                else:
                    value = float(value)
            except (TypeError, ValueError):
                continue
            params.aliases[alias] = (value, obj.Label)
    return params


def rewrite_expression(expr: str, params: ParameterSet) -> str | None:
    """Rewrite a FreeCAD ExpressionEngine expression in build123d-friendly form.

    Returns the rewritten expression (as a string suitable for Python emit),
    or None when the expression can't be safely rewritten — in which case
    the handler falls back to emitting the literal value.

    v1 strategy: substitute every ``<<Sheet>>.alias`` occurrence with the bare
    alias name. Strip FreeCAD unit suffixes (``mm``, ``deg``, etc.) — build123d
    treats values as their natural unit (mm for length, deg for angle).
    Reject the expression if any alias is unknown (we can't substitute what
    we don't have).
    """
    # Strip FreeCAD unit suffixes (build123d treats lengths as mm natively).
    stripped = re.sub(r"\b(mm|deg|rad|cm|m)\b", "", expr).strip()

    # Replace each <<Sheet>>.alias with the bare alias (tracking which were
    # referenced for the function signature).
    rewritten = _SHEET_REF.sub(lambda m: _resolve_alias(m, params), stripped)
    if rewritten is None:
        return None  # an unknown alias was referenced

    # After substitution the expression must contain only known parameter
    # names, numbers, and arithmetic. If any other identifier survives (e.g.
    # ``Sketch.Constraints.Length`` — a reference to a Sketcher constraint),
    # we can't emit it safely; fall back to the literal value.
    known = set(params.aliases.keys())
    for ident in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", rewritten):
        if ident not in known:
            return None
    return rewritten if rewritten else None


def _resolve_alias(m, params: ParameterSet) -> str:
    alias = m.group("alias")
    if alias not in params.aliases:
        # Signal "unknown" via a sentinel the regex.sub callback will surface
        # — re.sub doesn't have a clean abort path so we propagate through
        # the post-substitution scan above. Mark the alias as missing to
        # ensure rewrite_expression bails.
        params._unknown_seen = True  # type: ignore[attr-defined]
        return alias  # the post-substitution scan rejects it as unknown
    params.mark_referenced(alias)
    return alias


def resolve_property(obj, prop_name: str, params: ParameterSet) -> str | None:
    """Look up obj.<prop_name> in its ExpressionEngine. Return the rewritten
    expression if the property is parametric, else None.

    The handler then chooses: if a string is returned, emit it as-is; if None,
    fall back to the literal value formatting.
    """
    try:
        bindings = list(obj.ExpressionEngine)
    except Exception:
        return None
    for binding_prop, expr in bindings:
        if binding_prop == prop_name:
            return rewrite_expression(expr, params)
    return None
