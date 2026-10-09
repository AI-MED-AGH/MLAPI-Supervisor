import pytest

from app.registry.resources import ResourceError, Resources, parse_cpu, parse_quantity

GI = 1024**3


@pytest.mark.parametrize(
    "raw,expected",
    [("1", 1000), ("2", 2000), ("500m", 500), ("0.5", 500), ("1.5", 1500), ("250m", 250)],
)
def test_parse_cpu(raw, expected):
    assert parse_cpu(raw) == expected


@pytest.mark.parametrize("raw", ["", "0", "0m", "-1", "abc", "1x", "1000", "1e3", "1 "])
def test_parse_cpu_rejects(raw):
    with pytest.raises(ResourceError):
        parse_cpu(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [("4Gi", 4 * GI), ("512Mi", 512 * 1024**2), ("1Ti", 1024**4), ("1.5Gi", int(1.5 * GI)),
     ("100Ki", 100 * 1024), ("1048576", 1048576)],
)
def test_parse_quantity(raw, expected):
    assert parse_quantity(raw) == expected


@pytest.mark.parametrize("raw", ["", "0", "0Gi", "-1Gi", "Gi", "4GB", "4 Gi", "9999Ti", "abc"])
def test_parse_quantity_rejects(raw):
    with pytest.raises(ResourceError):
        parse_quantity(raw)


def R(cpu=1000, mem=2 * GI, gpu=False, disk=5 * GI):
    return Resources(cpu_m=cpu, memory_bytes=mem, gpu=gpu, disk_bytes=disk)


@pytest.mark.parametrize(
    "requested,approved,exceeds",
    [
        (R(), None, True),                      # nothing approved yet
        (R(), R(), False),                      # equal
        (R(cpu=500), R(), False),               # lower cpu
        (R(cpu=1001), R(), True),
        (R(mem=2 * GI + 1), R(), True),
        (R(disk=5 * GI + 1), R(), True),
        (R(gpu=True), R(gpu=False), True),      # turning the GPU on is an increase
        (R(gpu=True), R(gpu=True), False),
        (R(gpu=False), R(gpu=True), False),     # dropping the GPU is fine
        (R(cpu=500, mem=GI, disk=GI), R(), False),
    ],
)
def test_exceeds(requested, approved, exceeds):
    assert requested.exceeds(approved) is exceeds


def test_dict_roundtrip():
    r = R(cpu=1500, gpu=True)
    assert Resources.from_dict(r.to_dict()) == r


def test_from_dict_rejects_garbage():
    with pytest.raises(ResourceError):
        Resources.from_dict({"cpu_m": "x"})
