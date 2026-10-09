from enum import Enum

from app.registry.resources import Resources


class Decision(Enum):
    AUTO = "auto"
    NEEDS_APPROVAL = "needs_approval"


def decide(approved: Resources | None, requested: Resources) -> Decision:
    """Any increase over what an admin approved (or no approval yet) needs a human."""
    return Decision.NEEDS_APPROVAL if requested.exceeds(approved) else Decision.AUTO
