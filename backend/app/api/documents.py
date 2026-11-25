import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import ok
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.security import now
from app.models import Document, DocumentChunk, DocumentVersion, IngestionJob
from app.schemas import Input
from app.services import auth
from app.services import documents as service
from app.services.embedding import usage_summary

router = APIRouter(prefix="/api/v1")


class SearchInput(Input):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    kb_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)


@router.get("/knowledge-bases/{kb_id}/documents")
async def documents(
    kb_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.list_documents(db, context.user, kb_id))


@router.post("/admin/knowledge-bases/{kb_id}/documents", status_code=202)
async def upload(
    kb_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(),
    title: str = Form(max_length=200),
    effective_from: datetime | None = Form(None),
    effective_until: datetime | None = Form(None),
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return ok(
            request,
            await service.upload(
                db,
                context,
                request.app.state.settings,
                file,
                title,
                effective_from or now(),
                effective_until,
                kb_id=kb_id,
            ),
        )
    finally:
        await file.close()


@router.post("/admin/documents/{document_id}/versions", status_code=202)
async def replace(
    document_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(),
    expected_current_version_id: uuid.UUID | None = Form(None),
    effective_from: datetime | None = Form(None),
    effective_until: datetime | None = Form(None),
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        return ok(
            request,
            await service.upload(
                db,
                context,
                request.app.state.settings,
                file,
                "替换版本",
                effective_from or now(),
                effective_until,
                document_id=document_id,
                expected_version=expected_current_version_id,
            ),
        )
    finally:
        await file.close()


@router.delete("/admin/documents/{document_id}", status_code=204)
async def delete(
    document_id: uuid.UUID, context=Depends(auth.admin_context), db: AsyncSession = Depends(get_db)
):
    await service.delete_document(db, context, document_id)


@router.get("/admin/ingestion-jobs/{job_id}")
async def job(
    job_id: uuid.UUID,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    item = await db.get(IngestionJob, job_id)
    if item is None:
        raise ApiError(404, "NOT_FOUND", "任务不存在")
    return ok(request, service.job_out(item))


@router.post("/admin/ingestion-jobs/{job_id}/retry", status_code=202)
async def retry(
    job_id: uuid.UUID,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.retry_job(db, context, job_id))


@router.get("/admin/document-versions/{version_id}/chunks")
async def chunks(
    version_id: uuid.UUID,
    request: Request,
    context=Depends(auth.admin_context),
    db: AsyncSession = Depends(get_db),
):
    version = await db.get(DocumentVersion, version_id)
    doc = await db.get(Document, version.document_id) if version else None
    if doc is None or doc.deleted_at:
        raise ApiError(404, "NOT_FOUND", "文档不存在")
    rows = (
        await db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.version_id == version_id)
            .order_by(DocumentChunk.ordinal)
        )
    ).all()
    return ok(
        request,
        {"items": [service.hit_out(chunk, version, doc) for chunk in rows], "total": len(rows)},
    )


@router.post("/search")
async def search(
    body: SearchInput,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    return ok(request, await service.search(request.app, db, context, body.query, body.kb_ids))


@router.get("/document-chunks/{chunk_id}")
async def citation(
    chunk_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    ids = [kb.id for kb in await auth.allowed_kbs(db, context.user)]
    row = (
        await db.execute(
            service.visible_chunks(context.user, ids, request.app.state.settings).where(
                DocumentChunk.id == chunk_id
            )
        )
    ).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "来源不存在或不可访问")
    return ok(request, service.hit_out(*row))


@router.get("/documents/{document_id}/versions/{version_id}/download")
async def download(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    context=Depends(auth.ready_context),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(Document, document_id)
    version = await db.get(DocumentVersion, version_id)
    if doc is None or doc.deleted_at or version is None or version.document_id != document_id:
        raise ApiError(404, "NOT_FOUND", "文件不存在或不可访问")
    await service.require_kb(db, context.user, doc.kb_id, manage=context.user.role == "admin")
    if context.user.role != "admin" and (
        doc.current_version_id != version.id or not service.effective(version)
    ):
        raise ApiError(404, "NOT_FOUND", "文件不存在或不可访问")
    path = service.blob_path(request.app.state.settings, version.blob_key)
    if not path.is_file():
        raise ApiError(404, "NOT_FOUND", "文件不存在")
    return FileResponse(path, filename=version.original_name, media_type="application/octet-stream")


@router.get("/admin/usage")
async def usage(request: Request, context=Depends(auth.admin_context)):
    return ok(request, await usage_summary(request.app.state.sessions, request.app.state.settings))
