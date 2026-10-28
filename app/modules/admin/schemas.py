from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

from app.modules.auth.schemas import UserCreate


class AdminUserCreate(UserCreate):
    """Account creation input for authenticated administrators only."""

    role: Literal["user", "admin"] = "user"


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: StrictBool
