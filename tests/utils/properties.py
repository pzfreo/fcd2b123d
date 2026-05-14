"""Shared geometric property schema.

Pure Python. No FreeCAD, build123d, or OCCT imports. Both the snapshot tool
(FreeCAD side) and the comparison utility (build123d side) construct and
exchange `Properties` instances via this module's JSON serialization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass(frozen=True)
class Properties:
    volume: float
    surface_area: float
    center_of_mass: tuple[float, float, float]
    principal_moi: tuple[float, float, float]

    source: str | None = None
    snapshot_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "volume": self.volume,
            "surface_area": self.surface_area,
            "center_of_mass": list(self.center_of_mass),
            "principal_moi": list(self.principal_moi),
            "source": self.source,
            "snapshot_date": self.snapshot_date,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Properties":
        return cls(
            volume=float(d["volume"]),
            surface_area=float(d["surface_area"]),
            center_of_mass=tuple(float(x) for x in d["center_of_mass"]),
            principal_moi=tuple(sorted(float(x) for x in d["principal_moi"])),
            source=d.get("source"),
            snapshot_date=d.get("snapshot_date"),
        )

    def to_file(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def from_file(cls, path: Path | str) -> "Properties":
        return cls.from_dict(json.loads(Path(path).read_text()))
