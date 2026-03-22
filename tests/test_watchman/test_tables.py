from app.watchman import Observer, Subscribtion, Event, Base
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

def test_error_observers(engine):
    stmt = (
        select(Observer.webhook_url)
        .join(Observer.events)
        .where(Event.name == "error")
    )
    with Session(engine) as session:
        query_result = set()
        results = set(session.execute(stmt).scalars().all())
        for url in results:
            query_result.add(url)
        assert query_result == {"https://cool.service/webhook"}

def test_freeze_observers(engine):
    stmt = (
        select(Observer.webhook_url)
        .join(Observer.events)
        .where(Event.name == "freeze")
    )
    with Session(engine) as session:
        query_result = set()
        results = set(session.execute(stmt).scalars().all())
        for url in results:
            query_result.add(url) 
        assert query_result == {"https://cool.service/webhook", "https://ilikefreeze.com/freeze"}

def test_all_observer(engine):
    stmt = (
        select(Event.name)
        .join(Event.observers)
        .where(Observer.webhook_url == "https://cool.service/webhook")
    )
    with Session(engine) as session:
        query_result = set()
        results = session.execute(stmt).scalars().all()
        for url in results:
            query_result.add(url)
        assert query_result == {"error","freeze"}

def insert_test_records(engine):
    with Session(engine) as session:
        error_event = Event(name="error")
        freeze_event = Event(name="freeze")

        all_observer = Observer(webhook_url="https://cool.service/webhook")
        freeze_observer = Observer(webhook_url="https://ilikefreeze.com/freeze")

        all_observer.events.append(error_event)
        all_observer.events.append(freeze_event)

        freeze_observer.events.append(freeze_event)

        session.add_all([error_event, freeze_event, all_observer, freeze_event])
        session.commit()


def create_tables(engine):
    Base.metadata.create_all(engine)

    print("\n--- Tables Created!---\n")


def test_append():
    engine = create_engine("sqlite:///:memory:", echo=True)

    create_tables(engine)
    insert_test_records(engine)

    test_error_observers(engine) 
    test_freeze_observers(engine)
    test_all_observer(engine)


def test():
    test_append()

if __name__ == "__main__":
    test()
