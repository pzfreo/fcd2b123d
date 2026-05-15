"""Code-emission primitives: TranslationUnit and the module renderer.

Pure Python — does not import FreeCAD or build123d. The translator's job is
to produce TranslationUnits; the emitter's job is to format them into a
clean .py source.
"""

from __future__ import annotations

import re
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
    "_edges_at": """def _edges_at(
    shape: Any,
    points: list[tuple[float, float, float]],
    tol: float = 1e-3,
) -> list[Edge]:
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
    "_pattern_union": """def _pattern_union(base, *additions):
    \"\"\"Boolean-union ``base`` with each addition via BuildPart.

    Chained ``+`` on build123d Part objects returns a Compound that does not
    fuse overlapping geometry — so for pattern features whose copies overlap
    (e.g. sprocket teeth meeting at the hub) the resulting volume is wrong.
    Routing through BuildPart.add() invokes OCCT's robust boolean fusion.
    \"\"\"
    from build123d import BuildPart, add
    with BuildPart() as _bp:
        add(base)
        for s in additions:
            add(s)
    return _bp.part""",
    "_pattern_difference": """def _pattern_difference(base, *removals):
    \"\"\"Boolean-subtract each removal from ``base`` via BuildPart.

    Mirror of ``_pattern_union`` for subtractive (Pocket Original) patterns.
    Chained ``-`` does *not* exhibit the same Compound-collapsing bug as
    ``+`` in current build123d, but using BuildPart for both keeps the emit
    symmetric and future-proofs against the inverse issue.
    \"\"\"
    from build123d import BuildPart, Mode, add
    with BuildPart() as _bp:
        add(base)
        for s in removals:
            add(s, mode=Mode.SUBTRACT)
    return _bp.part""",
    "_pattern_intersection": """def _pattern_intersection(base, *others):
    \"\"\"Boolean-intersect ``base`` with each other shape via BuildPart.

    Used by Part::Common / Part::MultiCommon translation. The ``&``
    operator returns a Compound that doesn't always behave correctly for
    multi-shape intersections; BuildPart with Mode.INTERSECT routes
    through OCCT's robust intersection.
    \"\"\"
    from build123d import BuildPart, Mode, add
    with BuildPart() as _bp:
        add(base)
        for s in others:
            add(s, mode=Mode.INTERSECT)
    return _bp.part""",
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
    # Helpers that need extra build123d names for their type annotations.
    if "_edges_at" in helpers:
        imports.add("Edge")
    import_line = f"from build123d import {', '.join(sorted(imports))}"
    # Extra stdlib imports for helper type annotations.
    typing_import_line = (
        "from typing import Any" if "_edges_at" in helpers else ""
    )

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
        + (f"{typing_import_line}\n" if typing_import_line else "")
        + f"\n"
        + helper_block
        + _assemble_body(body_lines, final_var, used_params)
    )
    return _format(_snake_case_pass(raw))


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


# Build123d names that would shadow if a translated variable adopts the same
# snake_case identifier. When a FreeCAD object name like "Fillet" maps to
# "fillet" and that's the imported function, the variable is suffixed "_0"
# so subsequent calls to the function still resolve correctly.
_B123D_FUNCTION_IMPORTS = frozenset({
    "extrude", "revolve", "fillet", "chamfer", "mirror", "make_face",
    "add", "loft", "sweep",
})


_PASCAL_RE = re.compile(r"([a-z])([A-Z])")
_TRAILING_DIGITS_RE = re.compile(r"([a-zA-Z])(\d+)")
_ASSIGN_TARGET_RE = re.compile(r"^[ \t]*([A-Z][A-Za-z0-9_]*)\s*=", re.MULTILINE)


def _snake_case(freecad_name: str) -> str:
    """Convert a FreeCAD PascalCase identifier to Python snake_case.

    Examples:
        Sketch         -> sketch
        Sketch001      -> sketch_001  (preserves the FreeCAD ordinal padding)
        Pad            -> pad
        LinearPattern  -> linear_pattern
        Fillet001      -> fillet_001
    """
    s = _PASCAL_RE.sub(r"\1_\2", freecad_name)
    s = _TRAILING_DIGITS_RE.sub(r"\1_\2", s)
    return s.lower()


_IMPORT_RE = re.compile(r"^from build123d import\s+(.+)$", re.MULTILINE)


def _snake_case_pass(source: str) -> str:
    """Post-pass: rename PascalCase variables to snake_case throughout source.

    Collects every assignment target whose identifier starts with an uppercase
    letter (FreeCAD's auto-naming convention). Builds a rename map with
    collision-avoidance against build123d's imported function names, then
    replaces word-boundary occurrences on non-comment, non-docstring lines so
    the per-feature comment retains the original FreeCAD label for grep-back.

    Names that match a build123d import (``Sketch``, ``Box``, etc.) are
    NEVER renamed even when a FreeCAD object happens to share the same
    PascalCase identifier — that would shadow the class constructor and
    break the emit.
    """
    targets: set[str] = set()
    for line in source.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith('"""'):
            continue
        m = _ASSIGN_TARGET_RE.match(line)
        if m:
            targets.add(m.group(1))
    if not targets:
        return source

    rename: dict[str, str] = {}
    for t in targets:
        snake = _snake_case(t)
        if snake in _B123D_FUNCTION_IMPORTS:
            snake = snake + "_0"
        # Pathological: two FreeCAD names mapping to the same snake form.
        # Disambiguate by appending an underscore until unique.
        while snake in rename.values():
            snake = snake + "_"
        rename[t] = snake

    out_lines: list[str] = []
    for line in source.split("\n"):
        stripped = line.lstrip()
        # Preserve comments, docstrings, and import lines exactly. Import
        # lines reference build123d's PascalCase class names (Sketch, Part,
        # Plane, Box, ...) which must not be renamed even if a translated
        # variable happens to share the same FreeCAD identifier.
        if (
            stripped.startswith("#")
            or stripped.startswith('"""')
            or stripped.startswith("from ")
            or stripped.startswith("import ")
        ):
            out_lines.append(line)
            continue
        new = line
        for orig, snake in rename.items():
            # Skip constructor calls — ``Sketch(`` (the build123d class) must
            # not get renamed even if a FreeCAD object also named ``Sketch``
            # is in the rename map. The negative lookahead ``(?!\()`` keeps
            # class calls intact while still renaming bare references.
            new = re.sub(r"\b" + re.escape(orig) + r"\b(?!\()", snake, new)
        out_lines.append(new)
    return "\n".join(out_lines)
