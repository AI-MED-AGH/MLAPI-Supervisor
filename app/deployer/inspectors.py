import logging
import re

from app.deployer.images import ImageInfo
from app.poller.ghcr import GhcrClient, GhcrError, is_valid_package

logger = logging.getLogger(__name__)

_REF_RE = re.compile(r"ghcr\.io/(?P<org>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<pkg>[A-Za-z0-9][A-Za-z0-9._-]*)(?::(?P<tag>[A-Za-z0-9._-]{1,128}))?")


class GhcrInspector:
    """Reads labels and the digest of an image in the configured GHCR organisation."""

    def __init__(self, client: GhcrClient, org: str):
        self._client = client
        self._org = org

    async def inspect(self, image: str, source: str) -> ImageInfo:
        if source != "ghcr":
            raise LookupError(image)
        match = _REF_RE.fullmatch(image or "")
        if match is None or match["org"].lower() != self._org.lower() or not is_valid_package(match["pkg"]):
            raise LookupError(image)
        try:
            digest = await self._client.resolve_digest(match["pkg"], match["tag"] or "latest")
            if digest is None:
                raise LookupError(image)
            labels = await self._client.read_labels(match["pkg"], digest)
        except GhcrError as exc:
            logger.warning("GHCR lookup failed for %s: %s", image, exc)
            raise LookupError(image) from exc
        return ImageInfo(labels=labels, digest=digest, package_name=match["pkg"])


class CompositeInspector:
    def __init__(self, *, local, ghcr):
        self._by_source = {"local": local, "ghcr": ghcr}

    async def inspect(self, image: str, source: str) -> ImageInfo:
        inspector = self._by_source.get(source)
        if inspector is None:
            raise LookupError(f"no inspector available for source {source!r}")
        return await inspector.inspect(image, source)
