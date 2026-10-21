from dataclasses import dataclass


@dataclass(frozen=True)
class Tier:
    name: str
    step: int  # seconds per point
    keep: int  # seconds of history kept
    source: str | None  # the finer tier this one is averaged from (None = measured directly)


# Each tier is built from the one before it, so a point is only written once its whole bucket is over.
TIERS = (
    Tier("5s", 5, 3600, None),
    Tier("1m", 60, 24 * 3600, "5s"),
    Tier("1h", 3600, 30 * 24 * 3600, "1m"),
    Tier("1d", 86400, 365 * 24 * 3600, "1h"),
)
BY_NAME = {t.name: t for t in TIERS}

# what the admin API calls a range -> the tier that serves it
RANGES = {"1h": "5s", "24h": "1m", "30d": "1h", "1y": "1d"}
