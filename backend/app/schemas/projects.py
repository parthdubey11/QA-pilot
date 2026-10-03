from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.core.targets import blocked_reason


def _strip_not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Can't be blank")
    return value.strip()


def _allowed_target(value: AnyHttpUrl | None) -> AnyHttpUrl | None:
    if value is not None and (reason := blocked_reason(str(value))):
        raise ValueError(f"This address can't be tested: {reason}")
    return value


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: AnyHttpUrl
    # The "only test sites you own or are authorised to test" notice checkbox.
    authorised_testing_confirmed: bool

    _name = field_validator("name")(_strip_not_blank)
    _base_url = field_validator("base_url")(_allowed_target)

    @field_validator("authorised_testing_confirmed")
    @classmethod
    def must_confirm(cls, value: bool) -> bool:
        if not value:
            raise ValueError("You must confirm you own this site or are authorised to test it")
        return value


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: AnyHttpUrl | None = None
    is_own_site: bool | None = None
    security_probes_enabled: bool | None = None

    _base_url = field_validator("base_url")(_allowed_target)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        return None if value is None else _strip_not_blank(value)


class ProjectRead(BaseModel):
    id: str
    name: str
    base_url: str
    is_own_site: bool
    security_probes_enabled: bool
    authorised_at: datetime
    created_at: datetime
    updated_at: datetime


class CredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=500)

    _label = field_validator("label")(_strip_not_blank)


class CredentialUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    username: str | None = Field(default=None, min_length=1, max_length=320)
    password: str | None = Field(default=None, min_length=1, max_length=500)


class CredentialRead(BaseModel):
    """Never includes the password."""

    id: str
    label: str
    username: str
    created_at: datetime
    updated_at: datetime
