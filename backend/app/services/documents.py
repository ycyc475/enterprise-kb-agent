import asyncio
import hashlib
import re
import uuid

from sqlalchemy import delete, func, or_, select, text

from app.core.errors import ApiError
from app.core.security import now
from app.models import (
    AuthTokenFamily,
    Document,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    User,
)
from app.services import audit
from app.services.auth import allowed_kbs
from app.services.embedding import space
from app.services.management import lock_admin
from app.services.parsing import parse


def blob_path(settings, key):
    if not re.fullmatch(r"[0-9a-f]{32}\.(md|pdf|docx)", key):
        raise ApiError(404, "NOT_FOUND", "文件不存在")
    root = settings.storage_dir.resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ApiError(404, "NOT_FOUND", "文件不存在")
    return target


async def require_kb(db, user, kb_id, *, manage=False):
    if manage and user.role == "admin":
        kb = await db.get(KnowledgeBase, kb_id)
    else:
        kb = next((k for k in await allowed_kbs(db, user) if k.id == kb_id), None)
    if kb is None:
        raise ApiError(404, "NOT_FOUND", "知识库不存在或不可访问")
    return kb


def effective(version):
    return (
        version.status == "ready"
        and version.effective_from <= now()
        and (version.effective_until is None or version.effective_until > now())
    )


def version_out(version):
    if version is None:
        return None
    return {
        "id": str(version.id),
        "version_no": version.version_no,
        "original_name": version.original_name,
        "format": version.format,
        "status": version.status,
        "byte_size": version.byte_size,
        "extracted_chars": version.extracted_chars,
        "effective_from": version.effective_from.isoformat(),
        "effective_until": version.effective_until.isoformat() if version.effective_until else None,
        "effective": effective(version),
    }


def job_out(job):
    return {
        "id": str(job.id),
        "version_id": str(job.version_id),
        "status": job.status,
        "attempt_no": job.attempt_no,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


def audit_state(doc, version=None, job=None):
    result = {"title": doc.title, "kb_id": doc.kb_id, "current_version_id": doc.current_version_id}
    if version:
        result.update(
            version_id=version.id,
            version_no=version.version_no,
            status=version.status,
            format=version.format,
            sha256=version.sha256,
            effective_from=version.effective_from,
            effective_until=version.effective_until,
        )
    if job:
        result.update(job_id=job.id, attempt_no=job.attempt_no, error_code=job.error_code)
    return result


async def list_documents(db, user, kb_id):
    await require_kb(db, user, kb_id, manage=user.role == "admin")
    docs = (
        await db.scalars(
            select(Document)
            .where(Document.kb_id == kb_id, Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
        )
    ).all()
    items = []
    for doc in docs:
        current = (
            await db.get(DocumentVersion, doc.current_version_id)
            if doc.current_version_id
            else None
        )
        if user.role != "admin" and (current is None or not effective(current)):
            continue
        item = {
            "id": str(doc.id),
            "title": doc.title,
            "kb_id": str(doc.kb_id),
            "current_version": version_out(current),
        }
        if user.role == "admin":
            latest = await db.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == doc.id)
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
            job = await db.scalar(
                select(IngestionJob)
                .where(IngestionJob.version_id == latest.id)
                .order_by(IngestionJob.created_at.desc())
                .limit(1)
            )
            item.update(latest_version=version_out(latest), job=job_out(job) if job else None)
        items.append(item)
    return {"items": items, "total": len(items)}


def validate_dates(start, end):
    if start.tzinfo is None or (end is not None and end.tzinfo is None):
        raise ApiError(422, "INVALID_DATES", "生效时间必须包含时区")
    if end is not None and end <= start:
        raise ApiError(422, "INVALID_DATES", "失效时间必须晚于生效时间")


async def upload(
    db,
    actor,
    settings,
    file,
    title,
    start,
    end,
    *,
    kb_id=None,
    document_id=None,
    expected_version=None,
):
    validate_dates(start, end)
    title = title.strip()
    if not 1 <= len(title) <= 200:
        raise ApiError(422, "INVALID_TITLE", "文档标题应为1至200个字符")
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    fmt = name.rsplit(".", 1)[-1].lower()
    if fmt not in {"md", "pdf", "docx"} or len(name) > 200:
        raise ApiError(422, "UNSUPPORTED_FORMAT", "仅支持.md、文字.pdf、.docx，文件名最多200个字符")
    content = bytearray()
    while block := await file.read(65536):
        content.extend(block)
        if len(content) > settings.max_file_bytes:
            raise ApiError(413, "FILE_TOO_LARGE", "单文件不能超过10MB")
    if not content:
        raise ApiError(422, "EMPTY_FILE", "文件不能为空")
    await lock_admin(db, actor)
    if document_id is None:
        await require_kb(db, actor.user, kb_id, manage=True)
        count = await db.scalar(
            select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
        )
        if count >= settings.max_current_documents:
            raise ApiError(409, "DOCUMENT_LIMIT", "当前演示文档数量已达到30份上限")
        doc = Document(kb_id=kb_id, title=title)
        db.add(doc)
        await db.flush()
        number = 1
    else:
        doc = await db.get(Document, document_id, with_for_update=True)
        if doc is None or doc.deleted_at:
            raise ApiError(404, "NOT_FOUND", "文档不存在")
        if doc.current_version_id != expected_version:
            raise ApiError(409, "VERSION_CHANGED", "文档版本已变化，请刷新后重试")
        active = await db.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == doc.id,
                DocumentVersion.status.in_(["queued", "processing"]),
            )
        )
        if active:
            raise ApiError(409, "INGESTION_ACTIVE", "此文档已有处理任务")
        number = (
            await db.scalar(
                select(func.max(DocumentVersion.version_no)).where(
                    DocumentVersion.document_id == doc.id
                )
            )
        ) + 1
        # Title remains the logical document title during replacement.
    previous_state = audit_state(doc) if document_id else None
    key = uuid.uuid4().hex + "." + fmt
    path = blob_path(settings, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, content)
    version = DocumentVersion(
        document_id=doc.id,
        version_no=number,
        base_version_id=doc.current_version_id,
        original_name=name,
        blob_key=key,
        sha256=hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        format=fmt,
        status="queued",
        effective_from=start,
        effective_until=end,
        embedding_space=space(settings),
    )
    try:
        db.add(version)
        await db.flush()
        job = IngestionJob(version_id=version.id, status="queued")
        db.add(job)
        await db.flush()
        audit.record(
            db,
            "document",
            doc.id,
            "document.replace_requested" if document_id else "document.uploaded",
            actor,
            before=previous_state,
            after=audit_state(doc, version, job),
        )
        await db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {
        "document_id": str(doc.id),
        "version_id": str(version.id),
        "job_id": str(job.id),
        "status": "queued",
    }


