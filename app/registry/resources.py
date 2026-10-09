import re
from dataclasses import dataclass

MAX_CPU_M = 256_000
MAX_BYTES = 4 * 1024**4  # 4 Ti

_CPU_RE = re.compile(r"^(\d+(?:\.\d+)?)(m?)$")
_QTY_RE = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti)?$")
_UNITS = {None: 1, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}


class ResourceError(ValueError):
    pass


def parse_cpu(raw: str) -> int:
    """Returns millicores. Accepts '2', '0.5', '500m'."""
    match = _CPU_RE.match(raw or "")
    if not match:
        raise ResourceError(f"invalid cpu quantity: {raw!r}")
    number, milli = float(match.group(1)), match.group(2) == "m"
    cpu_m = int(round(number if milli else number * 1000))
    if cpu_m <= 0 or cpu_m > MAX_CPU_M:
        raise ResourceError(f"cpu out of range: {raw!r}")
    return cpu_m


def parse_quantity(raw: str) -> int:
    """Returns bytes. Accepts plain bytes or Ki/Mi/Gi/Ti suffixes (decimals allowed)."""
    match = _QTY_RE.match(raw or "")
    if not match:
        raise ResourceError(f"invalid quantity: {raw!r}")
    value = int(round(float(match.group(1)) * _UNITS[match.group(2)]))
    if value <= 0 or value > MAX_BYTES:
        raise ResourceError(f"quantity out of range: {raw!r}")
    return value


@dataclass(frozen=True)
class Resources:
    cpu_m: int
    memory_bytes: int
    gpu: bool
    disk_bytes: int

    def exceeds(self, approved: "Resources | None") -> bool:
        """True when this request needs admin approval given what was approved before."""
        if approved is None:
            return True
        return (
            self.cpu_m > approved.cpu_m
            or self.memory_bytes > approved.memory_bytes
            or self.disk_bytes > approved.disk_bytes
            or (self.gpu and not approved.gpu)
        )

    def to_dict(self) -> dict:
        return {
            "cpu_m": self.cpu_m,
            "memory_bytes": self.memory_bytes,
            "gpu": self.gpu,
            "disk_bytes": self.disk_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Resources":
        try:
            return cls(
                cpu_m=int(data["cpu_m"]),
                memory_bytes=int(data["memory_bytes"]),
                gpu=bool(data["gpu"]),
                disk_bytes=int(data["disk_bytes"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceError(f"invalid resources: {data!r}") from exc
