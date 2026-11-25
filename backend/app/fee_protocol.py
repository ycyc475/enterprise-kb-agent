from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.chat_protocol import TripContext
from app.schemas import Input


class RuleInput(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    kind: Literal["lodging", "daily", "office", "training", "hospitality"]
    grade: Literal["S1", "S2"] | None = None
    cities: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    ] = Field(default_factory=list, max_length=30)
    unit_rate: Decimal = Field(ge=0, le=Decimal("999999.99"), decimal_places=2, allow_inf_nan=False)
    application_cap: Decimal | None = Field(
        default=None, gt=0, le=Decimal("999999.99"), decimal_places=2, allow_inf_nan=False
    )
    reimbursement_ratio: Decimal = Field(
        default=Decimal("1"), gt=0, le=1, decimal_places=4, allow_inf_nan=False
    )
    max_quantity: int = Field(default=100, ge=1, le=100, strict=True)
    source_chunk_id: UUID
    source_quote: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def scope(self):
        self.cities = sorted(set(self.cities))
        if self.kind == "lodging" and (self.grade is None or not self.cities):
            raise ValueError("住宿规则必须指定职级和城市名单")
        if self.kind == "daily" and (self.grade is not None or self.cities):
            raise ValueError("当前补贴规则统一适用，不区分职级或城市")
        if self.kind in {"lodging", "daily"} and (
            self.application_cap is not None or self.reimbursement_ratio != 1
        ):
            raise ValueError("差旅标准不使用新增费用的单次上限和报销比例")
        if self.kind in {"office", "training", "hospitality"}:
            if self.grade is not None or self.cities or self.application_cap is None:
                raise ValueError("新增费用统一适用，必须设置单次上限，不能指定职级或城市")
            if self.kind != "training" and self.reimbursement_ratio != 1:
                raise ValueError("仅培训类允许配置报销比例")
        return self


class RuleEdit(RuleInput):
    expected_revision: int = Field(ge=1)


class RuleRevision(Input):
    expected_revision: int = Field(ge=1)


class RuleActivate(RuleRevision):
    source_checked: Literal[True]


class RuleRecover(RuleRevision):
    expected_source_version_id: UUID
    source_chunk_id: UUID
    source_quote: str = Field(min_length=1, max_length=2000)
    unit_rate: Decimal = Field(ge=0, le=Decimal("999999.99"), decimal_places=2, allow_inf_nan=False)
    source_checked: Literal[True]


class CalculateInput(Input):
    client_request_id: UUID
    context_revision: int = Field(ge=1)
    profile_revision: int = Field(ge=1)
    # Explicit confirmation only: natural-language proposals never write this field themselves.
    trip_context: TripContext | None = None
