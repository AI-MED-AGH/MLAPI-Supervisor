from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImageInfo:
    labels: dict[str, str]
    digest: str  # registry digest (ghcr) or local image id
    package_name: str  # last path component of the image, used when no name label is set


class ImageInspector(Protocol):
    async def inspect(self, image: str, source: str) -> ImageInfo:
        """Reads labels and digest of an image. Raises LookupError when it cannot be found."""
