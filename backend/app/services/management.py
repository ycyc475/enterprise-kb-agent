import secrets
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.security import hasher, now
from app.models import AuthTokenFamily, EmployeeProfile, KBGrant, KnowledgeBase, User
from app.schemas import KBCreate, KBPatch, MembersInput, ProfilePatch, UserCreate, UserPatch
from app.services import audit
from app.services.auth import AuthContext, kb_out, revoke_all, unauthorized, user_out


async def lock_admin(db: AsyncSession, context: AuthContext):
    # Small single-enterprise administration: serialize security changes to avoid lockouts/races.
    await db.execute(text("SELECT pg_advisory_xact_lock(8174201)"))
    user = await db.scalar(
        select(User)
        .where(User.id == context.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    family = await db.get(AuthTokenFamily, context.family.id, populate_existing=True)
    if (
        user is None
        or not user.is_active
        or user.auth_version != context.version
        or family is None
        or family.revoked_at is not None
    ):
        raise unauthorized()
    if user.role != "admin":
        raise ApiError(403, "ADMIN_REQUIRED", "此操作需要管理员权限")


async def users_page(db: AsyncSession) -> dict:
    rows = (
        await db.execute(
            select(User, EmployeeProfile)
            .outerjoin(EmployeeProfile, User.id == EmployeeProfile.user_id)
            .order_by(User.created_at, User.id)
        )
    ).all()
    return {"items": [user_out(user, profile) for user, profile in rows], "total": len(rows)}


async def create_user(db: AsyncSession, actor: AuthContext, body: UserCreate) -> dict:
    await lock_admin(db, actor)
    raw = body.password.get_secret_value() if body.password else secrets.token_urlsafe(16)
    user = User(
        username=body.username,
        role=body.role,
        password_hash=hasher.hash(raw),
        must_change_password=True,
    )
    db.add(user)
    try:
        await db.flush()
        profile = EmployeeProfile(
            user_id=user.id,
            display_name=body.display_name,
            department=body.department,
            grade=body.grade,
            updated_by=actor.user.id,
        )
        db.add(profile)
        audit.record(db, "account", user.id, "account.created", actor, after=user)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, "USERNAME_EXISTS", "账号已存在") from None
    return {
        "user": user_out(user, profile),
        "temporary_password": raw if body.password is None else None,
    }


async def patch_user(db: AsyncSession, actor: AuthContext, user_id: uuid.UUID, body: UserPatch):
    await lock_admin(db, actor)
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "账号不存在")
    if user.id == actor.user.id and (body.is_active is False or body.role == "employee"):
        raise ApiError(
            409, "SELF_ADMIN_PROTECTED", "不能停用自己的管理员账号或移除自己的管理员角色"
        )
    before = audit.snapshot("account", user)
    changed = (body.role is not None and body.role != user.role) or (
        body.is_active is not None and body.is_active != user.is_active
    )
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if changed:
        await revoke_all(db, user, "account_changed")
        audit.record(db, "account", user.id, "account.updated", actor, before=before, after=user)
    await db.commit()
    return user_out(user, await db.get(EmployeeProfile, user.id))


async def reset_password(db: AsyncSession, actor: AuthContext, user_id: uuid.UUID):
    await lock_admin(db, actor)
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "账号不存在")
    if user.id == actor.user.id:
        raise ApiError(409, "USE_PASSWORD_CHANGE", "请通过个人资料中的修改密码功能操作")
    before = audit.snapshot("account", user)
    raw = secrets.token_urlsafe(16)
    user.password_hash, user.must_change_password = hasher.hash(raw), True
    await revoke_all(db, user, "password_reset")
    audit.record(db, "account", user.id, "account.password_reset", actor, before=before, after=user)
    await db.commit()
    return {"temporary_password": raw, "must_change_password": True}


async def patch_profile(
    db: AsyncSession, actor: AuthContext, user_id: uuid.UUID, body: ProfilePatch
):
    await lock_admin(db, actor)
    profile = await db.get(EmployeeProfile, user_id, with_for_update=True)
    if profile is None:
        raise ApiError(404, "NOT_FOUND", "员工资料不存在")
    if profile.revision != body.expected_revision:
        raise ApiError(409, "PROFILE_CHANGED", "员工资料已更新，请刷新后重新修改")
    before = audit.snapshot("profile", profile)
    profile.display_name, profile.department, profile.grade = (
        body.display_name,
        body.department,
        body.grade,
    )
    profile.revision += 1
    profile.updated_by = actor.user.id
    audit.record(db, "profile", user_id, "profile.updated", actor, before=before, after=profile)
    await db.commit()
    return user_out(await db.get(User, user_id), profile)


