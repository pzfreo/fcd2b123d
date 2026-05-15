"""Errors raised by the translator."""


class TranslatorError(Exception):
    """Base class for all translator errors."""


class UnsupportedFeatureError(TranslatorError):
    """Raised when a FreeCAD object's TypeId has no registered translator.

    Carries the object's TypeId and Label so the message names the offending
    feature precisely. We refuse loudly rather than silently produce wrong
    geometry (see SPEC §2).
    """

    def __init__(self, typeid: str, label: str):
        self.typeid = typeid
        self.label = label
        super().__init__(
            f"No translator for FreeCAD type {typeid!r} (object {label!r}). "
            f"This may be a feature outside v1's tier-supported set, or a "
            f"gap in the tier map — see SPEC §13.4."
        )
