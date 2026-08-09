"""Auth domain types — plain enums/dataclasses, not the ORM model
(`app.models.user.User`) and not API schemas (`app.schemas.auth`). Same
split every other package in this project uses.
"""

from dataclasses import dataclass
from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    VIEWER = "VIEWER"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


@dataclass
class AuthenticatedUser:
    """The identity attached to a request once its session cookie has been
    validated — never carries the password hash or the session token
    itself."""

    id: int
    email: str
    name: str
    role: UserRole
