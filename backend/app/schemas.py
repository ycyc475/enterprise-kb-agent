from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

Username = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[a-zA-Z0-9_.-]{3,64}$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Department = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    username: Username
    password: SecretStr

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value: SecretStr):
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("密码长度不合法")
        return value


class PasswordInput(Input):
    old_password: SecretStr
    new_password: SecretStr

    @field_validator("new_password")
    @classmethod
    def strong_password(cls, value: SecretStr):
        if not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("密码长度应为12至128个字符")
        return value


class UserCreate(Input):
    username: Username
    role: Literal["admin", "employee"] = "employee"
    password: SecretStr | None = None
    display_name: Name
    department: Department
    grade: Literal["S1", "S2"] = "S1"

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: SecretStr | None):
        if value is not None and not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("密码长度应为12至128个字符")
        return value


class UserPatch(Input):
    role: Literal["admin", "employee"] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.role is None and self.is_active is None:
            raise ValueError("请提供需要修改的字段")
        return self


class ProfilePatch(Input):
    display_name: Name
    department: Department
    grade: Literal["S1", "S2"]
    expected_revision: int = Field(ge=1)


class KBCreate(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    description: str = Field(default="", max_length=1000)
    is_active: bool = True


class KBPatch(KBCreate):
    expected_revision: int = Field(ge=1)


class MembersInput(Input):
    user_ids: list[UUID] = Field(max_length=200)
    expected_revision: int = Field(ge=1)