async def retry_job(db, actor, job_id):
    await lock_admin(db, actor)
    job = await db.get(IngestionJob, job_id)
    if job is None:
        raise ApiError(404, "NOT_FOUND", "任务不存在")
    version = await db.get(DocumentVersion, job.version_id)
    doc = await db.get(Document, version.document_id, with_for_update=True)
    if doc.deleted_at or job.status != "failed" or version.status != "failed":
        raise ApiError(409, "RETRY_NOT_ALLOWED", "仅未删除文档的失败任务可以重试")
    latest = await db.scalar(
        select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == doc.id)
    )
    if latest != version.version_no or doc.current_version_id != version.base_version_id:
        raise ApiError(409, "VERSION_CHANGED", "已有更新版本，不能重试旧任务")
    before = audit_state(doc, version, job)
    job.status, job.error_code, job.error_message = "queued", None, None
    job.attempt_no += 1
    job.started_at = job.finished_at = None
    version.status = "queued"
    audit.record(
        db,
        "document",
        doc.id,
        "document.retry_requested",
        actor,
        before=before,
        after=audit_state(doc, version, job),
    )
    await db.commit()
    return job_out(job)


async def delete_document(db, actor, document_id):
    await lock_admin(db, actor)
    doc = await db.get(Document, document_id, with_for_update=True)
    if doc is None or doc.deleted_at:
        raise ApiError(404, "NOT_FOUND", "文档不存在")
    from app.services.fees import suspend_version

    if doc.current_version_id:
        await suspend_version(db, doc.current_version_id, reason="DOCUMENT_DELETED", actor=actor)
    doc.deleted_at = now()
    audit.record(
        db,
        "document",
        doc.id,
        "document.deleted",
        actor,
        before=audit_state(doc),
        after={"title": doc.title, "status": "deleted"},
    )
    await db.commit()


def tokens(content):
    import jieba

    return " ".join(
        word
        for token in jieba.cut_for_search(content.lower())
        for word in re.findall(r"\w+", token)
    )


