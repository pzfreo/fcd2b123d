"""TranslationContext: accumulator passed through every handler.

Each handler appends one or more step records via ``ctx.add_step()``. At the
end of ``translate()`` the context serialises to the JSON schema described in
SPEC §14.3.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__

SCHEMA_VERSION = "1"


@dataclass
class TranslationContext:
    source_path: Path
    freecad_version: str | None = None
    steps: list[dict] = field(default_factory=list)
    # Tier-6 parametric data: populated by translator before handlers run.
    # ParameterSet is intentionally typed as Any here to keep this module
    # free of the FreeCAD-aware parametric module's imports.
    parameters: object | None = None
    # Emit style: "algebra" (default, value-style) or "builder" (with
    # BuildSketch / BuildPart contexts). Set by the translator from the
    # CLI's ``--style`` flag; consumed by individual translator functions.
    style: str = "algebra"

    def add_step(
        self,
        *,
        feature_type: str,
        feature_name: str,
        depends_on: list[str] | None = None,
        renamed_from_default: bool = False,
        build123d_code: str = "",
        properties: dict | None = None,
    ) -> None:
        """Record one feature step.

        ``properties`` is the FreeCAD-side geometric snapshot of the
        cumulative shape after this step (same fields the comparison utility
        uses). None for steps that don't produce a 3D solid (e.g. sketches).
        """
        self.steps.append({
            "step_index": len(self.steps),
            "feature_type": feature_type,
            "feature_name": feature_name,
            "freecad_internal_name": feature_name,
            "depends_on": depends_on or [],
            "renamed_from_default": renamed_from_default,
            "build123d_code": build123d_code,
            "properties": properties,
        })

    def to_dict(self, final_code: str | None = None) -> dict[str, Any]:
        primitives_used = sorted({s["feature_type"] for s in self.steps})
        return {
            "schema_version": SCHEMA_VERSION,
            "exporter_version": f"fcstd2b123d-{__version__}",
            "source": {
                "path": str(self.source_path),
                "freecad_version": self.freecad_version,
            },
            "summary": {
                "feature_count": len(self.steps),
                "primitives_used": primitives_used,
            },
            "steps": self.steps,
            "final_code": final_code,
        }

    def write_json(self, path: Path | str, final_code: str | None = None) -> None:
        Path(path).write_text(json.dumps(self.to_dict(final_code), indent=2) + "\n")