async def create_kb(db: AsyncSession, actor: AuthContext, body: KBCreate):
    await lock_admin(db, actor)
    kb = KnowledgeBase(**body.model_dump(), created_by=actor.user.id)
    db.add(kb)
    try:
        await db.flush()
        audit.record(db, "knowledge_base", kb.id, "knowledge_base.created", actor, after=kb)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, "KB_NAME_EXISTS", "知识库名称已存在") from None
    return kb_out(kb)


async def patch_kb(db: AsyncSession, actor: AuthContext, kb_id: uuid.UUID, body: KBPatch):
    await lock_admin(db, actor)
    kb = await db.get(KnowledgeBase, kb_id, with_for_update=True)
    if kb is None:
        raise ApiError(404, "NOT_FOUND", "知识库不存在")
    if kb.revision != body.expected_revision:
        raise ApiError(409, "KB_CHANGED", "知识库已更新，请刷新后重试")
    before = audit.snapshot("knowledge_base", kb)
    kb.name, kb.description, kb.is_active = body.name, body.description, body.is_active
    kb.revision += 1
    try:
        if before["is_active"] and not kb.is_active:
            from app.services.fees import suspend_kb

            await suspend_kb(db, kb.id, actor)
        audit.record(
            db, "knowledge_base", kb.id, "knowledge_base.updated", actor, before=before, after=kb
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(409, "KB_NAME_EXISTS", "知识库名称已存在") from None
    return kb_out(kb)


async def members(db: AsyncSession, kb_id: uuid.UUID) -> dict:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise ApiError(404, "NOT_FOUND", "知识库不存在")
    ids = (
        await db.scalars(
            select(KBGrant.user_id).where(KBGrant.kb_id == kb_id, KBGrant.revoked_at.is_(None))
        )
    ).all()
    return {"user_ids": [str(value) for value in ids], "revision": kb.revision}


async def replace_members(
    db: AsyncSession, actor: AuthContext, kb_id: uuid.UUID, body: MembersInput
):
    await lock_admin(db, actor)
    kb = await db.get(KnowledgeBase, kb_id, with_for_update=True)
    if kb is None:
        raise ApiError(404, "NOT_FOUND", "知识库不存在")
    if kb.revision != body.expected_revision:
        raise ApiError(409, "KB_CHANGED", "授权范围已更新，请刷新后重试")
    wanted = set(body.user_ids)
    known = (
        set(
            (
                await db.scalars(
                    select(User.id).where(User.id.in_(wanted), User.role == "employee")
                )
            ).all()
        )
        if wanted
        else set()
    )
    if wanted != known:
        raise ApiError(422, "INVALID_MEMBERS", "请仅选择已存在的员工账号；管理员无需单独授权")
    grants = {
        g.user_id: g
        for g in (await db.scalars(select(KBGrant).where(KBGrant.kb_id == kb_id))).all()
    }
    before = {
        "name": kb.name,
        "revision": kb.revision,
        "user_ids": sorted(str(uid) for uid, grant in grants.items() if grant.revoked_at is None),
    }
    names = {
        str(user.id): user.username
        for user in (await db.scalars(select(User).where(User.id.in_(set(grants) | wanted)))).all()
    }
    before["member_names"] = sorted(names[uid] for uid in before["user_ids"] if uid in names)
    for uid, grant in grants.items():
        if uid not in wanted and grant.revoked_at is None:
            grant.revoked_at = now()
    for uid in wanted:
        if uid in grants:
            grants[uid].revoked_at, grants[uid].granted_by = None, actor.user.id
            grants[uid].granted_at = now()
        else:
            db.add(KBGrant(user_id=uid, kb_id=kb_id, granted_by=actor.user.id))
    kb.revision += 1
    audit.record(
        db,
        "knowledge_base",
        kb.id,
        "knowledge_base.members_updated",
        actor,
        before=before,
        after={
            "name": kb.name,
            "revision": kb.revision,
            "user_ids": sorted(str(uid) for uid in wanted),
            "member_names": sorted(names[str(uid)] for uid in wanted),
        },
    )
    await db.commit()
    return await members(db, kb_id)
