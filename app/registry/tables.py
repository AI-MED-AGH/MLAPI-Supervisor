import time

from sqlalchemy import JSON, BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.registry import states


def _now() -> int:
    return int(time.time())


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    image: Mapped[str]
    mode: Mapped[str] = mapped_column(default="sync")  # sync | queue
    source: Mapped[str] = mapped_column(default="ghcr")  # ghcr | local
    state: Mapped[str] = mapped_column(default=states.PENDING_APPROVAL)
    state_detail: Mapped[str | None] = mapped_column(default=None)
    current_digest: Mapped[str | None] = mapped_column(default=None)
    previous_digest: Mapped[str | None] = mapped_column(default=None)
    pending_digest: Mapped[str | None] = mapped_column(default=None)
    failed_digest: Mapped[str | None] = mapped_column(default=None)  # not retried until a new digest appears
    approved_resources: Mapped[dict | None] = mapped_column(JSON, default=None)
    requested_resources: Mapped[dict | None] = mapped_column(JSON, default=None)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # env, secret_refs, idle_timeout_s, max_job_seconds
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=_now, onupdate=_now)
    last_checked_at: Mapped[int | None] = mapped_column(BigInteger, default=None)

    def __repr__(self) -> str:
        return f"Model(name={self.name!r}, state={self.state!r})"


class ResourceRequest(Base):
    __tablename__ = "resource_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    digest: Mapped[str]
    requested: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default=states.REQ_PENDING)
    decided_by: Mapped[str | None] = mapped_column(default=None)
    decided_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
    note: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[int] = mapped_column(BigInteger, default=_now)


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)
    digest: Mapped[str]
    status: Mapped[str]
    reason: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[int] = mapped_column(BigInteger, default=_now)
    finished_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
