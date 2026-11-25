import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import ok
from app.core.db import get_db
from app.expense_protocol import ExpenseCalculate
from app.fee_protocol import (
    CalculateInput,
    RuleActivate,
    RuleEdit,
    RuleInput,
    RuleRecover,
    RuleRevision,
)
from app.services import auth, fees

router = APIRouter(prefix="/api/v1")


@router.get("/admin/fee-rules/{rule_id}/source-review")
async def review_source(
    rule_id: uuid.UUID,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request, await fees.review_source(db, request.app.state.settings, context.user, rule_id)
    )


@router.post("/admin/fee-rules/{rule_id}/recover")
async def recover(
    rule_id: uuid.UUID,
    body: RuleRecover,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await fees.recover(db, request.app.state.settings, context, rule_id, body))


@router.get("/admin/fee-rules")
async def rules(
    request: Request, context=Depends(auth.admin_context), db: AsyncSession = Depends(get_db)
):
    return ok(request, await fees.list_rules(db, request.app.state.settings, context.user))


@router.post("/admin/fee-rules", status_code=201)
async def create(
    body: RuleInput,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await fees.save_rule(db, request.app.state.settings, context, body))


@router.put("/admin/fee-rules/{rule_id}")
async def edit(
    rule_id: uuid.UUID,
    body: RuleEdit,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await fees.save_rule(db, request.app.state.settings, context, body, rule_id))


@router.post("/admin/fee-rules/{rule_id}/activate")
async def activate(
    rule_id: uuid.UUID,
    body: RuleActivate,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request, await fees.transition(db, request.app.state.settings, context, rule_id, body, True)
    )


@router.post("/admin/fee-rules/{rule_id}/suspend")
async def suspend(
    rule_id: uuid.UUID,
    body: RuleRevision,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await fees.transition(db, request.app.state.settings, context, rule_id, body, False),
    )


@router.post("/conversations/{conversation_id}/calculations", status_code=201)
async def calculate(
    conversation_id: uuid.UUID,
    body: CalculateInput,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await fees.calculate(db, request.app.state.settings, context, conversation_id, body),
    )


@router.get("/calculations/{calculation_id}")
async def read(
    calculation_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await fees.read_calculation(db, request.app.state.settings, context.user, calculation_id),
    )


@router.post("/conversations/{conversation_id}/expense-calculations", status_code=201)
async def calculate_expense(
    conversation_id: uuid.UUID,
    body: ExpenseCalculate,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(
        request,
        await fees.calculate(
            db, request.app.state.settings, context, conversation_id, body, expense=True
        ),
    )
