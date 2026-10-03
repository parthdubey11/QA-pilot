from datetime import datetime
from typing import Annotated

from beanie import Document, Indexed
from pydantic import Field

from app.models.base import utcnow


class User(Document):
    email: Annotated[str, Indexed(unique=True)]  # stored lower-cased
    name: str
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "users"
