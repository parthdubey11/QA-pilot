import hmac

from fastapi import APIRouter, HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.core.config import get_settings
from app.core.deps import CurrentUser, parse_object_id
from app.core.ratelimit import login_limiter
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    InvalidTokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_TAKEN = "An account with this email already exists."


def _tokens(user: User) -> dict:
    user_id = str(user.id)
    return {
        "access_token": create_token(user_id, "access"),
        "refresh_token": create_token(user_id, "refresh"),
        "expires_in": get_settings().access_token_minutes * 60,
    }


def _user_read(user: User) -> UserRead:
    return UserRead(id=str(user.id), email=user.email, name=user.name, created_at=user.created_at)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest) -> AuthResponse:
    code = get_settings().registration_code
    if code and not hmac.compare_digest(body.invite_code.strip().encode(), code.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A valid invite code is needed to create an account here.")
    email = body.email.lower()
    if await User.find_one(User.email == email):
        raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN)
    user = User(email=email, name=body.name, password_hash=hash_password(body.password))
    try:
        await user.insert()
    except DuplicateKeyError:  # lost a race with a concurrent registration (unique index on email)
        raise HTTPException(status.HTTP_409_CONFLICT, EMAIL_TAKEN) from None
    return AuthResponse(user=_user_read(user), **_tokens(user))


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    email = body.email.lower()
    if (wait := login_limiter.retry_after(email)) is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Too many failed attempts. Try again in {wait // 60 + 1} minutes.",
                            headers={"Retry-After": str(wait)})
    user = await User.find_one(User.email == email)
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok:
        login_limiter.failed(email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")
    login_limiter.succeeded(email)
    return AuthResponse(user=_user_read(user), **_tokens(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    try:
        user_id = parse_object_id(decode_token(body.refresh_token, "refresh"))
    except InvalidTokenError:
        user_id = None
    user = await User.get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token.")
    return TokenResponse(**_tokens(user))


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return _user_read(user)
