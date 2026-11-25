from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, model_validator

from app.schemas import Input

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class TripContext(Input):
    destination: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None
    ) = None
    travel_days: Annotated[StrictInt, Field(ge=1, le=30)] | None = None
    lodging_nights: Annotated[StrictInt, Field(ge=0, le=29)] | None = None

    @model_validator(mode="after")
    def nights_fit(self):
        if (
            self.travel_days is not None
            and self.lodging_nights is not None
            and self.lodging_nights >= self.travel_days
        ):
            raise ValueError("住宿晚数必须小于出差天数")
        return self


class ConversationCreate(Input):
    title: Title = "新会话"
    knowledge_base_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=30)


class ConversationPatch(Input):
    title: Title | None = None
    knowledge_base_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=30)
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def nonempty(self):
        if self.title is None and self.knowledge_base_ids is None:
            raise ValueError("请提供需要修改的字段")
        return self


class TripPatch(Input):
    trip_context: TripContext
    expected_revision: int = Field(ge=1)


class MessageSubmit(Input):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
    client_request_id: UUID
    context_revision: int = Field(ge=1)
    trip_context: TripContext | None = None
    budget_confirmed_revision: int | None = Field(default=None, ge=1)
    budget_profile_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def budget_confirmation(self):
        if self.budget_confirmed_revision is not None and (
            self.trip_context is not None
            or self.budget_confirmed_revision != self.context_revision
            or self.budget_profile_revision is None
        ):
            raise ValueError("预算确认必须对应已保存的当前条件版本")
        if self.budget_profile_revision is not None and self.budget_confirmed_revision is None:
            raise ValueError("预算资料确认必须与条件版本一同提交")
        return self


class Quote(Input):
    chunk_id: str = Field(
        min_length=1,
        max_length=80,
        description="本轮证据中提供的短编号，例如E1、E2；直接选择，不得编造",
    )
    quote: str = Field(
        default="",
        max_length=800,
        description="留空即可，由服务端生成真实原文；不要改写或自行拼接摘录",
    )


class Answer(Input):
    """提交本轮的最终结果。制度回答必须引用实际检索原文。"""

    status: Literal["answered", "needs_clarification", "insufficient_evidence"]
    basis: Literal[
        "documents",
        "employee_profile",
        "clarification",
        "unavailable",
        "calculation",
        "trip_proposal",
        "system_info",
        "expense_proposal",
    ]
    answer: str = Field(min_length=1, max_length=6000)
    citations: list[Quote] = Field(max_length=12)
    missing_fields: list[
        Literal[
            "destination", "travel_days", "lodging_nights", "question", "quantity", "actual_amount"
        ]
    ] = Field(max_length=6)
