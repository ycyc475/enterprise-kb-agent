from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt

from app.schemas import Input

ExpenseKind = Literal["office", "training", "hospitality"]


class ExpenseContext(Input):
    kind: ExpenseKind
    quantity: Annotated[StrictInt, Field(ge=1, le=100)] | None = None
    actual_amount: Decimal | None = Field(
        default=None, ge=0, le=Decimal("999999.99"), decimal_places=2, allow_inf_nan=False
    )


class ExpenseCalculate(Input):
    client_request_id: UUID
    context_revision: int = Field(ge=1)
    profile_revision: int = Field(ge=1)
    expense_context: ExpenseContext
