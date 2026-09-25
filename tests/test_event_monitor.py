from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas import ModelSnapshot
from app.services import EventMonitor

STATE_0 = [
    {
        "name": "yolo-v8",
        "status": "Running",
        "cpu_usage": "500m",
        "replicas": {"desired": 2, "ready": 2, "available": 2},
        "pods": [
            {
                "name": "yolo-1",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "250m",
            },
            {
                "name": "yolo-2",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "250m",
            },
        ],
    },
    {
        "name": "resnet50",
        "status": "CrashLoopBackOff",  # Błąd z poda przepisywany do statusu modelu
        "cpu_usage": "0m",
        "replicas": {"desired": 1, "ready": 0, "available": 0},
        "pods": [
            {
                "name": "resnet-1",
                "phase": "Running",
                "state": "waiting",
                "reason": "CrashLoopBackOff",
                "cpu_usage": "0m",
            }
        ],
    },
    {
        "name": "whisper",
        "status": "Running",
        "cpu_usage": "800m",
        "replicas": {"desired": 2, "ready": 2, "available": 2},
        "pods": [
            {
                "name": "whisper-1",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "400m",
            },
            {
                "name": "whisper-2",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "400m",
            },
        ],
    },
    {
        "name": "llama-3",
        "status": "ErrImagePull",
        "cpu_usage": "0m",
        "replicas": {"desired": 1, "ready": 0, "available": 0},
        "pods": [
            {
                "name": "llama-1",
                "phase": "Pending",
                "state": "waiting",
                "reason": "ErrImagePull",
                "cpu_usage": "0m",
            }
        ],
    },
    {
        "name": "bert",
        "status": "ScaledToZero",  # desired = 0
        "cpu_usage": "0m",
        "replicas": {"desired": 0, "ready": 0, "available": 0},
        "pods": [],
    },
]
STATE_1 = [
    {
        "name": "yolo-v8",
        "status": "Running",
        "cpu_usage": "500m",
        "replicas": {"desired": 2, "ready": 2, "available": 2},
        "pods": [
            {
                "name": "yolo-1",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "250m",
            },
            {
                "name": "yolo-2",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "250m",
            },
        ],
    },
    {
        "name": "resnet50",
        "status": "Running",  # Naprawił się!
        "cpu_usage": "400m",
        "replicas": {"desired": 1, "ready": 1, "available": 1},
        "pods": [
            {
                "name": "resnet-2-new",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "400m",
            }
        ],
    },
    {
        "name": "whisper",
        "status": "OOMKilled",  # Kaskadowa awaria podów
        "cpu_usage": "0m",
        "replicas": {"desired": 2, "ready": 0, "available": 0},
        "pods": [
            {
                "name": "whisper-1",
                "phase": "Running",
                "state": "waiting",
                "reason": "CrashLoopBackOff",
                "cpu_usage": "0m",
            },
            {
                "name": "whisper-2",
                "phase": "Failed",
                "state": "terminated",
                "reason": "OOMKilled",
                "cpu_usage": "0m",
            },
        ],
    },
    {
        "name": "llama-3",
        "status": "ImagePullBackOff",  # Zmiana błędu (częste w K8s przy problemach z obrazem)
        "cpu_usage": "0m",
        "replicas": {"desired": 1, "ready": 0, "available": 0},
        "pods": [
            {
                "name": "llama-1",
                "phase": "Pending",
                "state": "waiting",
                "reason": "ImagePullBackOff",
                "cpu_usage": "0m",
            }
        ],
    },
    {
        "name": "bert",
        "status": "Pending",  # desired = 1, ale pody jeszcze się nie stworzyły
        "cpu_usage": "0m",
        "replicas": {"desired": 1, "ready": 0, "available": 0},
        "pods": [],
    },
]
STATE_2 = [
    {
        "name": "yolo-v8",
        "status": "Running",
        "cpu_usage": "500m",
        "replicas": {"desired": 2, "ready": 2, "available": 2},
        "pods": [
            {
                "name": "yolo-1",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "250m",
            },
            {
                "name": "yolo-2",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "250m",
            },
        ],
    },
    {
        "name": "resnet50",
        "status": "Running",
        "cpu_usage": "400m",
        "replicas": {"desired": 1, "ready": 1, "available": 1},
        "pods": [
            {
                "name": "resnet-2-new",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "400m",
            }
        ],
    },
    {
        "name": "whisper",
        "status": "Running",  # Wyzdrowiał!
        "cpu_usage": "800m",
        "replicas": {"desired": 2, "ready": 2, "available": 2},
        "pods": [
            {
                "name": "whisper-1-new",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "400m",
            },
            {
                "name": "whisper-2-new",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "400m",
            },
        ],
    },
    {
        "name": "llama-3",
        "status": "Running",  # W końcu pobrał obraz
        "cpu_usage": "600m",
        "replicas": {"desired": 1, "ready": 1, "available": 1},
        "pods": [
            {
                "name": "llama-1",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "600m",
            }
        ],
    },
    {
        "name": "bert",
        "status": "Degraded",  # desired = 2, ale ready = 1 (jeden wstał, drugi się jeszcze tworzy, brak błędu)
        "cpu_usage": "300m",
        "replicas": {"desired": 2, "ready": 1, "available": 1},
        "pods": [
            {
                "name": "bert-1",
                "phase": "Running",
                "state": "running",
                "reason": None,
                "cpu_usage": "300m",
            },
            {
                "name": "bert-2",
                "phase": "Pending",
                "state": "waiting",
                "reason": None,
                "cpu_usage": "0m",
            },
        ],
    },
]


