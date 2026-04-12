from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from typing import List


class Base(DeclarativeBase):
    pass

class Observer(Base):
    __tablename__ = "observers"
   
    id:Mapped[int] = mapped_column(primary_key=True) 
    webhook_url:Mapped[str]
    connection_errors_count:Mapped[int] = mapped_column(default=0)

    events: Mapped[List["Event"]] = relationship(
            secondary="subscriptions", 
            back_populates="observers",
        )

    def __repr__(self)->str:
        return f"Observer(id={self.id!r}, webhook_url={self.webhook_url!r})"

class Event(Base):
    __tablename__ = "events"

    id:Mapped[int] = mapped_column(primary_key=True)
    name:Mapped[str]

    observers: Mapped[List["Observer"]] = relationship(
            secondary="subscriptions",
            back_populates="events",
            cascade="all, delete",
         )
    
    def __repr__(self)->str:
        return f"Event(id={self.id!r}, name={self.name!r})"

class Subscription(Base):
    __tablename__ = "subscriptions"

    id:Mapped[int] = mapped_column(primary_key=True)
    observer_id:Mapped[int] = mapped_column(ForeignKey("observers.id"))
    event_id:Mapped[int] = mapped_column(ForeignKey("events.id"))


    def __repr__(self)->str:
        return f"Subscription(id={self.id!r}, observer_id={self.observer_id!r}, event_id={self.event_id!r})"