async def run_job(app, job_id):
    sessions, cfg = app.state.sessions, app.state.settings
    try:
        async with sessions() as db:
            job = await db.get(IngestionJob, job_id, with_for_update=True)
            if job is None or job.status != "queued":
                return
            version = await db.get(DocumentVersion, job.version_id)
            job.status, job.started_at, version.status = "processing", now(), "processing"
            await db.commit()
            version_id, doc_id, key, fmt, index_space = (
                version.id,
                version.document_id,
                version.blob_key,
                version.format,
                version.embedding_space,
            )
        if index_space != space(cfg):
            raise ApiError(409, "INDEX_CONFIG_CHANGED", "向量配置已变更，请重新上传并建立索引")
        raw = await asyncio.to_thread(blob_path(cfg, key).read_bytes)
        parts, chars = await asyncio.wait_for(asyncio.to_thread(parse, raw, fmt, cfg), timeout=45)
        vectors = []
        for start in range(0, len(parts), 10):
            async with sessions() as db:
                doc = await db.get(Document, doc_id)
                if doc.deleted_at:
                    raise ApiError(409, "DOCUMENT_DELETED", "文档已删除，处理已停止")
            vectors.extend(
                await app.state.embedder.embed(
                    [
                        str(p.anchor.get("heading", ""))[:300] + "\n" + p.content
                        for p in parts[start : start + 10]
                    ]
                )
            )
        async with sessions() as db:
            doc = await db.get(Document, doc_id, with_for_update=True)
            version = await db.get(DocumentVersion, version_id)
            if doc.deleted_at or doc.current_version_id != version.base_version_id:
                raise ApiError(409, "VERSION_CHANGED", "文档已删除或版本变化，未发布本次索引")
            previous_state = audit_state(doc, version)
            await db.execute(delete(DocumentChunk).where(DocumentChunk.version_id == version_id))
            for ordinal, (part, vector) in enumerate(zip(parts, vectors, strict=True)):
                db.add(
                    DocumentChunk(
                        version_id=version_id,
                        ordinal=ordinal,
                        content=part.content,
                        anchor=part.anchor,
                        search_vector=func.to_tsvector("simple", tokens(part.content)),
                        embedding=vector,
                    )
                )
            version.status, version.extracted_chars = "ready", chars
            from app.services.fees import suspend_version

            if doc.current_version_id:
                await suspend_version(db, doc.current_version_id)
            doc.current_version_id = version.id
            job = await db.get(IngestionJob, job_id)
            job.status, job.finished_at = "completed", now()
            audit.record(
                db,
                "document",
                doc.id,
                "document.processing_completed",
                before=previous_state,
                after=audit_state(doc, version, job),
            )
            await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        async with sessions() as db:
            job = await db.get(IngestionJob, job_id)
            if job:
                version = await db.get(DocumentVersion, job.version_id)
                doc = await db.get(Document, version.document_id)
                before = audit_state(doc, version, job)
                job.status, job.finished_at = "failed", now()
                job.error_code = exc.code if isinstance(exc, ApiError) else "PROCESSING_FAILED"
                job.error_message = (
                    exc.message
                    if isinstance(exc, ApiError)
                    else "解析、索引或文件处理失败，请重试；重启中断也可重试"
                )
                version.status = "failed"
                audit.record(
                    db,
                    "document",
                    doc.id,
                    "document.processing_failed",
                    before=before,
                    after=audit_state(doc, version, job),
                )
                await db.commit()


async def worker(app):
    # One local worker across API processes. Session-level lock releases on crash.
    async with app.state.engine.connect() as conn:
        if not await conn.scalar(text("SELECT pg_try_advisory_lock(8174202)")):
            return
        await conn.commit()
        try:
            async with app.state.sessions() as db:
                interrupted = (
                    await db.scalars(
                        select(IngestionJob).where(IngestionJob.status == "processing")
                    )
                ).all()
                for job in interrupted:
                    version = await db.get(DocumentVersion, job.version_id)
                    doc = await db.get(Document, version.document_id)
                    before = audit_state(doc, version, job)
                    job.status, job.error_code, job.error_message = (
                        "failed",
                        "INTERRUPTED",
                        "服务重启中断，请重试",
                    )
                    version.status = "failed"
                    audit.record(
                        db,
                        "document",
                        doc.id,
                        "document.processing_failed",
                        before=before,
                        after=audit_state(doc, version, job),
                    )
                await db.commit()
            last_sweep = 0.0
            while True:
                current = asyncio.get_running_loop().time()
                if current - last_sweep >= 5:
                    from app.services.fees import reconcile_sources

                    async with app.state.sessions() as db:
                        await reconcile_sources(db, app.state.settings)
                        await db.commit()
                    last_sweep = current
                async with app.state.sessions() as db:
                    job_id = await db.scalar(
                        select(IngestionJob.id)
                        .where(IngestionJob.status == "queued")
                        .order_by(IngestionJob.created_at)
                        .limit(1)
                    )
                if job_id:
                    await run_job(app, job_id)
                else:
                    await asyncio.sleep(0.5)
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(8174202)"))
            await conn.commit()


