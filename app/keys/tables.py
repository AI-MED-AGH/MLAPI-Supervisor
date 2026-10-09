from sqlalchemy import JSON, BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str]
    secret_hash: Mapped[str]
    allowed_models: Mapped[list] = mapped_column(JSON, default=list)
    allow_all: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, default=None)  # unix seconds
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, default=None)  # unix seconds
    created_at: Mapped[int] = mapped_column(BigInteger)

    def __repr__(self) -> str:
        return f"ApiKey(id={self.id!r}, name={self.name!r})"
