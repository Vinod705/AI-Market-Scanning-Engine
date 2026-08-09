"""Pydantic schemas for /auth and /admin/users — the password hash and
session token never appear in any of these."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str
    role: str
    status: str
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None


class LoginResponse(BaseModel):
    user: UserOut


class CreateUserRequest(BaseModel):
    email: str
    name: str
    role: str = "VIEWER"


class CreateUserResponse(BaseModel):
    user: UserOut
    temporary_password: str


class ResetPasswordResponse(BaseModel):
    temporary_password: str


class UpdateUserRoleRequest(BaseModel):
    role: str
