from typing import Annotated

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import InvalidTokenError, decode_token
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def parse_object_id(value: str) -> PydanticObjectId | None:
    """Parse a document id from a URL or token; None if it isn't a valid ObjectId."""
    try:
        return PydanticObjectId(value)
    except (InvalidId, TypeError):
        return None


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        user_id = parse_object_id(decode_token(credentials.credentials, "access"))
    except InvalidTokenError:
        raise _unauthorized("Invalid or expired token") from None
    user = await User.get(user_id) if user_id else None
    if user is None:
        raise _unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
