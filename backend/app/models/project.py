from datetime import datetime
from typing import Annotated

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

from app.models.base import utcnow


class Project(Document):
    owner_id: Annotated[PydanticObjectId, Indexed()]
    name: str
    base_url: str
    # When the owner ticked "I own this site or am authorised to test it".
    authorised_at: datetime = Field(default_factory=utcnow)
    # Security-style probes (XSS/SQLi strings) are only allowed on sites the owner marks as their own.
    is_own_site: bool = False
    security_probes_enabled: bool = False
    created_at: Annotated[datetime, Indexed()] = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "projects"


class Credential(Document):
    """A test-site login. The password is Fernet-encrypted and never returned by the API."""

    project_id: Annotated[PydanticObjectId, Indexed()]
    label: str
    username: str
    password_encrypted: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "credentials"