def visible_chunks(user, kb_ids, settings):
    return (
        select(DocumentChunk, DocumentVersion, Document)
        .join(DocumentVersion, DocumentChunk.version_id == DocumentVersion.id)
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .where(
            Document.deleted_at.is_(None),
            Document.kb_id.in_(kb_ids),
            DocumentVersion.status == "ready",
            DocumentVersion.effective_from <= now(),
            or_(DocumentVersion.effective_until.is_(None), DocumentVersion.effective_until > now()),
            DocumentVersion.embedding_space == space(settings),
        )
    )


def hit_out(chunk, version, doc):
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(doc.id),
        "version_id": str(version.id),
        "title": doc.title,
        "original_name": version.original_name,
        "version_no": version.version_no,
        "content": chunk.content,
        "anchor": chunk.anchor,
        "download_path": f"/documents/{doc.id}/versions/{version.id}/download",
    }


async def search(app, db, context, query, kb_ids, *, run_id=None):
    user = context.user
    user_id, family_id, auth_version = user.id, context.family.id, context.version
    allowed = {kb.id for kb in await allowed_kbs(db, user)}
    if not set(kb_ids).issubset(allowed):
        raise ApiError(404, "NOT_FOUND", "知识库不存在或不可访问")
    if not kb_ids:
        return {"items": [], "total": 0, "mode": "hybrid"}
    base = visible_chunks(user, kb_ids, app.state.settings)
    if not (await db.execute(base.limit(1))).first():
        return {"items": [], "total": 0, "mode": "hybrid"}
    vector = (
        await app.state.embedder.embed(
            [query], actor_id=user.id, operation="query_embedding", run_id=run_id
        )
    )[0]
    # Refresh current permissions after the external call; no stale grant cache.
    await db.rollback()
    user = await db.get(User, user_id)
    family = await db.get(AuthTokenFamily, family_id)
    if (
        user is None
        or family is None
        or not user.is_active
        or user.auth_version != auth_version
        or family.revoked_at
        or family.expires_at <= now()
    ):
        raise ApiError(401, "AUTH_REQUIRED", "登录状态已失效，请重新登录")
    allowed = {kb.id for kb in await allowed_kbs(db, user)}
    if not set(kb_ids).issubset(allowed):
        raise ApiError(404, "NOT_FOUND", "知识库授权已变化，请刷新范围")
    base = visible_chunks(user, kb_ids, app.state.settings)
    keyword_query = " | ".join(dict.fromkeys(tokens(query).split()))
    tsquery = func.to_tsquery("simple", keyword_query)
    keyword_rows = (
        (
            await db.execute(
                base.where(DocumentChunk.search_vector.op("@@")(tsquery))
                .order_by(
                    func.ts_rank_cd(DocumentChunk.search_vector, tsquery).desc(), DocumentChunk.id
                )
                .limit(20)
            )
        ).all()
        if keyword_query
        else []
    )
    distance = DocumentChunk.embedding.cosine_distance(vector)
    vector_rows = (await db.execute(base.order_by(distance, DocumentChunk.id).limit(20))).all()
    merged = {}
    for branch, rows in [("keyword", keyword_rows), ("vector", vector_rows)]:
        for rank, row in enumerate(rows, 1):
            chunk, version, doc = row
            item = merged.setdefault(
                str(chunk.id), {**hit_out(chunk, version, doc), "score": 0.0, "branches": []}
            )
            item["score"] += 1 / (60 + rank)
            item["branches"].append(branch)
    items = sorted(merged.values(), key=lambda item: (-item["score"], item["chunk_id"]))[:6]
    return {
        "items": items,
        "total": len(items),
        "mode": "hybrid",
        "note": "检索原文片段，尚未生成AI回答；排序分数不是正确概率",
    }
