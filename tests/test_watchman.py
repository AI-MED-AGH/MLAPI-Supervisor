import pytest
import httpx
from app.main import app
from app.watchman import Base, Event, NotificationService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from app.watchman.database import get_session
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock, call
from app.watchman.queries import get_observers_subscribed_to_event, insert_new_subscription
from app.watchman.schemas import SubscriptionSchema

DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool  
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def get_test_session():
    Base.metadata.create_all(bind=engine)
    testSession = TestingSessionLocal()
    
    error = Event(name="error")
    freeze = Event(name="freeze")
    testSession.add_all([error, freeze])
    testSession.commit()
    testSession.refresh(error)
    testSession.refresh(freeze)
    
    yield testSession 
    
    testSession.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client(get_test_session):
    def override_get_session():
        try:
            yield get_test_session
        finally:
            pass 
            
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

def test_subscribe(client):
    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "wrong":["error"]}) 
    assert response.status_code == 422

    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":[]})
    assert response.status_code == 404

    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":["error"]}) 
    assert response.status_code == 201

def test_insert_subscription(client, get_test_session):
    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":["error"]}) 
    assert response.status_code == 201
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8001", "event_types":["error"]}) 
    assert response.status_code == 201
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"
    
def test_notify_subscribers(client, get_test_session):
    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8000", "event_types":["error"]}) 
    assert response.status_code == 201
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    response = client.post("/observers/", json={"webhook_url":"0.0.0.0:8001", "event_types":["error"]}) 
    assert response.status_code == 201
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

@patch("httpx.Client.post") 
def test_notify_success(mock_post,get_test_session):
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8000", event_types=["error"]))

    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None 
    mock_post.return_value = mock_response 

    service = NotificationService()
    service.notify(get_test_session, event="error", payload={"msg": "test"})

    mock_post.assert_called_once_with("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout)
    
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8001", event_types=["error"]))

    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None 
    mock_post.return_value = mock_response 

    service.notify(get_test_session, event="error", payload={"msg": "test"})
    
    calls = [call("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout), call("0.0.0.0:8001", json={"msg": "test"}, timeout=service.timeout)]
    mock_post.assert_has_calls(calls, any_order=True)
    
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"


@patch("httpx.Client.post") 
def test_notify_error(mock_post,get_test_session):
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8000", event_types=["error"]))
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8001", event_types=["error"]))
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    success_response = MagicMock()
    success_response.raise_for_status.return_value = None 

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = httpx.ConnectError("Server Error")

    mock_post.side_effect = [success_response, error_response, success_response, success_response, success_response, error_response, success_response, error_response] 
    service = NotificationService(connection_error_max=1)

    # 1 notification 
    service.notify(get_test_session, event="error", payload={"msg": "test"})
    
    mock_post.assert_called()
    
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    # 2 notification 
    service.notify(get_test_session, event="error", payload={"msg": "test"})
    
    mock_post.assert_called()
    
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    # 3 notification 
    service.notify(get_test_session, event="error", payload={"msg": "test"})
    
    mock_post.assert_called()
    
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001')]"

    # 4 notification 
    service.notify(get_test_session, event="error", payload={"msg": "test"})
    
    mock_post.assert_called()
    
    observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000')]"

@patch("httpx.Client.post")
def test_many_subs(mock_post, get_test_session):
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8000", event_types=["error", "freeze"]))
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8001", event_types=["error"]))
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8002", event_types=["freeze"]))
    insert_new_subscription(get_test_session, SubscriptionSchema(webhook_url="0.0.0.0:8003", event_types=["error"]))
    error_observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(error_observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001'), Observer(id=4, webhook_url='0.0.0.0:8003')]"
    freeze_observers = get_observers_subscribed_to_event(get_test_session, "freeze")
    assert str(freeze_observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=3, webhook_url='0.0.0.0:8002')]"

    success_response = MagicMock()
    success_response.raise_for_status.return_value = None 

    error_response = MagicMock()
    error_response.raise_for_status.side_effect = httpx.ConnectError("Server Error")

    mock_post.side_effect = [error_response, success_response, success_response, error_response, success_response] 
    service = NotificationService(connection_error_max=1)
    service.notify(get_test_session, "error", {"msg":"test"})

    calls = [call("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout),call("0.0.0.0:8001", json={"msg": "test"}, timeout=service.timeout), call("0.0.0.0:8003", json={"msg": "test"}, timeout=service.timeout)]
    mock_post.assert_has_calls(calls)
    error_observers = get_observers_subscribed_to_event(get_test_session, "error")
    assert str(error_observers) == "[Observer(id=1, webhook_url='0.0.0.0:8000'), Observer(id=2, webhook_url='0.0.0.0:8001'), Observer(id=4, webhook_url='0.0.0.0:8003')]"

    service.notify(get_test_session, "freeze", {"msg":"test"})

    calls = [call("0.0.0.0:8000", json={"msg": "test"}, timeout=service.timeout),call("0.0.0.0:8002", json={"msg": "test"}, timeout=service.timeout)] 
    mock_post.assert_has_calls(calls)
    freeze_observers = get_observers_subscribed_to_event(get_test_session, "freeze")
    assert str(freeze_observers) == "[Observer(id=3, webhook_url='0.0.0.0:8002')]"
    
