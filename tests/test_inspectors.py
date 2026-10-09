import pytest

from app.deployer.inspectors import CompositeInspector, GhcrInspector
from app.deployer.images import ImageInfo
from app.poller.ghcr import GhcrClient
from tests.fake_ghcr import FakeGhcr

LABELS = {"mlapi.model": "true"}


@pytest.fixture
def ghcr():
    g = FakeGhcr()
    g.add("ecg", LABELS)
    return g


def inspector(ghcr):
    return GhcrInspector(GhcrClient(token=ghcr.token, org=ghcr.org, http=ghcr.client()), org=ghcr.org)


async def test_inspects_a_ghcr_image_by_default_tag(ghcr):
    info = await inspector(ghcr).inspect("ghcr.io/org/ecg", "ghcr")
    assert info.labels == LABELS and info.digest == ghcr.top_digest("ecg") and info.package_name == "ecg"


async def test_inspects_a_ghcr_image_with_explicit_latest_tag(ghcr):
    info = await inspector(ghcr).inspect("ghcr.io/org/ecg:latest", "ghcr")
    assert info.package_name == "ecg"


@pytest.mark.parametrize(
    "image",
    ["ghcr.io/other-org/ecg", "ghcr.io/org/missing", "docker.io/org/ecg", "ecg", "ghcr.io/org/ecg/extra/parts", "ghcr.io/org/../ecg", ""],
)
async def test_rejects_foreign_missing_or_malformed_references(ghcr, image):
    with pytest.raises(LookupError):
        await inspector(ghcr).inspect(image, "ghcr")


async def test_wrong_source_is_not_found(ghcr):
    with pytest.raises(LookupError):
        await inspector(ghcr).inspect("ghcr.io/org/ecg", "local")


class Fixed:
    def __init__(self, tag):
        self.tag = tag

    async def inspect(self, image, source):
        return ImageInfo(labels={}, digest=self.tag, package_name=self.tag)


async def test_composite_routes_by_source_and_reports_missing_ones():
    comp = CompositeInspector(local=Fixed("L"), ghcr=Fixed("G"))
    assert (await comp.inspect("x", "local")).digest == "L"
    assert (await comp.inspect("x", "ghcr")).digest == "G"
    with pytest.raises(LookupError):
        await CompositeInspector(local=None, ghcr=Fixed("G")).inspect("x", "local")
