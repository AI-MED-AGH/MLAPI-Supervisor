class RegistryError(Exception):
    pass


class NotFound(RegistryError):
    pass


class Conflict(RegistryError):
    """The requested transition is not valid in the current state."""


class Invalid(RegistryError):
    """The request is well-formed but not acceptable."""
