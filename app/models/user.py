"""User ORM model — dashboard login accounts (ADMIN/VIEWER).

`password_hash` is the only credential material ever persisted (bcrypt,
see `app.auth.passwords`) — never a plaintext password, never a session
token. Session tokens live only in Redis (`app.auth.session_store`),
not here.
"""

from datetime import datetime as datetime_

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "ADMIN" | "VIEWER"
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE"
    )  # "ACTIVE" | "DISABLED"
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime_] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime_ | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"User(email={self.email!r}, role={self.role!r}, status={self.status!r})"
