"""fcstd2b123d — translate FreeCAD .FCStd files into build123d Python."""

__version__ = "0.1.0"

from .translator import translate

__all__ = ["translate", "__version__"]
