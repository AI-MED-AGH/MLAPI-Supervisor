from re import I
from app.watchman import Observer, Subscribtion, Event, Base, events_exist, insert_new_subscription
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.watchman.schemas import SubscriptionSchema

def test_event_exists(engine):
    with Session(engine) as session:
        error = Event(name="error")
        freeze = Event(name="freeze")
        cool_event = Event(name="cool_event")

        session.add_all([error, freeze, cool_event])
        session.commit()

        assert events_exist(session, ["error", "freeze", "cool_event"])
        assert events_exist(session, ["freeze", "cool_event"])
        assert events_exist(session, ["freeze"])

        assert not events_exist(session, ["error", "freeze", "cool_event", "random_event"])
        assert not events_exist(session, ["random_event"])
        assert not events_exist(session, [])

def get_event_observers(session, event_name):
    query = (
            select(Observer.webhook_url)
            .join(Observer.events)
            .where(Event.name == event_name)
            )
    return set(session.scalars(query).all())

def get_observers_event(session, webhook):
    query = (
            select(Event.name)
            .join(Event.observers)
            .where(Observer.webhook_url == webhook)
        )
    return set(session.scalars(query).all())

def test_insert_new_subscription(engine):
    with Session(engine) as session:
        insert_new_subscription(session, SubscriptionSchema(webhook_url="https://cool.service/webhook", event_types=["cool_event"])) 

        result = get_event_observers(session, "cool_event")
        assert result == {"https://cool.service/webhook"}, f"got: {result}"

        result = get_observers_event(session, "https://cool.service/webhook")
        assert result == {"cool_event"} , f"got: {result}"

        insert_new_subscription(session, SubscriptionSchema(webhook_url="https://cool.service/inny_webhook", event_types=["cool_event", "freeze"])) 
        insert_new_subscription(session, SubscriptionSchema(webhook_url="https://cool.service/webhook", event_types=["cool_event"])) 
        insert_new_subscription(session, SubscriptionSchema(webhook_url="https://cool.service/webhook", event_types=["error"])) 
        
        result = get_event_observers(session, "cool_event")
        assert result == {"https://cool.service/webhook", "https://cool.service/inny_webhook"}, f"got: {result}"

        result = get_observers_event(session, "https://cool.service/webhook")
        assert result == {"cool_event", "error"} , f"got: {result}"

        result = get_observers_event(session, "https://cool.service/inny_webhook")
        assert result == {"cool_event", "freeze"}, f"got: {result}"

def create_tables(engine):
    Base.metadata.create_all(engine)

    print("\n--- Tables Created!---\n")

def test():
    engine = create_engine("sqlite:///:memory:")
    create_tables(engine)

    test_event_exists(engine)
    test_insert_new_subscription(engine)


if __name__ == "__main__":
    test()
