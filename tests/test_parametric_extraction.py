"""Parameter discovery from both holders, without needing FreeCAD.

``extract_parameters`` reads only a handful of attributes off each object, so
it can be exercised against stand-ins. That matters because the translator
otherwise needs a FreeCAD interpreter to test at all, and this is the layer
where a whole holder type went unsupported without anything failing.

Values mirror ``FCBL_table_parametric.FCStd`` from the FreeCAD Parts Library,
whose VarSet is labelled "Table Dimensions" and carries Width/Height/Depth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fcstd2b123d.parametric import extract_parameters, rewrite_expression


class Quantity:
    """Stands in for a FreeCAD Quantity, which carries its value on .Value."""

    def __init__(self, value: float) -> None:
        self.Value = value


@dataclass
class VarSet:
    """An App::VarSet: variables are dynamic properties, not cells."""

    Label: str = "Table Dimensions"
    TypeId: str = "App::VarSet"
    variables: dict = field(default_factory=dict)

    @property
    def PropertiesList(self):
        # A real VarSet lists its intrinsics alongside the user's variables.
        return [*self.variables, "ExpressionEngine", "Label", "Label2", "Visibility"]

    def __getattr__(self, name):
        try:
            return self.__dict__["variables"][name]
        except KeyError as missing:
            raise AttributeError(name) from missing


@dataclass
class Spreadsheet:
    """A Spreadsheet::Sheet: an aliased cell exposes its alias as an attribute."""

    Label: str = "Parameters"
    TypeId: str = "Spreadsheet::Sheet"
    cells: dict = field(default_factory=dict)  # address -> (alias, value)

    def getUsedCells(self):
        return list(self.cells)

    def getAlias(self, cell):
        return self.cells[cell][0]

    def __getattr__(self, name):
        for alias, value in self.__dict__["cells"].values():
            if alias == name:
                return value
        raise AttributeError(name)


@dataclass
class Document:
    Objects: list = field(default_factory=list)


def test_varset_variables_are_parameters():
    doc = Document([VarSet(variables={
        "Width": Quantity(1400.0), "Height": Quantity(750.0), "Depth": Quantity(800.0),
    })])
    params = extract_parameters(doc)
    assert params.value_of("Width") == 1400.0
    assert params.value_of("Height") == 750.0
    assert params.value_of("Depth") == 800.0


def test_varset_intrinsics_are_not_parameters():
    doc = Document([VarSet(variables={"Width": Quantity(1400.0)})])
    params = extract_parameters(doc)
    assert set(params.aliases) == {"Width"}


def test_varset_non_numeric_variables_are_skipped():
    # A VarSet holds a material name or a flag as readily as a length.
    doc = Document([VarSet(variables={
        "Width": Quantity(1400.0), "Material": "oak", "Chamfered": True,
    })])
    assert set(extract_parameters(doc).aliases) == {"Width"}


def test_spreadsheet_aliases_still_extract():
    doc = Document([Spreadsheet(cells={"B1": ("width", 50.0), "B2": ("height", Quantity(20.0))})])
    params = extract_parameters(doc)
    assert params.value_of("width") == 50.0
    assert params.value_of("height") == 20.0


def test_both_holders_share_one_namespace():
    doc = Document([
        Spreadsheet(cells={"B1": ("width", 50.0)}),
        VarSet(variables={"Height": Quantity(750.0)}),
    ])
    params = extract_parameters(doc)
    assert params.value_of("width") == 50.0
    assert params.value_of("Height") == 750.0


def test_a_name_collision_breaks_on_document_order():
    # The namespace is flat, so a name held twice is ambiguous by the time an
    # expression is rewritten. Document order has always been the tie-break
    # between two spreadsheets; a VarSet joins the same rule rather than
    # introducing a second one.
    sheet = Spreadsheet(cells={"B1": ("Width", 50.0)})
    varset = VarSet(variables={"Width": Quantity(1400.0)})
    assert extract_parameters(Document([sheet, varset])).value_of("Width") == 1400.0
    assert extract_parameters(Document([varset, sheet])).value_of("Width") == 50.0


def test_a_varset_reference_rewrites_like_a_sheet_reference():
    # Both holders are written <<Label>>.name, so the expression path is shared.
    doc = Document([VarSet(variables={"Height": Quantity(750.0)})])
    params = extract_parameters(doc)
    assert rewrite_expression("<<Table Dimensions>>.Height", params) == "Height"
    assert params.used_parameters() == [("Height", 750.0)]
