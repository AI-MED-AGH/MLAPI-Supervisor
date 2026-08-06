from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, ConnectError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.main import app
from app.watchman import Base
from app.watchman.database import get_session
from app.watchman.notification_service import NotificationService
from app.watchman.queries import (
    get_observers_subscribed_to_event,
    insert_new_subscription,
)
from app.watchman.schemas import SubscriptionSchema

pytestmark = pytest.mark.asyncio

DATABASE_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest_asyncio.fixture
async def get_test_session():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
       await conn.run_sync(Base.metadata.drop_all)

    await test_engine.dispose()

@pytest_asyncio.fixture
async def client(get_test_session):
    async def override_get_session():
        yield get_test_session


    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()

async def test_subscribe(client):
    response = await client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "wrong":["error"]})
    assert response.status_code == 422

    response = await client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":["error"]})
    assert response.status_code == 201

async def test_insert_subscription(client, get_test_session):
    response = await client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":["error"]})
    assert response.status_code == 201
    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    response = await client.post("/observers/", json={"webhook_url":"0.0.0.0:8001", "event_types":["error"]})
    assert response.status_code == 201
    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

async def test_notify_subscribers(client, get_test_session):
    response = await client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":["error"]})
    assert response.status_code == 201
    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    response = await client.post("/observers/", json={"webhook_url":"0.0.0.0:8001", "event_types":["error"]})
    assert response.status_code == 201
    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_notify_success(mock_post,get_test_session):
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8000", event_types=["error"]))

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    service = NotificationService()
    await service.notify(get_test_session, event="error", payload={"msg": "test"})

    mock_post.assert_called_once_with("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout)

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8001", event_types=["error"]))

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    await service.notify(get_test_session, event="error", payload={"msg": "test"})

    calls = [call("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout), call("0.0.0.0:8001", json={"msg": "test"}, timeout=service.timeout)]
    mock_post.assert_has_calls(calls, any_order=True)

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"


@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_notify_error(mock_post,get_test_session):
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8000", event_types=["error"]))
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8001", event_types=["error"]))
    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    success_response = None

    error_response = ConnectError("Server Error")

    mock_post.side_effect = [success_response, error_response, success_response, success_response, success_response, error_response, success_response, error_response]
    service = NotificationService(connection_error_max=1)

    # 1 notification
    await service.notify(get_test_session, event="error", payload={"msg": "test"})

    mock_post.assert_called()

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    # 2 notification
    await service.notify(get_test_session, event="error", payload={"msg": "test"})

    mock_post.assert_called()

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    # 3 notification
    await service.notify(get_test_session, event="error", payload={"msg": "test"})

    mock_post.assert_called()

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    # 4 notification
    await service.notify(get_test_session, event="error", payload={"msg": "test"})

    mock_post.assert_called()

    observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
async def test_many_subs(mock_post, get_test_session):
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8000", event_types=["error", "freeze"]))
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8001", event_types=["error"]))
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8002", event_types=["freeze"]))
    await insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8003", event_types=["error"]))
    error_observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(error_observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001'), Observer(id=4, webhook_url='0.0.0.0:8003')]"
    freeze_observers = await get_observers_subscribed_to_event(get_test_session, "freeze")
    assert str(freeze_observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=3, webhook_url='0.0.0.0:8002')]"

    success_response= None

    error_response = ConnectError("Server Error")

    mock_post.side_effect = [error_response, success_response, success_response, error_response, success_response]
    service = NotificationService(connection_error_max=1)
    await service.notify(get_test_session, "error", {"msg":"test"})

    calls = [call("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout),call("0.0.0.0:8001", json={"msg": "test"}, timeout=service.timeout), call("0.0.0.0:8003", json={"msg": "test"}, timeout=service.timeout)]
    mock_post.assert_has_calls(calls)
    error_observers = await get_observers_subscribed_to_event(get_test_session, "error")
    assert str(error_observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001'), Observer(id=4, webhook_url='0.0.0.0:8003')]"

    await service.notify(get_test_session, "freeze", {"msg":"test"})

    calls = [call("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout),call("0.0.0.0:8002", json={"msg": "test"}, timeout=service.timeout)]
    mock_post.assert_has_calls(calls)
    freeze_observers = await get_observers_subscribed_to_event(get_test_session, "freeze")
    assert str(freeze_observers) == "[Observer(id=3, webhook_url='0.0.0.0:8002')]"
