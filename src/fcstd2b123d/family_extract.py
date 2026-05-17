"""Family extraction — manifest + N fixtures → one parametric class.

Phase 3 of the family-extraction architecture
(``docs/design/family-extraction.md``). Reads a YAML family manifest
(:mod:`fcstd2b123d.family`), translates each matching fixture via
``--emit=class`` (:mod:`fcstd2b123d.translator`), then diffs the N
resulting class bodies to find which numeric literals correspond to
declared parameters. Emits a single parametric class.

CLI: ``python -m fcstd2b123d.family_extract <manifest.yaml>``.

Algorithm overview
==================

For each fixture matching the manifest's ``fixture_glob``:
  1. Extract pre-translation params from filename (regex) and
     constant declarations.
  2. Translate via the per-fixture ``--emit=class`` pipeline.
  3. Parse the result with :mod:`ast` to get the class definition.
  4. Extract ``extrude_amount`` params from the translation output.

Then, walking the N specific-instance class ASTs in parallel:
  5. Identify every numeric literal in each AST.
  6. For each literal-position, attempt to express the value as a
     simple function of declared parameters (literal equality, half,
     double, negation). The substitution must hold across ALL N
     fixtures.
  7. Replace matching literals with parameter references; constants
     stay as-is.
  8. Emit a single parametric class whose ``__init__`` accepts the
     declared parameters.

Bounded by design: only **simple linear substitutions** are tried
(``p``, ``-p``, ``p/2``, ``-p/2``, ``p*2``, sum of two params).
Complex expressions like ``width * cos(angle)`` fall outside the
current scope; the algorithm fails loudly so the manifest author
knows to either extend the algorithm or restructure the source.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .family import FamilyManifest, ParameterDecl, load_manifest


# ---------------------------------------------------------------------------
# Fixture-set discovery and per-fixture param extraction
# ---------------------------------------------------------------------------


@dataclass
class FixtureRecord:
    """One fixture's pre-translation context: path + parameter values."""

    path: Path
    params: dict[str, float | int | str]


def discover_fixtures(
    manifest: FamilyManifest, fixtures_root: Path
) -> list[FixtureRecord]:
    """Glob the fixtures matching the manifest and extract per-fixture
    pre-translation parameters (from filename regex + constant defaults).

    Spreadsheet and extrude_amount sources are filled in post-translation.
    """
    fixture_paths = sorted(fixtures_root.rglob(manifest.fixture_glob))
    if not fixture_paths:
        raise FamilyExtractionError(
            f"no fixtures match {manifest.fixture_glob!r} under {fixtures_root}"
        )

    records: list[FixtureRecord] = []
    pattern = (
        re.compile(manifest.filename_pattern)
        if manifest.filename_pattern
        else None
    )

    for path in fixture_paths:
        params: dict[str, float | int | str] = {}
        for p in manifest.parameters:
            if p.source == "constant":
                params[p.name] = p.default
            elif p.source == "filename":
                if pattern is None:
                    raise FamilyExtractionError(
                        f"parameter {p.name!r} has source=filename but the "
                        f"manifest has no filename_pattern"
                    )
                m = pattern.search(path.name)
                if m is None or p.name not in m.groupdict():
                    raise FamilyExtractionError(
                        f"filename_pattern did not match {path.name!r} for "
                        f"parameter {p.name!r}"
                    )
                params[p.name] = _coerce_value(m.group(p.name), p.type)
            # spreadsheet/extrude_amount/lookup handled below or later.

        # Lookup params: resolved from dimensions_table once the
        # lookup_key value is known. (lookup_key is itself a filename
        # param, already populated above.)
        lookup_params = [p for p in manifest.parameters if p.source == "lookup"]
        if lookup_params:
            assert manifest.dimensions_table is not None
            assert manifest.lookup_key is not None
            key_value = params.get(manifest.lookup_key)
            if key_value is None:
                raise FamilyExtractionError(
                    f"lookup_key {manifest.lookup_key!r} has no value for "
                    f"{path.name}; cannot resolve lookup parameters"
                )
            # Stringify for the table (YAML keys may be int or str).
            key_str = str(int(float(key_value))) if isinstance(key_value, (int, float)) else str(key_value)
            if key_str not in manifest.dimensions_table:
                raise FamilyExtractionError(
                    f"dimensions_table has no entry for "
                    f"{manifest.lookup_key}={key_str!r} (fixture {path.name}). "
                    f"Available keys: {sorted(manifest.dimensions_table.keys())}"
                )
            row = manifest.dimensions_table[key_str]
            for p in lookup_params:
                if p.name not in row:
                    raise FamilyExtractionError(
                        f"dimensions_table[{key_str!r}] missing param {p.name!r}"
                    )
                params[p.name] = _coerce_value(str(row[p.name]), p.type)

        records.append(FixtureRecord(path=path, params=params))
    return records


