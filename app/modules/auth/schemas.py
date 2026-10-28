from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints


Username = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=3,
                           max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
]


class UserCreate(BaseModel):
    """Registration and local provisioning input; role is never client supplied."""

    model_config = ConfigDict(extra="forbid")

    username: Username
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username
    password: SecretStr = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    role: Literal["user", "admin"]
    is_active: bool
    created_at: datetime
