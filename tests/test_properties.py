"""Schema round-trip tests for Properties. No build123d or FreeCAD required."""

from fcstd2b123d.properties import Properties


def _props(**kw):
    base = dict(
        volume=1000.0,
        surface_area=600.0,
        center_of_mass=(1.0, 2.0, 3.0),
        principal_moi=(100.0, 200.0, 300.0),
    )
    base.update(kw)
    return Properties(**base)


def test_dict_round_trip():
    p = _props(source="test", snapshot_date="2026-05-14")
    assert Properties.from_dict(p.to_dict()) == p


def test_dict_round_trip_no_metadata():
    p = _props()
    p2 = Properties.from_dict(p.to_dict())
    assert p2 == p
    assert p2.source is None
    assert p2.snapshot_date is None


def test_principal_moi_sorted_on_from_dict():
    """Inbound MOI eigenvalues are sorted ascending, regardless of input order."""
    p = Properties.from_dict({
        "volume": 1.0,
        "surface_area": 6.0,
        "center_of_mass": [0.0, 0.0, 0.0],
        "principal_moi": [300.0, 100.0, 200.0],
    })
    assert p.principal_moi == (100.0, 200.0, 300.0)


def test_file_round_trip(tmp_path):
    p = _props()
    f = tmp_path / "snapshot.json"
    p.to_file(f)
    assert Properties.from_file(f) == p


def test_file_is_human_readable_json(tmp_path):
    p = _props()
    f = tmp_path / "snapshot.json"
    p.to_file(f)
    text = f.read_text()
    # Pretty-printed (newlines + indentation), not a single line
    assert "\n" in text
    assert '"volume"' in text


def test_center_of_mass_is_tuple():
    p = Properties.from_dict({
        "volume": 1.0, "surface_area": 1.0,
        "center_of_mass": [1.0, 2.0, 3.0],
        "principal_moi": [1.0, 2.0, 3.0],
    })
    assert isinstance(p.center_of_mass, tuple)
    assert p.center_of_mass == (1.0, 2.0, 3.0)


def test_frozen_dataclass_immutable():
    import dataclasses
    p = _props()
    try:
        p.volume = 9999.0  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Properties should be frozen")