def _coerce_value(s: str, kind: str) -> float | int | str:
    if kind == "int":
        return int(s)
    if kind == "float":
        return float(s)
    return s


# ---------------------------------------------------------------------------
# Translation orchestration
# ---------------------------------------------------------------------------


def translate_fixture_class(fixture_path: Path) -> str:
    """Translate a fixture with ``--emit=class`` and return the source."""
    py = os.environ.get("FCSTD2B123D_FREECAD_PYTHON")
    if not py:
        raise FamilyExtractionError(
            "FCSTD2B123D_FREECAD_PYTHON env var not set — needed to "
            "translate fixtures"
        )
    fc_pp = os.environ.get("FCSTD2B123D_FREECAD_PYTHONPATH", "")
    repo_src = str(Path(__file__).resolve().parent.parent)
    pythonpath = ":".join(p for p in (repo_src, fc_pp) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}
    result = subprocess.run(
        [py, "-m", "fcstd2b123d", "--emit", "class", str(fixture_path)],
        capture_output=True, text=True, env=env, check=False,
    )
    if result.returncode != 0:
        raise FamilyExtractionError(
            f"translator failed on {fixture_path.name}:\n"
            f"stdout:\n{result.stdout[-500:]}\nstderr:\n{result.stderr[-500:]}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# AST parsing — find the body inside the generated class' __init__
# ---------------------------------------------------------------------------


def find_init_body(source: str) -> tuple[ast.Module, ast.ClassDef, ast.FunctionDef]:
    """Locate the ``class Foo(BasePartObject): def __init__(self, …): <body>``
    structure in a translator emit. Return (module, class, init_func)."""
    module = ast.parse(source)
    classes = [n for n in module.body if isinstance(n, ast.ClassDef)]
    if not classes:
        raise FamilyExtractionError("emitted source has no class definition")
    if len(classes) > 1:
        raise FamilyExtractionError(
            f"emitted source has multiple class definitions ({len(classes)})"
            " — only single-class emits are supported"
        )
    cls = classes[0]
    inits = [
        n for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    ]
    if not inits:
        raise FamilyExtractionError(
            f"class {cls.name} has no __init__ method"
        )
    return module, cls, inits[0]


# ---------------------------------------------------------------------------
# Numeric-literal walk + substitution inference
# ---------------------------------------------------------------------------


@dataclass
class LiteralSite:
    """One numeric-literal position, with the values seen across fixtures."""

    path: tuple[int, ...]  # AST index path from __init__ root
    values: list[float]    # per-fixture values, parallel to fixtures list


def collect_numeric_literals(
    init_body_nodes: list[ast.stmt],
) -> list[tuple[tuple[int, ...], float]]:
    """Walk the body, returning (ast_path, value) for every numeric
    Constant. The path is a sequence of (statement_index, attr_index,
    ...) walks from the body root."""
    out: list[tuple[tuple[int, ...], float]] = []

    def walk(node: ast.AST, path: tuple[int, ...]) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            # Skip bool (bool is a subclass of int).
            if isinstance(node.value, bool):
                return
            out.append((path, float(node.value)))
            return
        for i, (field_name, value) in enumerate(ast.iter_fields(node)):
            if isinstance(value, list):
                for j, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        walk(item, path + (i, j))
            elif isinstance(value, ast.AST):
                walk(value, path + (i,))

    # Walk the init body, indexing each statement.
    for stmt_i, stmt in enumerate(init_body_nodes):
        walk(stmt, (stmt_i,))
    return out


def align_literals_across_fixtures(
    per_fixture_literals: list[list[tuple[tuple[int, ...], float]]],
) -> list[LiteralSite]:
    """Verify all fixtures have the same set of AST paths, then collect
    per-position value lists. Fails if ASTs aren't isomorphic at the
    literal-position level."""
    if not per_fixture_literals:
        return []
    reference_paths = [p for p, _ in per_fixture_literals[0]]
    for i, lits in enumerate(per_fixture_literals[1:], start=1):
        paths = [p for p, _ in lits]
        if paths != reference_paths:
            raise FamilyExtractionError(
                f"fixture {i} has different AST literal positions than "
                f"fixture 0 — fixtures are not isomorphic (different "
                f"feature counts? different sketch shapes?). Cannot "
                f"extract a single parametric class."
            )
    sites: list[LiteralSite] = []
    for col, path in enumerate(reference_paths):
        values = [per_fixture_literals[r][col][1] for r in range(len(per_fixture_literals))]
        sites.append(LiteralSite(path=path, values=values))
    return sites


def infer_substitution(
    values: list[float],
    params_per_fixture: list[dict[str, float | int | str]],
) -> ast.AST | None:
    """Try simple substitution patterns. Return the AST node that
    replaces this literal (or None to keep as a literal).

    Patterns tried, in order:
      * exact match to a param's value → ``ast.Name(id=p)``
      * exact match to -param → ``-p``
      * exact match to param/2 → ``p / 2``
      * exact match to -param/2 → ``-p / 2``
      * exact match to param*2 → ``p * 2``
      * none → keep as literal

    Note: substitution wins even when the literal is constant across
    all fixtures, *if* it matches a declared parameter. Otherwise a
    parameter the manifest declared (e.g. ``length`` with
    ``source: extrude_amount, default: 50``) would never appear in
    the body when all fixtures happen to share that value.
    """
    if not params_per_fixture:
        return None
    param_names = list(params_per_fixture[0].keys())

    def matches(pred) -> str | None:
        """Apply the predicate to (value, param_name → value) for each
        fixture; return the first param_name for which it holds across
        every fixture, or None."""
        for name in param_names:
            ok = True
            for value, params in zip(values, params_per_fixture):
                pv = params.get(name)
                if not isinstance(pv, (int, float)):
                    ok = False
                    break
                if not pred(value, float(pv)):
                    ok = False
                    break
            if ok:
                return name
        return None

    # Try the simple linear forms in order. Tight tolerance — float
    # comparisons need slack for legitimate solver-noise.
    tol = 1e-9

    name = matches(lambda v, p: abs(v - p) < tol)
    if name is not None:
        return ast.Name(id=name, ctx=ast.Load())
    name = matches(lambda v, p: abs(v + p) < tol)
    if name is not None:
        return _neg(ast.Name(id=name, ctx=ast.Load()))
    name = matches(lambda v, p: abs(v - p / 2) < tol)
    if name is not None:
        return ast.BinOp(
            left=ast.Name(id=name, ctx=ast.Load()),
            op=ast.Div(), right=ast.Constant(value=2),
        )
    name = matches(lambda v, p: abs(v + p / 2) < tol)
    if name is not None:
        return _neg(
            ast.BinOp(
                left=ast.Name(id=name, ctx=ast.Load()),
                op=ast.Div(), right=ast.Constant(value=2),
            )
        )
    name = matches(lambda v, p: abs(v - p * 2) < tol)
    if name is not None:
        return ast.BinOp(
            left=ast.Name(id=name, ctx=ast.Load()),
            op=ast.Mult(), right=ast.Constant(value=2),
        )

    # ---- Two-param forms: p1 ± p2 and p1/2 ± p2 (and their negations).
    #
    # I-beam corners need these (h/2 - tf, tw/2 + r, etc.). Tries every
    # ordered pair of declared params.
    def matches2(pred) -> tuple[str, str] | None:
        for n1 in param_names:
            for n2 in param_names:
                if n1 == n2:
                    continue
                ok = True
                for value, params in zip(values, params_per_fixture):
                    p1 = params.get(n1)
                    p2 = params.get(n2)
                    if not isinstance(p1, (int, float)) or not isinstance(p2, (int, float)):
                        ok = False
                        break
                    if not pred(value, float(p1), float(p2)):
                        ok = False
                        break
                if ok:
                    return n1, n2
        return None

    # p1 + p2 — useful when literal is the sum of two dimensions.
    found = matches2(lambda v, a, b: abs(v - (a + b)) < tol)
    if found:
        n1, n2 = found
        return _add(_name(n1), _name(n2))

    # p1 - p2
    found = matches2(lambda v, a, b: abs(v - (a - b)) < tol)
    if found:
        n1, n2 = found
        return _sub(_name(n1), _name(n2))

    # -(p1 + p2)
    found = matches2(lambda v, a, b: abs(v + (a + b)) < tol)
    if found:
        n1, n2 = found
        return _neg(_add(_name(n1), _name(n2)))

    # -(p1 - p2)
    found = matches2(lambda v, a, b: abs(v + (a - b)) < tol)
    if found:
        n1, n2 = found
        return _neg(_sub(_name(n1), _name(n2)))

    # p1/2 + p2 — e.g. tw/2 + r (web-edge to arc-center)
    found = matches2(lambda v, a, b: abs(v - (a / 2 + b)) < tol)
    if found:
        n1, n2 = found
        return _add(_half(n1), _name(n2))

    # p1/2 - p2 — e.g. h/2 - tf (flange inner edge)
    found = matches2(lambda v, a, b: abs(v - (a / 2 - b)) < tol)
    if found:
        n1, n2 = found
        return _sub(_half(n1), _name(n2))

    # -(p1/2 + p2)
    found = matches2(lambda v, a, b: abs(v + (a / 2 + b)) < tol)
    if found:
        n1, n2 = found
        return _neg(_add(_half(n1), _name(n2)))

    # -(p1/2 - p2) — i.e. p2 - p1/2 mirrored
    found = matches2(lambda v, a, b: abs(v + (a / 2 - b)) < tol)
    if found:
        n1, n2 = found
        return _neg(_sub(_half(n1), _name(n2)))

    # ---- Three-param forms: p1/2 ± p2 ± p3 (and their negations).
    #
    # I-beam arc-center y coordinates need h/2 - tf - r.
    def matches3(pred) -> tuple[str, str, str] | None:
        for n1 in param_names:
            for n2 in param_names:
                if n1 == n2:
                    continue
                for n3 in param_names:
                    if n3 == n1 or n3 == n2:
                        continue
                    ok = True
                    for value, params in zip(values, params_per_fixture):
                        p1 = params.get(n1)
                        p2 = params.get(n2)
                        p3 = params.get(n3)
                        if not all(isinstance(x, (int, float)) for x in (p1, p2, p3)):
                            ok = False
                            break
                        if not pred(value, float(p1), float(p2), float(p3)):
                            ok = False
                            break
                    if ok:
                        return n1, n2, n3
        return None

    # p1/2 - p2 - p3 — h/2 - tf - r (I-beam arc-center y)
    found = matches3(lambda v, a, b, c: abs(v - (a / 2 - b - c)) < tol)
    if found:
        n1, n2, n3 = found
        return _sub(_sub(_half(n1), _name(n2)), _name(n3))

    # -(p1/2 - p2 - p3)
    found = matches3(lambda v, a, b, c: abs(v + (a / 2 - b - c)) < tol)
    if found:
        n1, n2, n3 = found
        return _neg(_sub(_sub(_half(n1), _name(n2)), _name(n3)))

    # p1/2 + p2 + p3 (rarer but symmetric for completeness)
    found = matches3(lambda v, a, b, c: abs(v - (a / 2 + b + c)) < tol)
    if found:
        n1, n2, n3 = found
        return _add(_add(_half(n1), _name(n2)), _name(n3))

    # -(p1/2 + p2 + p3)
    found = matches3(lambda v, a, b, c: abs(v + (a / 2 + b + c)) < tol)
    if found:
        n1, n2, n3 = found
        return _neg(_add(_add(_half(n1), _name(n2)), _name(n3)))

    return None


def _name(n: str) -> ast.Name:
    return ast.Name(id=n, ctx=ast.Load())


def _half(n: str) -> ast.BinOp:
    return ast.BinOp(left=_name(n), op=ast.Div(), right=ast.Constant(value=2))


def _add(a: ast.AST, b: ast.AST) -> ast.BinOp:
    return ast.BinOp(left=a, op=ast.Add(), right=b)


def _sub(a: ast.AST, b: ast.AST) -> ast.BinOp:
    return ast.BinOp(left=a, op=ast.Sub(), right=b)


def _neg(inner: ast.AST) -> ast.UnaryOp:
    return ast.UnaryOp(op=ast.USub(), operand=inner)


# ---------------------------------------------------------------------------
# Apply substitutions to an AST (use a deep walk to replace by path)
# ---------------------------------------------------------------------------


def apply_substitutions(
    init_body_nodes: list[ast.stmt],
    substitutions: dict[tuple[int, ...], ast.AST],
) -> list[ast.stmt]:
    """Return new statement list with literals replaced by substitution
    expressions at the matching AST paths."""

    def replace(node: ast.AST, current_path: tuple[int, ...]) -> ast.AST:
        if current_path in substitutions:
            return substitutions[current_path]
        for i, (field_name, value) in enumerate(ast.iter_fields(node)):
            if isinstance(value, list):
                new_list = []
                for j, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        new_list.append(replace(item, current_path + (i, j)))
                    else:
                        new_list.append(item)
                setattr(node, field_name, new_list)
            elif isinstance(value, ast.AST):
                setattr(node, field_name, replace(value, current_path + (i,)))
        return node

    new_stmts: list[ast.stmt] = []
    for stmt_i, stmt in enumerate(init_body_nodes):
        new_stmts.append(replace(stmt, (stmt_i,)))
    return new_stmts


# ---------------------------------------------------------------------------
# Final emission — render a parametric class from the substituted AST
# ---------------------------------------------------------------------------


def render_parametric_module(
    manifest: FamilyManifest,
    reference_module: ast.Module,
    new_init_body: list[ast.stmt],
    reference_params: dict[str, float | int | str],
) -> str:
    """Take the reference (per-fixture) module, swap in the parametric
    __init__ body, and re-emit. Update the __init__ signature to
    accept the declared parameters.

    ``reference_params`` is the param dict from the first fixture; used
    to fill in a parameter's default value when the manifest doesn't
    declare one explicitly.
    """
    module = ast.parse(ast.unparse(reference_module))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef))
    init = next(
        n for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )

    # Rebuild __init__ args: self, then declared params, then the
    # existing rotation/align/mode kwargs from the reference.
    new_args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg="self", annotation=None)] + [
            ast.arg(arg=p.name, annotation=_type_annotation(p))
            for p in manifest.parameters
        ] + init.args.args[1:],  # preserve rotation/align/mode trio
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[
            _default_node(p, reference_params) for p in manifest.parameters
        ] + init.args.defaults,
    )
    init.args = new_args
    init.body = new_init_body

    # Rename the class to the manifest's declared class_name.
    cls.name = manifest.class_name

    # Update the trailing ``result = <OldName>()`` to use the new class name.
    for i, stmt in enumerate(module.body):
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "result"
        ):
            if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name):
                stmt.value.func.id = manifest.class_name

    source = ast.unparse(module)
    return _format_with_black(source)


