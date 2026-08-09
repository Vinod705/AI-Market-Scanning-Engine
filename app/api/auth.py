"""Login/logout/me/change-password — the only unauthenticated route here
is POST /auth/login itself; everything else requires a valid session.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SESSION_COOKIE_NAME, get_current_user, get_session_store
from app.auth.models import AuthenticatedUser
from app.auth.session_store import SessionStore
from app.config.settings import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.repositories.user_repository import UserRepository
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse, UserOut
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
Store = Annotated[SessionStore, Depends(get_session_store)]
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, response: Response, session: DbSession, store: Store
) -> LoginResponse:
    settings = get_settings()
    service = UserService(UserRepository(session), store)
    user = await service.authenticate(payload.email.strip().lower(), payload.password)
    await session.commit()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    token = await store.create(user_id=user.id, role=user.role)
    _set_session_cookie(response, token, settings)
    return LoginResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    store: Store,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> None:
    """Idempotent: clearing an already-expired/missing session still
    succeeds and still clears the cookie — a stale local cookie is a
    valid reason to hit logout, not an error."""
    if session_id:
        await store.delete(session_id)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser, session: DbSession) -> UserOut:
    db_user = await UserRepository(session).get_by_id(user.id)
    if db_user is None:
        raise NotFoundError("User not found")
    return UserOut.model_validate(db_user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, session: DbSession, store: Store
) -> None:
    repo = UserRepository(session)
    db_user = await repo.get_by_id(user.id)
    if db_user is None:
        raise NotFoundError("User not found")
    service = UserService(repo, store)
    ok = await service.change_password(
        db_user, current_password=payload.current_password, new_password=payload.new_password
    )
    await session.commit()
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect"
        )
