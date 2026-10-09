from app.deployer.images import ImageInfo
from app.deployer.probe import Health


class FakeProbe:
    """Scriptable model probe: per-URL health, info and schema."""

    def __init__(self):
        self.loaded = True
        self.healthy = True
        self.info_name: str | None = None  # None -> echo the name in the URL
        self.schema_body: dict | None = {
            "model_name": "x", "model_version": "1.0.0", "request_schema": {"type": "object"},
            "response_schema": {"type": "object"}, "example": {"data": 1},
        }
        self.health_calls = 0

    async def health(self, url: str) -> Health:
        self.health_calls += 1
        if not self.healthy:
            return Health(ok=False)
        return Health(ok=True, model_loaded=self.loaded)

    async def info(self, url: str) -> dict | None:
        name = self.info_name or url.removeprefix("http://fake-").split(":")[0]
        return {"name": name, "version": "1.0.0"}

    async def schema(self, url: str) -> dict | None:
        return self.schema_body


class FakeInspector:
    def __init__(self):
        self.images: dict[str, ImageInfo] = {}

    def add(self, image: str, labels: dict, digest: str = "sha256:aaa", package_name: str | None = None):
        self.images[image] = ImageInfo(
            labels=labels, digest=digest, package_name=package_name or image.rsplit("/", 1)[-1].split(":")[0]
        )

    async def inspect(self, image: str, source: str) -> ImageInfo:
        if image not in self.images:
            raise LookupError(image)
        return self.images[image]
