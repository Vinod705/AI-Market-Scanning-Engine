"""Admin -> Users management API. Every route here requires `require_admin`
— a VIEWER gets a 403, not a redirect or a degraded view. See
`app.auth.dependencies.require_admin`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_session_store, require_admin
from app.auth.models import AuthenticatedUser, UserRole
from app.auth.session_store import SessionStore
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    CreateUserRequest,
    CreateUserResponse,
    ResetPasswordResponse,
    UpdateUserRoleRequest,
    UserOut,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/admin/users", tags=["admin"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Store = Annotated[SessionStore, Depends(get_session_store)]
Admin = Annotated[AuthenticatedUser, Depends(require_admin)]


async def _get_user_or_404(repo: UserRepository, user_id: int) -> User:
    user = await repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError(f"No user with id {user_id}")
    return user


async def _guard_not_last_admin(repo: UserRepository, target: User) -> None:
    """Blocks disabling/deleting/demoting the last remaining ACTIVE admin —
    without this, an admin could lock every admin out of the system with
    no way back in short of direct database access."""
    if target.role != UserRole.ADMIN.value or target.status != "ACTIVE":
        return
    all_users = await repo.list_all()
    active_admins = [
        u for u in all_users if u.role == UserRole.ADMIN.value and u.status == "ACTIVE"
    ]
    if len(active_admins) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last remaining active admin account",
        )


@router.get("", response_model=list[UserOut])
async def list_users(admin: Admin, session: DbSession) -> list[UserOut]:
    users = await UserRepository(session).list_all()
    return [UserOut.model_validate(u) for u in users]


@router.post("", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest, admin: Admin, session: DbSession, store: Store
) -> CreateUserResponse:
    try:
        role = UserRole(payload.role.upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="role must be ADMIN or VIEWER"
        ) from exc

    repo = UserRepository(session)
    email = payload.email.strip().lower()
    if await repo.get_by_email(email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists"
        )

    service = UserService(repo, store)
    created = await service.create_viewer(email=email, name=payload.name.strip(), role=role)
    await session.commit()
    return CreateUserResponse(
        user=UserOut.model_validate(created.user), temporary_password=created.temporary_password
    )


@router.patch("/{user_id}/disable", response_model=UserOut)
async def disable_user(user_id: int, admin: Admin, session: DbSession, store: Store) -> UserOut:
    repo = UserRepository(session)
    target = await _get_user_or_404(repo, user_id)
    await _guard_not_last_admin(repo, target)
    await UserService(repo, store).disable(target)
    await session.commit()
    return UserOut.model_validate(target)


@router.patch("/{user_id}/enable", response_model=UserOut)
async def enable_user(user_id: int, admin: Admin, session: DbSession, store: Store) -> UserOut:
    repo = UserRepository(session)
    target = await _get_user_or_404(repo, user_id)
    await UserService(repo, store).enable(target)
    await session.commit()
    return UserOut.model_validate(target)


@router.patch("/{user_id}/role", response_model=UserOut)
async def change_role(
    user_id: int, payload: UpdateUserRoleRequest, admin: Admin, session: DbSession, store: Store
) -> UserOut:
    try:
        role = UserRole(payload.role.upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="role must be ADMIN or VIEWER"
        ) from exc

    repo = UserRepository(session)
    target = await _get_user_or_404(repo, user_id)
    if role != UserRole.ADMIN:
        await _guard_not_last_admin(repo, target)
    await UserService(repo, store).change_role(target, role)
    await session.commit()
    return UserOut.model_validate(target)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: int, admin: Admin, session: DbSession, store: Store
) -> ResetPasswordResponse:
    repo = UserRepository(session)
    target = await _get_user_or_404(repo, user_id)
    temp_password = await UserService(repo, store).reset_password(target)
    await session.commit()
    return ResetPasswordResponse(temporary_password=temp_password)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin: Admin, session: DbSession, store: Store) -> None:
    repo = UserRepository(session)
    target = await _get_user_or_404(repo, user_id)
    await _guard_not_last_admin(repo, target)
    await UserService(repo, store).delete(target)
    await session.commit()
