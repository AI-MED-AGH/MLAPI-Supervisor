from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass

class Observer(Base):
    __tablename__ = "observers"

    id:Mapped[int] = mapped_column(primary_key=True)
    webhook_url:Mapped[str] = mapped_column(unique=True, index=True)
    connection_errors_count:Mapped[int] = mapped_column(default=0)

    subscriptions: Mapped[list["Subscription"]] = relationship(
            back_populates="observer",
            cascade="all, delete-orphan",
        )

    def __repr__(self)->str:
        return f"Observer(id={self.id!r}, webhook_url={self.webhook_url!r})"

class Subscription(Base):
    __tablename__ = "subscriptions"

    id:Mapped[int] = mapped_column(primary_key=True)
    observer_id:Mapped[int] = mapped_column(ForeignKey("observers.id"))
    event_type:Mapped[str]

    observer: Mapped["Observer"] = relationship(
        back_populates="subscriptions",
    )


    def __repr__(self)->str:
        return f"Subscription(id={self.id!r}, observer_id={self.observer_id!r}, event_type={self.event_type!r})"
