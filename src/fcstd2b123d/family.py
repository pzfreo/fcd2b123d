"""Family manifests — declarative spec of a parametric parts family.

A manifest tells the translator "these N FCStd fixtures share a
parametric family with these named parameters." It's the durable
artifact that captures the semantic judgement the translator can't
make itself (which fixtures belong together, what to call their
parameters, what standard they implement).

Phase 2 (issue #129) of the family-extraction architecture defines
*just* the schema + loader + validator. Phase 3 (issue #130) will
read manifests and produce parametric classes.

See ``docs/design/family-extraction.md`` for the full architecture.

Schema (YAML):

    family: en_10058_flat_bar       # slug; required
    class_name: EN10058FlatBar      # Python class name; required
    standard:                       # required
      ref: EN 10058:2018
      title: Hot rolled flat steel bars for general purposes
      url: https://www.iso.org/...  # optional
    fixture_glob: "**/Flat_Bar_*_EN10058_S235JR.FCStd"  # required
    filename_pattern: "Flat_Bar_(?P<width>\\d+)x(?P<thickness>\\d+)_..."  # optional
    parameters:                     # required, non-empty
      - name: width
        source: filename            # filename | extrude_amount | spreadsheet | constant
        type: float                 # float | int | str
        units: mm                   # optional
        default: 50                 # optional; required when source=constant
        doc: "..."                  # optional
    docstring: |                    # optional, multi-line
      EN 10058 hot-rolled flat steel bar.
    base_class: BasePartObject      # optional; deferred to a future phase
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Sources that a parameter can declare. ``filename`` pulls from a
# named regex group in ``filename_pattern``; ``extrude_amount``
# extracts the constant N from an ``extrude(..., amount=N)`` call;
# ``spreadsheet`` reads from a tier-6 cell of the same name;
# ``constant`` uses the manifest's declared ``default`` for every
# fixture (the parameter doesn't vary, but is exposed as a kwarg);
# ``lookup`` reads from the manifest's ``dimensions_table`` keyed by
# the value of another filename-sourced parameter.
_VALID_PARAM_SOURCES = frozenset({
    "filename", "extrude_amount", "spreadsheet", "constant", "lookup",
})


_VALID_PARAM_TYPES = frozenset({"float", "int", "str"})


class ManifestError(ValueError):
    """Raised when a family manifest fails schema validation."""


@dataclass(frozen=True)
class StandardRef:
    """The international standard a family implements."""

    ref: str
    title: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ParameterDecl:
    """One parameter declared by a manifest."""

    name: str
    source: str
    type: str = "float"
    units: str | None = None
    default: Any = None
    doc: str | None = None


@dataclass(frozen=True)
class FamilyManifest:
    """A parsed and validated family manifest."""

    family: str
    class_name: str
    standard: StandardRef
    fixture_glob: str
    parameters: tuple[ParameterDecl, ...]
    filename_pattern: str | None = None
    docstring: str | None = None
    base_class: str | None = None
    # ``dimensions_table`` keys are stringified lookup keys (e.g. ``"280"``
    # for an HE-B 280 fixture). Each value is a mapping of param name → value.
    # Used by parameters declared with ``source: lookup``.
    dimensions_table: dict[str, dict[str, Any]] | None = field(
        default=None, compare=False
    )
    # The name of the filename-sourced parameter whose value is used as
    # the dimensions_table key. When not set, defaults to the first
    # ``source: filename`` parameter.
    lookup_key: str | None = None
    source_path: Path | None = field(default=None, compare=False)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_manifest(path: Path | str) -> FamilyManifest:
    """Load a YAML family manifest from disk.

    Returns a fully-validated :class:`FamilyManifest`. Raises
    :class:`ManifestError` with a specific message if any required
    field is missing, any value is malformed, or any cross-field
    consistency check fails.
    """
    path = Path(path)
    try:
        raw = path.read_text()
    except OSError as exc:
        raise ManifestError(f"could not read manifest {path}: {exc}") from exc
    return _parse_manifest_text(raw, source_path=path)


def parse_manifest_text(text: str) -> FamilyManifest:
    """Parse a manifest from a YAML string (no filesystem read)."""
    return _parse_manifest_text(text, source_path=None)


def _parse_manifest_text(text: str, source_path: Path | None) -> FamilyManifest:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(
            "manifest top-level must be a mapping (got "
            f"{type(data).__name__})"
        )
    return _validate(data, source_path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(data: dict, source_path: Path | None) -> FamilyManifest:
    """Apply the schema to a parsed-YAML mapping."""
    family = _require_str(data, "family")
    if not _re.fullmatch(r"[a-z][a-z0-9_]*", family):
        raise ManifestError(
            f"family slug {family!r} must be lowercase snake_case (start "
            f"with a letter, only ``a-z 0-9 _``)"
        )

    class_name = _require_str(data, "class_name")
    if not _re.fullmatch(r"[A-Z][A-Za-z0-9_]*", class_name):
        raise ManifestError(
            f"class_name {class_name!r} must be PascalCase (start with an "
            f"uppercase letter, only ``A-Z a-z 0-9 _``)"
        )

    standard_raw = data.get("standard")
    if not isinstance(standard_raw, dict):
        raise ManifestError("missing or non-mapping ``standard`` field")
    standard_ref = _require_str(standard_raw, "standard.ref", parent=standard_raw)
    standard = StandardRef(
        ref=standard_ref,
        title=standard_raw.get("title"),
        url=standard_raw.get("url"),
    )

    fixture_glob = _require_str(data, "fixture_glob")

    filename_pattern = data.get("filename_pattern")
    if filename_pattern is not None and not isinstance(filename_pattern, str):
        raise ManifestError(
            "filename_pattern must be a string regex (got "
            f"{type(filename_pattern).__name__})"
        )
    filename_named_groups: set[str] = set()
    if filename_pattern is not None:
        try:
            compiled = _re.compile(filename_pattern)
        except _re.error as exc:
            raise ManifestError(
                f"filename_pattern {filename_pattern!r} is not a valid "
                f"regex: {exc}"
            ) from exc
        filename_named_groups = set(compiled.groupindex.keys())

    parameters_raw = data.get("parameters")
    if not isinstance(parameters_raw, list) or not parameters_raw:
        raise ManifestError(
            "``parameters`` must be a non-empty list of parameter "
            "declarations"
        )

    parameters: list[ParameterDecl] = []
    seen_names: set[str] = set()
    for i, p_raw in enumerate(parameters_raw):
        if not isinstance(p_raw, dict):
            raise ManifestError(
                f"parameters[{i}] must be a mapping; got "
                f"{type(p_raw).__name__}"
            )
        name = _require_str(p_raw, f"parameters[{i}].name")
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ManifestError(
                f"parameters[{i}].name {name!r} must be a valid Python "
                f"identifier"
            )
        if name in seen_names:
            raise ManifestError(
                f"duplicate parameter name {name!r}"
            )
        seen_names.add(name)

        source = _require_str(p_raw, f"parameters[{i}].source")
        if source not in _VALID_PARAM_SOURCES:
            raise ManifestError(
                f"parameters[{i}].source {source!r} not understood; "
                f"valid: {sorted(_VALID_PARAM_SOURCES)}"
            )
        if source == "filename":
            if filename_pattern is None:
                raise ManifestError(
                    f"parameters[{i}] (name={name!r}) has source=filename "
                    "but the manifest declares no filename_pattern"
                )
            if name not in filename_named_groups:
                raise ManifestError(
                    f"parameters[{i}] (name={name!r}) has source=filename "
                    f"but is not a named group in filename_pattern "
                    f"(groups: {sorted(filename_named_groups)})"
                )
        if source == "constant" and "default" not in p_raw:
            raise ManifestError(
                f"parameters[{i}] (name={name!r}) has source=constant "
                "but no ``default`` value"
            )

        type_ = p_raw.get("type", "float")
        if type_ not in _VALID_PARAM_TYPES:
            raise ManifestError(
                f"parameters[{i}].type {type_!r} not understood; "
                f"valid: {sorted(_VALID_PARAM_TYPES)}"
            )

        parameters.append(
            ParameterDecl(
                name=name,
                source=source,
                type=type_,
                units=p_raw.get("units"),
                default=p_raw.get("default"),
                doc=p_raw.get("doc"),
            )
        )

    docstring = data.get("docstring")
    if docstring is not None and not isinstance(docstring, str):
        raise ManifestError("docstring must be a string when provided")

    base_class = data.get("base_class")
    if base_class is not None and not isinstance(base_class, str):
        raise ManifestError("base_class must be a string when provided")

    dimensions_table = data.get("dimensions_table")
    if dimensions_table is not None:
        if not isinstance(dimensions_table, dict):
            raise ManifestError(
                "dimensions_table must be a mapping of {key: {param: value, ...}}"
            )
        # Coerce all top-level keys to strings — YAML often parses numeric
        # keys as ints; we want a uniform string lookup.
        coerced = {}
        for k, v in dimensions_table.items():
            if not isinstance(v, dict):
                raise ManifestError(
                    f"dimensions_table entry for {k!r} must be a mapping "
                    f"of param names to values; got {type(v).__name__}"
                )
            coerced[str(k)] = v
        dimensions_table = coerced

    lookup_key = data.get("lookup_key")
    if lookup_key is not None and not isinstance(lookup_key, str):
        raise ManifestError("lookup_key must be a string when provided")

    # Cross-checks for the lookup pipeline.
    lookup_params = [p for p in parameters if p.source == "lookup"]
    if lookup_params and dimensions_table is None:
        raise ManifestError(
            f"parameters with source=lookup require a dimensions_table: "
            f"{[p.name for p in lookup_params]}"
        )
    if dimensions_table is not None and not lookup_params:
        raise ManifestError(
            "dimensions_table is set but no parameter has source=lookup"
        )
    if dimensions_table is not None:
        # Verify every lookup param appears in every table row.
        for key, row in dimensions_table.items():
            missing = [p.name for p in lookup_params if p.name not in row]
            if missing:
                raise ManifestError(
                    f"dimensions_table[{key!r}] missing lookup params: {missing}"
                )
        # Resolve the lookup_key default to the first filename-sourced param.
        if lookup_key is None:
            filename_params = [p.name for p in parameters if p.source == "filename"]
            if not filename_params:
                raise ManifestError(
                    "dimensions_table requires either an explicit lookup_key "
                    "or at least one parameter with source=filename"
                )
            lookup_key = filename_params[0]
        # Verify the lookup_key references a real declared param.
        if lookup_key not in {p.name for p in parameters}:
            raise ManifestError(
                f"lookup_key {lookup_key!r} is not a declared parameter"
            )

    # Reject unknown top-level keys so typos surface loudly.
    allowed = {
        "family", "class_name", "standard", "fixture_glob",
        "filename_pattern", "parameters", "docstring", "base_class",
        "dimensions_table", "lookup_key",
    }
    unknown = set(data.keys()) - allowed
    if unknown:
        raise ManifestError(
            f"unknown top-level keys: {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
        )

    return FamilyManifest(
        family=family,
        class_name=class_name,
        standard=standard,
        fixture_glob=fixture_glob,
        filename_pattern=filename_pattern,
        parameters=tuple(parameters),
        docstring=docstring,
        base_class=base_class,
        dimensions_table=dimensions_table,
        lookup_key=lookup_key,
        source_path=source_path,
    )


def _require_str(d: dict, field_name: str, parent: dict | None = None) -> str:
    """Look up ``d[field_name]``, ensure it's a non-empty string."""
    src = parent if parent is not None else d
    key = field_name.split(".")[-1]
    val = src.get(key)
    if val is None:
        raise ManifestError(f"missing required field {field_name!r}")
    if not isinstance(val, str) or not val.strip():
        raise ManifestError(
            f"field {field_name!r} must be a non-empty string (got "
            f"{type(val).__name__})"
        )
    return val
