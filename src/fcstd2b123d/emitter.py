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

    For floats: snap FP-roundoff noise but never snap real computed values.
    Two layers of snap, both at FP-roundoff scale (sub-nanometre shifts
    that can't affect any geometric tolerance):

    1. **Near-integer**: ``-19.999999999993534`` → ``-20``. Tight
       relative tolerance (1e-12), works for any-magnitude integer.
    2. **Near-N-decimal-place**: ``4.249999999941`` → ``4.25``,
       ``4.222289999941`` → ``4.22229``. Used for values that aren't
       near an integer — sketch coordinates often land at a few-decimal
       value with FP-roundoff noise in trailing digits. Absolute
       tolerance (1e-9) — only snaps when the noise is at the FP-
       precision frontier, never the solver-shifted "55 vs 54.999978"
       case (that one's handled coherently by
       :mod:`fcstd2b123d.sketch_snap` instead, where shared endpoints
       are tracked).

    The solver-noise example from the original docstring (``270.000035``
    landing near 270° to satisfy constraints) is **NOT** caught here —
    its shift is too large (~3e-5 rel err) to be FP roundoff. It's
    handled in the sketch-level snap pass (#43) which also propagates
    the snap to coincident endpoints.
    """
    if isinstance(v, str):
        return v
    if abs(v) < 1e-12:
        return "0"
    nearest = round(v)
    tol = max(1e-9, abs(v) * 1e-12)
    if abs(v - nearest) < tol:
        return f"{int(nearest)}"
    # FP-roundoff snap for near-decimal-place values. Try rounding to
    # 1..8 decimal places — the first that's within 1e-9 absolute is the
    # snapped value. Stops at 8 places (FP precision frontier; rounding
    # past that would just round to v itself).
    for places in range(1, 9):
        rounded = round(v, places)
        if abs(v - rounded) < 1e-9 and rounded != v:
            return repr(rounded)
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
    "_faces_at": """def _faces_at(
    shape: Any,
    points: list[tuple[float, float, float]],
    tol: float = 1e-3,
) -> list[Face]:
    \"\"\"Select faces whose centres match any of the target points.

    Companion to ``_edges_at`` for face-based features like Draft. The
    translator captures FreeCAD's referenced face centres in world frame
    and emits this lookup; build123d returns the matching faces of its
    own BRep so the operation re-targets correctly.
    \"\"\"
    from build123d import Vector
    targets = [Vector(*p) for p in points]
    return [
        f for f in shape.faces()
        if any((f.center() - t).length < tol for t in targets)
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
        label: the FreeCAD ``Label`` (human-set name) of the source object,
            when the user renamed it. Empty when ``Label == Name`` (default
            auto-name). The snake_case post-pass prefers this over Name when
            it sanitises to a valid Python identifier — so ``Sketch001``
            with Label ``"Hex recess"`` becomes ``hex_recess`` rather than
            ``sketch_001``.
    """

    var_name: str
    imports: set[str] = field(default_factory=set)
    lines: list[str] = field(default_factory=list)
    comment: str = ""
    helpers: set[str] = field(default_factory=set)
    label: str = ""


def render_module(
    units: list[TranslationUnit],
    source_path: Path | str,
    parameters: object | None = None,
    doc_description: str | None = None,
    shared_helpers: bool = False,
) -> str:
    """Render translation units into a complete build123d Python module.

    The last unit's ``var_name`` is also aliased to ``result`` so callers
    (and the test harness) have a stable name for the translated geometry.

    ``doc_description`` is the FreeCAD ``Document.Label`` /
    ``Document.Comment`` when set — promoted to the module docstring so
    a renamed file like ``"M5 socket head cap screw, ISO 4762"`` reads
    as documentation rather than an auto-generated path.

    ``shared_helpers``: when True, emit
    ``from fcstd2b123d.runtime import _edges_at, …`` instead of
    inlining the helper ``def`` blocks. The runtime module is part
    of the installed package; this trades self-contained output for
    a 20-40 line saving per file. Default ``False`` preserves the
    "open file, no install required" property for one-off translations.

    Output is piped through black so emitted source is consistently
    formatted regardless of how individual translators construct strings.
    """
    if not units:
        raise ValueError("No translation units to render.")

    imports: set[str] = set()
    helpers: set[str] = set()
    label_map: dict[str, str] = {}
    for u in units:
        imports.update(u.imports)
        helpers.update(u.helpers)
        if u.label and u.label != u.var_name:
            label_map[u.var_name] = u.label
    # When helpers are inlined, the helper type annotations reference
    # ``Edge`` / ``Face`` / ``Any`` — pull them into the imports so
    # the inlined definitions type-check. With ``shared_helpers``, the
    # helpers live in ``fcstd2b123d.runtime`` and bring their own
    # annotations; user code only needs the build123d names it
    # explicitly references.
    if not shared_helpers:
        if "_edges_at" in helpers:
            imports.add("Edge")
        if "_faces_at" in helpers:
            imports.add("Face")
    import_line = f"from build123d import {', '.join(sorted(imports))}"
    typing_import_line = (
        "from typing import Any"
        if (not shared_helpers and ("_edges_at" in helpers or "_faces_at" in helpers))
        else ""
    )

    body_lines: list[str] = []
    for u in units:
        if u.comment:
            body_lines.append(f"# {u.comment}")
        body_lines.extend(u.lines)
        body_lines.append("")

    helper_block = ""
    runtime_import_line = ""
    if helpers:
        helper_names = sorted(h for h in helpers if h in HELPER_DEFINITIONS)
        if shared_helpers:
            runtime_import_line = (
                f"from fcstd2b123d.runtime import {', '.join(helper_names)}"
            )
        else:
            helper_block = "\n\n".join(
                HELPER_DEFINITIONS[h] for h in helper_names
            ) + "\n\n"

    final_var = units[-1].var_name

    used_params = []
    if parameters is not None:
        try:
            used_params = parameters.used_parameters()
        except AttributeError:
            used_params = []

    docstring = _module_docstring(doc_description, source_path)
    raw = (
        f"{docstring}\n"
        f"{import_line}\n"
        + (f"{typing_import_line}\n" if typing_import_line else "")
        + (f"{runtime_import_line}\n" if runtime_import_line else "")
        + f"\n"
        + helper_block
        + _assemble_body(body_lines, final_var, used_params)
    )
    return _format(_snake_case_pass(raw, label_map=label_map))


def _module_docstring(description: str | None, source_path: Path | str) -> str:
    """Build the module docstring.

    Prefers a FreeCAD ``Document.Label`` / ``Document.Comment`` when the
    user has set one (matching bd_warehouse-style "what this part is"
    descriptions); otherwise falls back to the
    ``Auto-generated by ...`` provenance line.
    """
    if description:
        sanitized = description.replace('"""', '\\"\\"\\"').strip()
        if "\n" in sanitized:
            return (
                f'"""{sanitized.splitlines()[0]}\n\n'
                f"{chr(10).join(sanitized.splitlines()[1:])}\n\n"
                f'Auto-generated by fcstd2b123d from {source_path}.\n"""'
            )
        return (
            f'"""{sanitized}\n\n'
            f'Auto-generated by fcstd2b123d from {source_path}.\n"""'
        )
    return f'"""Auto-generated by fcstd2b123d from {source_path}."""'


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
    "add", "loft", "sweep", "draft",
})


_PASCAL_RE = re.compile(r"([a-z])([A-Z])")
_TRAILING_DIGITS_RE = re.compile(r"([a-zA-Z])(\d+)")
_ASSIGN_TARGET_RE = re.compile(r"^[ \t]*([A-Z][A-Za-z0-9_]*)\s*=", re.MULTILINE)
# Builder-mode emits ``with BuildSketch() as Sketch001:`` — the ``Sketch001``
# is a target the post-pass should rename to snake_case alongside regular
# assignments. Matches ``as <PascalName>`` followed by ``:`` or end of line.
_AS_TARGET_RE = re.compile(r"\bas\s+([A-Z][A-Za-z0-9_]*)\s*[:\n]", re.MULTILINE)


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


# Labels coming from FreeCAD can contain spaces, hyphens, parentheses,
# unicode dashes, dots, slashes — anything a user typed in the tree.
# Sanitise to a Python identifier: alphanum + underscore, no leading
# digit, collapsed underscores. Empty result → fall back to the
# Name-derived snake_case.
_LABEL_SEP_RE = re.compile(r"[\s\-./()\[\]{}+,]+")
_LABEL_KEEP_RE = re.compile(r"[^A-Za-z0-9_]")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _label_to_identifier(label: str) -> str:
    """Sanitise a FreeCAD ``Label`` to a Python identifier (or "" if not usable).

    Spaces / hyphens / slashes / parens collapse to underscores; anything
    else non-alphanumeric is dropped; runs of underscores collapse;
    leading / trailing underscores stripped; result is lowercased so the
    snake-case post-pass treats it like other identifiers. A leading digit
    after sanitisation makes the result invalid — return ``""`` so the
    caller falls back to the Name-derived snake_case.
    """
    if not label:
        return ""
    s = _LABEL_SEP_RE.sub("_", label.strip())
    s = _LABEL_KEEP_RE.sub("", s)
    s = _MULTI_UNDERSCORE_RE.sub("_", s).strip("_")
    if not s:
        return ""
    if s[0].isdigit():
        return ""
    return s.lower()


_IMPORT_RE = re.compile(r"^from build123d import\s+(.+)$", re.MULTILINE)


def _snake_case_pass(source: str, label_map: dict[str, str] | None = None) -> str:
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
        for m in _AS_TARGET_RE.finditer(line):
            targets.add(m.group(1))
    if not targets:
        return source

    rename: dict[str, str] = {}
    # Stable ordering so collision-disambiguation is deterministic:
    # objects emitted earlier in the source keep their preferred name;
    # later collisions get the underscore suffix.
    targets_in_order: list[str] = []
    seen: set[str] = set()
    for line in source.split("\n"):
        m = _ASSIGN_TARGET_RE.match(line)
        if m and m.group(1) in targets and m.group(1) not in seen:
            targets_in_order.append(m.group(1))
            seen.add(m.group(1))
        for m in _AS_TARGET_RE.finditer(line):
            if m.group(1) in targets and m.group(1) not in seen:
                targets_in_order.append(m.group(1))
                seen.add(m.group(1))
    label_map = label_map or {}
    for t in targets_in_order:
        # Three-step name resolution:
        #   1. t itself is Label-mapped → use the Label.
        #   2. t is derivative of a Label-mapped parent (``<parent>_<suffix>``)
        #      → use the parent's Label + ``_`` + suffix. Catches helper
        #      vars like ``Sketch001_profile`` when the parent sketch is
        #      Label-renamed to ``Hexagon``: result is ``hexagon_profile``.
        #   3. Fall back to ``_snake_case`` of the Name.
        snake = ""
        label = label_map.get(t, "")
        if label:
            snake = _label_to_identifier(label)
        if not snake and "_" in t:
            parent = t[: t.rfind("_")]
            suffix = t[t.rfind("_") + 1 :]
            parent_label = label_map.get(parent, "")
            if parent_label:
                parent_snake = _label_to_identifier(parent_label)
                if parent_snake:
                    snake = f"{parent_snake}_{_snake_case(suffix)}"
        if not snake:
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