@pytest.mark.asyncio
async def test_worker_detects_cluster_state_changes():
    mock_kubernetes_service = MagicMock()
    mock_notification_service = AsyncMock()

    mock_kubernetes_service.get_models_cluster_status.side_effect = [
        [ModelSnapshot(**model_dict) for model_dict in STATE_0],
        [ModelSnapshot(**model_dict) for model_dict in STATE_1],
        [ModelSnapshot(**model_dict) for model_dict in STATE_2],
    ]

    worker = EventMonitor(
        kubernetes_service=mock_kubernetes_service,
        notification_service=mock_notification_service,
    )

    await worker._sent_events()

    assert mock_notification_service.notify.call_count == 3

    t0_calls = mock_notification_service.notify.call_args_list

    t0_notified_models = [call.args[2].data.name for call in t0_calls]
    t0_events = [call.args[1] for call in t0_calls]

    assert "resnet50" in t0_notified_models
    assert "CrashLoopBackOff" in t0_events
    assert "llama-3" in t0_notified_models
    assert "ErrImagePull" in t0_events
    assert "bert" in t0_notified_models
    assert "ScaledToZero" in t0_events

    mock_notification_service.notify.reset_mock()

    await worker._sent_events()

    assert mock_notification_service.notify.call_count == 4

    t1_calls = mock_notification_service.notify.call_args_list
    t1_notified_models = [call.args[2].data.name for call in t1_calls]
    t1_events = [call.args[1] for call in t1_calls]

    assert "resnet50" in t1_notified_models
    assert "Running" in t1_events

    assert "whisper" in t1_notified_models
    assert "OOMKilled" in t1_events

    assert "bert" in t1_notified_models
    assert "Pending" in t1_events

    assert "yolo-v8" not in t1_notified_models

    mock_notification_service.notify.reset_mock()

    await worker._sent_events()

    assert mock_notification_service.notify.call_count == 3

    t2_calls = mock_notification_service.notify.call_args_list
    t2_notified_models = [call.args[2].data.name for call in t2_calls]
    t2_events = [call.args[1] for call in t2_calls]

    assert "whisper" in t2_notified_models
    assert "llama-3" in t2_notified_models
    assert "bert" in t2_notified_models

    assert "CrashLoopBackOff" not in t2_events
    assert "OOMKilled" not in t2_events
    assert "Degraded" in t2_events
