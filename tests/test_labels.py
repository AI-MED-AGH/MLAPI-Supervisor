import pytest

from app.registry.labels import LabelError, NotAModel, parse_labels
from app.registry.resources import Resources

GI = 1024**3
OK = {"mlapi.model": "true", "mlapi.model.name": "pneumonia-xray", "mlapi.cpu": "2",
      "mlapi.memory": "4Gi", "mlapi.gpu": "true", "mlapi.disk": "20Gi", "mlapi.mode": "queue"}


def test_full_label_set():
    parsed = parse_labels(OK, "ignored-package")
    assert parsed.name == "pneumonia-xray"
    assert parsed.mode == "queue"
    assert parsed.resources == Resources(cpu_m=2000, memory_bytes=4 * GI, gpu=True, disk_bytes=20 * GI)


def test_defaults_when_labels_missing():
    parsed = parse_labels({"mlapi.model": "true"}, "my-model")
    assert parsed.name == "my-model"
    assert parsed.mode == "sync"
    assert parsed.resources == Resources(cpu_m=1000, memory_bytes=2 * GI, gpu=False, disk_bytes=5 * GI)


def test_package_name_uses_last_path_component():
    assert parse_labels({"mlapi.model": "true"}, "ai-med-agh/ecg-model").name == "ecg-model"


@pytest.mark.parametrize("labels", [{}, {"mlapi.model": "false"}, {"mlapi.model": "yes"}, {"other": "x"}])
def test_not_a_model(labels):
    with pytest.raises(NotAModel):
        parse_labels(labels, "pkg")


@pytest.mark.parametrize(
    "overrides",
    [
        {"mlapi.model.name": "Bad_Name"},
        {"mlapi.model.name": "x" * 50},
        {"mlapi.model.name": "-lead"},
        {"mlapi.mode": "weird"},
        {"mlapi.cpu": "lots"},
        {"mlapi.memory": "0"},
        {"mlapi.gpu": "maybe"},
        {"mlapi.disk": "-5Gi"},
    ],
)
def test_invalid_labels(overrides):
    with pytest.raises(LabelError):
        parse_labels({**OK, **overrides}, "pkg")


def test_invalid_package_name_without_name_label():
    with pytest.raises(LabelError):
        parse_labels({"mlapi.model": "true"}, "Bad Package")


@pytest.mark.parametrize("name", ["ok\n", "ok\n\n", "ok\r", "ok ", " ok", "ök", "ａｂｃ"])
def test_model_name_must_match_exactly(name):
    with pytest.raises(LabelError):
        parse_labels({"mlapi.model": "true", "mlapi.model.name": name}, "pkg")


def test_gpu_label_must_match_exactly():
    with pytest.raises(LabelError):
        parse_labels({"mlapi.model": "true", "mlapi.gpu": "true\n"}, "pkg")


def test_mode_label_must_match_exactly():
    with pytest.raises(LabelError):
        parse_labels({"mlapi.model": "true", "mlapi.mode": "queue\n"}, "pkg")
