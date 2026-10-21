from sqlalchemy import BigInteger, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UsageSample(Base):
    """One point of a model's resource use. `ts` is the start of the bucket (unix seconds, UTC)."""

    __tablename__ = "usage_samples"
    __table_args__ = (UniqueConstraint("model", "tier", "ts", name="uq_usage_sample"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model: Mapped[str] = mapped_column(String(40))  # by name, not a foreign key: history outlives nothing it should not
    tier: Mapped[str] = mapped_column(String(3))
    ts: Mapped[int] = mapped_column(BigInteger)
    cpu_m_avg: Mapped[float] = mapped_column(Float)
    cpu_m_max: Mapped[float] = mapped_column(Float)
    mem_avg: Mapped[float] = mapped_column(Float)
    mem_max: Mapped[float] = mapped_column(Float)
    n: Mapped[int] = mapped_column(Integer, default=1)  # raw samples behind the point (weights the averages)
