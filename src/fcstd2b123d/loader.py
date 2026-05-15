"""FreeCAD document loading. Imports FreeCAD lazily — only fails when called.

Keeping the FreeCAD import inside the function lets `import fcstd2b123d`
succeed in build123d-only environments (e.g. when reading version, or when
test infrastructure inspects the package without running the translator).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def open_document(path: Path | str):
    """Open an FCStd, recompute it, yield the document, close on exit.

    The recompute() call ensures every object's Shape is current — important
    for newly-loaded documents where derived shapes may be stale on disk.
    """
    import FreeCAD  # lazy

    doc = FreeCAD.openDocument(str(path))
    try:
        doc.recompute()
        yield doc
    finally:
        FreeCAD.closeDocument(doc.Name)
