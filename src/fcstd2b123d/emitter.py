"""Code-emission primitives: TranslationUnit and the module renderer.

Pure Python — does not import FreeCAD or build123d. The translator's job is
to produce TranslationUnits; the emitter's job is to format them into a
clean .py source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def format_value(v) -> str:
    """Render a float (or pass-through a parametric expression) as a literal.

    When ``v`` is a string, it's already a Python expression (typically a
    parameter name from tier-6 spreadsheet rewriting) — pass through.

    For floats: snap FP-roundoff noise (``-19.999999999993534`` → ``-20``)
    but never snap real computed values. The arc parameter ``270.000035`` is
    a real solver-computed angle that lands near 270° to satisfy sketch
    constraints; snapping it would shift the arc's endpoint by ~50 µm and
    break closed-wire continuity. So: snap only at FP-roundoff scale
    (≤ 1e-9 absolute, or 1e-12 relative).
    """
    if isinstance(v, str):
        return v
    if abs(v) < 1e-12:
        return "0"
    nearest = round(v)
    tol = max(1e-9, abs(v) * 1e-12)
    if abs(v - nearest) < tol:
        return f"{int(nearest)}"
    return repr(v)


def vfmt(*values) -> str:
    """Comma-join formatted floats (used in primitive constructor emits)."""
    return ", ".join(format_value(v) for v in values)


def half_expr(v) -> str:
    """Format ``v / 2`` as either a literal (if numeric) or expression (if parametric)."""
    if isinstance(v, str):
        return f"{v} / 2"
    return format_value(v / 2)


def add_expr(a, b) -> str:
    """Format ``a + b`` as either a literal or an expression string.

    Elides a zero operand so a placement offset of 0 + parametric_half
    renders as just ``parametric_half`` rather than ``0 + parametric_half``.
    """
    a_str = isinstance(a, str)
    b_str = isinstance(b, str)
    if not a_str and a == 0:
        return format_value(b)
    if not b_str and b == 0:
        return format_value(a)
    if a_str or b_str:
        return f"{format_value(a)} + {format_value(b)}"
    return format_value(a + b)


# Module-level helpers emitted at the top of the generated source when
# referenced. Keyed by name in TranslationUnit.helpers. Definitions live
# here so the same helper is emitted at most once per module.
HELPER_DEFINITIONS = {
    "_edges_at": """def _edges_at(shape, points, tol=1e-3):
    \"\"\"Select edges whose midpoints match any of the target points.

    Used by translated Fillet / Chamfer features — FreeCAD references edges
    by index, the translator captures their world-frame midpoints from
    FreeCAD's evaluated BRep, and this helper finds the corresponding
    edges in build123d's BRep.
    \"\"\"
    from build123d import Vector
    targets = [Vector(*p) for p in points]
    return [
        e for e in shape.edges()
        if any((e.position_at(0.5) - t).length < tol for t in targets)
    ]""",
}


@dataclass
class TranslationUnit:
    """One translated FreeCAD object.

    Attributes:
        var_name: a valid Python identifier the emitter assigns the result to.
        imports: build123d names to import (e.g. {"Box", "Pos"}).
        lines: the executable statement(s). Conventionally a single assignment
            to ``var_name``, but may include intermediate locals when needed.
        comment: a one-line provenance comment placed above the lines.
        helpers: names of module-level helpers required (see HELPER_DEFINITIONS).
    """

    var_name: str
    imports: set[str] = field(default_factory=set)
    lines: list[str] = field(default_factory=list)
    comment: str = ""
    helpers: set[str] = field(default_factory=set)


def render_module(
    units: list[TranslationUnit],
    source_path: Path | str,
    parameters: object | None = None,
) -> str:
    """Render translation units into a complete build123d Python module.

    The last unit's ``var_name`` is also aliased to ``result`` so callers
    (and the test harness) have a stable name for the translated geometry.

    Output is piped through black so emitted source is consistently
    formatted regardless of how individual translators construct strings.
    """
    if not units:
        raise ValueError("No translation units to render.")

    imports: set[str] = set()
    helpers: set[str] = set()
    for u in units:
        imports.update(u.imports)
        helpers.update(u.helpers)
    import_line = f"from build123d import {', '.join(sorted(imports))}"

    body_lines: list[str] = []
    for u in units:
        if u.comment:
            body_lines.append(f"# {u.comment}")
        body_lines.extend(u.lines)
        body_lines.append("")

    helper_block = ""
    if helpers:
        helper_block = "\n\n".join(
            HELPER_DEFINITIONS[h] for h in sorted(helpers) if h in HELPER_DEFINITIONS
        ) + "\n\n"

    final_var = units[-1].var_name

    used_params = []
    if parameters is not None:
        try:
            used_params = parameters.used_parameters()
        except AttributeError:
            used_params = []

    raw = (
        f'"""Auto-generated by fcstd2b123d from {source_path}."""\n'
        f"{import_line}\n"
        f"\n"
        + helper_block
        + _assemble_body(body_lines, final_var, used_params)
    )
    return _format(raw)


def _assemble_body(
    body_lines: list[str],
    final_var: str,
    used_params: list[tuple[str, float]],
) -> str:
    """Either wrap the body in a parametric function or leave it at module level.

    When ``used_params`` is non-empty (the model references Spreadsheet
    aliases), wrap so a downstream consumer can call ``make_part(width=…)``
    to produce a variant. Module-level ``result = make_part()`` keeps the
    test-harness contract.
    """
    if not used_params:
        return "\n".join(body_lines) + f"\nresult = {final_var}\n"

    signature = ", ".join(f"{name}={format_value(v)}" for name, v in used_params)
    indented = "\n".join("    " + line if line.strip() else "" for line in body_lines)
    return (
        f"def make_part({signature}):\n"
        f'    """Translated parametric design. Defaults match the source values."""\n'
        f"{indented}\n"
        f"    return {final_var}\n"
        f"\n"
        f"\n"
        f"result = make_part()\n"
    )


def _format(source: str) -> str:
    import black

    return black.format_str(source, mode=black.FileMode())