def _type_annotation(p: ParameterDecl) -> ast.expr:
    return ast.Name(id=p.type, ctx=ast.Load())


def _default_node(
    p: ParameterDecl,
    reference_params: dict[str, float | int | str],
) -> ast.expr:
    """Build the AST node for a parameter's default value.

    Order of preference: (1) manifest's declared ``default:``,
    (2) the value from the first fixture (the reference).
    """
    if p.default is not None:
        return ast.Constant(value=p.default)
    if p.name in reference_params:
        return ast.Constant(value=reference_params[p.name])
    raise FamilyExtractionError(
        f"parameter {p.name!r} has no manifest default and no value "
        f"extracted from the first fixture — cannot pick an __init__ "
        f"default value"
    )


def _format_with_black(source: str) -> str:
    import black
    return black.format_str(source, mode=black.FileMode())


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


class FamilyExtractionError(RuntimeError):
    """Raised when family extraction can't produce a clean parametric class."""


def extract_family(
    manifest: FamilyManifest,
    fixtures_root: Path,
) -> str:
    """Run the full pipeline: manifest + fixtures → parametric class.

    Returns the generated Python source.
    """
    records = discover_fixtures(manifest, fixtures_root)

    # Translate each fixture and parse the result.
    per_fixture_sources: list[str] = []
    per_fixture_modules: list[ast.Module] = []
    per_fixture_init_bodies: list[list[ast.stmt]] = []
    for rec in records:
        source = translate_fixture_class(rec.path)
        per_fixture_sources.append(source)
        module, _cls, init = find_init_body(source)
        # Drop the trailing super().__init__ call from the diff — it's
        # always the same shape (just plumbs args).
        body = list(init.body)
        # Trim trailing super().__init__(...) from body — recognise it.
        while body and _is_super_init_call(body[-1]):
            body.pop()
        per_fixture_modules.append(module)
        per_fixture_init_bodies.append(body)

    # Fill in extrude_amount params.
    for rec, body in zip(records, per_fixture_init_bodies):
        for p in manifest.parameters:
            if p.source != "extrude_amount":
                continue
            value = _extract_extrude_amount(body)
            if value is None:
                if p.default is not None:
                    rec.params[p.name] = p.default
                else:
                    raise FamilyExtractionError(
                        f"parameter {p.name!r} (source=extrude_amount) had "
                        f"no extrude(..., amount=N) call in {rec.path.name} "
                        f"and no default"
                    )
            else:
                rec.params[p.name] = value
        # For spreadsheet params: TODO — not yet supported here. The
        # canonical EN 10058 manifest doesn't use them.

    # Collect literals from each fixture's init body.
    per_fixture_literals = [collect_numeric_literals(b) for b in per_fixture_init_bodies]
    sites = align_literals_across_fixtures(per_fixture_literals)

    # Infer substitution per site.
    params_per_fixture = [rec.params for rec in records]
    substitutions: dict[tuple[int, ...], ast.AST] = {}
    for site in sites:
        sub = infer_substitution(site.values, params_per_fixture)
        if sub is not None:
            substitutions[site.path] = sub
        elif not all(v == site.values[0] for v in site.values):
            raise FamilyExtractionError(
                f"literal at AST path {site.path} varies across fixtures "
                f"({site.values}) but doesn't match any simple substitution "
                f"of declared parameters {list(params_per_fixture[0])}. "
                f"Either extend the substitution rules or declare an "
                f"additional parameter that captures this variation."
            )

    # Apply substitutions to the first fixture's init body (as the canonical
    # template) and re-emit.
    new_init_body = apply_substitutions(
        per_fixture_init_bodies[0], substitutions
    )

    # Append the super().__init__(...) call back onto the new body.
    # Pull it from the first fixture's original init.
    _mod0, _cls0, init0 = find_init_body(per_fixture_sources[0])
    for stmt in init0.body:
        if _is_super_init_call(stmt):
            new_init_body.append(stmt)
            break

    return render_parametric_module(
        manifest, per_fixture_modules[0], new_init_body,
        reference_params=records[0].params,
    )


def _is_super_init_call(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "__init__"
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
        and func.value.func.id == "super"
    )


def _extract_extrude_amount(body: list[ast.stmt]) -> float | None:
    """Find the first ``extrude(..., amount=N)`` call's amount."""
    for stmt in body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "extrude"
            ):
                for kw in node.keywords:
                    if kw.arg == "amount" and isinstance(kw.value, ast.Constant):
                        if isinstance(kw.value.value, (int, float)):
                            return float(kw.value.value)
    return None


# ---------------------------------------------------------------------------
# CLI: python -m fcstd2b123d.family_extract <manifest.yaml>
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="fcstd2b123d.family_extract",
        description="Generate a parametric build123d class from a family manifest + corpus fixtures.",
    )
    parser.add_argument("manifest", type=Path, help="Path to family manifest YAML")
    parser.add_argument(
        "--fixtures-root", type=Path, default=Path("tests/fixtures"),
        help="Where to look for fixtures matching the manifest's fixture_glob.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output .py path. Omit to write to stdout.",
    )
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    source = extract_family(manifest, fixtures_root=args.fixtures_root)
    if args.output is None:
        sys.stdout.write(source)
    else:
        args.output.write_text(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
