import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ApiError
from app.core.security import csrf_token, hasher, now, password_valid
from app.models import KnowledgeBase, User
from app.schemas import (
    KBCreate,
    KBPatch,
    LoginInput,
    MembersInput,
    PasswordInput,
    ProfilePatch,
    UserCreate,
    UserPatch,
)
from app.services import auth, management

router = APIRouter(prefix="/api/v1")


def ok(request: Request, data) -> dict:
    return {"data": data, "request_id": request.state.request_id}


@router.post("/auth/login")
async def login(
    body: LoginInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    auth.check_origin(request)
    settings = request.app.state.settings
    user, family, raw = await auth.login(
        db, body.username, body.password.get_secret_value(), settings
    )
    auth.put_cookie(response, raw, family, settings)
    return ok(
        request, {**auth.token_result(user, family, settings), "user": await auth.me(db, user)}
    )


@router.get("/auth/csrf")
async def csrf(request: Request, db: AsyncSession = Depends(get_db)):
    row = await auth.refresh_record(
        db, request.cookies.get(request.app.state.settings.refresh_cookie)
    )
    token = None
    if auth.usable(row) and row[0].consumed_at is None:
        token = csrf_token(row[1].id, request.app.state.settings)
    return ok(request, {"csrf_token": token})


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    user, family, raw = await auth.rotate(request, db)
    auth.put_cookie(response, raw, family, request.app.state.settings)
    return ok(request, auth.token_result(user, family, request.app.state.settings))


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    auth.check_origin(request)
    row = await auth.refresh_record(
        db, request.cookies.get(request.app.state.settings.refresh_cookie), lock=True
    )
    if row is not None:
        auth.check_csrf(request, row[1])
        row[1].revoked_at, row[1].revoke_reason = now(), "logout"
        await db.commit()
    auth.clear_cookie(response, request.app.state.settings)


@router.get("/auth/me")
async def me(
    request: Request,
    context: auth.AuthContext = Depends(auth.current_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await auth.me(db, context.user))


@router.post("/auth/password", status_code=204)
async def password(
    body: PasswordInput,
    request: Request,
    response: Response,
    context: auth.AuthContext = Depends(auth.current_context),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(
        select(User)
        .where(User.id == context.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user.auth_version != context.version:
        raise auth.unauthorized()
    if not password_valid(body.old_password.get_secret_value(), user.password_hash):
        raise ApiError(400, "OLD_PASSWORD_INCORRECT", "原密码不正确")
    user.password_hash = hasher.hash(body.new_password.get_secret_value())
    user.must_change_password = False
    await auth.revoke_all(db, user, "password_changed")
    await db.commit()
    auth.clear_cookie(response, request.app.state.settings)


@router.get("/knowledge-bases")
async def list_kbs(
    request: Request,
    include_inactive: bool = False,
    context: auth.AuthContext = Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    if include_inactive:
        if context.user.role != "admin":
            raise ApiError(403, "ADMIN_REQUIRED", "此操作需要管理员权限")
        items = list(
            (
                await db.scalars(
                    select(KnowledgeBase).order_by(KnowledgeBase.created_at, KnowledgeBase.id)
                )
            ).all()
        )
    else:
        items = await auth.allowed_kbs(db, context.user)
    return ok(request, {"items": [auth.kb_out(kb) for kb in items], "total": len(items)})


@router.get("/knowledge-bases/{kb_id}")
async def read_kb(
    kb_id: uuid.UUID,
    request: Request,
    context: auth.AuthContext = Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    kb = next((kb for kb in await auth.allowed_kbs(db, context.user) if kb.id == kb_id), None)
    if kb is None:
        raise ApiError(404, "NOT_FOUND", "知识库不存在或不可访问")
    return ok(request, auth.kb_out(kb))


@router.get("/admin/users")
async def users(
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.users_page(db))


@router.post("/admin/users", status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.create_user(db, context, body))


@router.patch("/admin/users/{user_id}")
async def patch_user(
    user_id: uuid.UUID,
    body: UserPatch,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.patch_user(db, context, user_id, body))


@router.post("/admin/users/{user_id}/password-reset")
async def reset(
    user_id: uuid.UUID,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.reset_password(db, context, user_id))


@router.get("/admin/employees/{user_id}")
async def employee(
    user_id: uuid.UUID,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    from app.models import EmployeeProfile

    user = await db.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "员工不存在")
    return ok(request, auth.user_out(user, await db.get(EmployeeProfile, user_id)))


@router.patch("/admin/employees/{user_id}")
async def employee_patch(
    user_id: uuid.UUID,
    body: ProfilePatch,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.patch_profile(db, context, user_id, body))


@router.post("/admin/knowledge-bases", status_code=201)
async def kb_create(
    body: KBCreate,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.create_kb(db, context, body))


@router.patch("/admin/knowledge-bases/{kb_id}")
async def kb_patch(
    kb_id: uuid.UUID,
    body: KBPatch,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.patch_kb(db, context, kb_id, body))


@router.get("/admin/knowledge-bases/{kb_id}/members")
async def kb_members(
    kb_id: uuid.UUID,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.members(db, kb_id))


@router.put("/admin/knowledge-bases/{kb_id}/members")
async def kb_members_put(
    kb_id: uuid.UUID,
    body: MembersInput,
    request: Request,
    context: auth.AuthContext = Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await management.replace_members(db, context, kb_id, body))
