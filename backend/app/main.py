import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.audit import router as audit_router
from app.api.conversations import router as conversation_router
from app.api.documents import router as document_router
from app.api.fees import router as fee_router
from app.api.router import router
from app.core.config import Settings, get_settings
from app.core.db import database
from app.core.errors import ApiError
from app.core.telemetry import configure_logging, log_event, ms_since, trace_id_var
from app.core.upload_limit import UploadLimit
from app.models import User
from app.services.auth import clear_cookie
from app.services.chat_agent import chat_worker
from app.services.chat_models import QwenProvider
from app.services.documents import worker
from app.services.embedding import Embedder

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()
    engine, sessions = database(settings)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        try:
            async with engine.connect() as connection:
                await connection.execute(select(User.id).limit(0))
            if settings.worker_enabled:
                tasks = [asyncio.create_task(worker(app)), asyncio.create_task(chat_worker(app))]
            yield
        finally:
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await engine.dispose()

    app = FastAPI(title="企业知识库 API", version="0.6.0", lifespan=lifespan)
    app.state.settings, app.state.sessions, app.state.engine = settings, sessions, engine
    app.state.embedder = Embedder(settings, sessions)
    app.state.chat_provider = QwenProvider(settings)
    app.add_middleware(UploadLimit, max_bytes=settings.max_file_bytes + 65536)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def request_id(request, call_next):
        rid = str(uuid.uuid4())
        request.state.request_id = rid
        token = trace_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log_event(
                "http.request_error",
                level=logging.ERROR,
                method=request.method,
                path=request.url.path,
                duration_ms=ms_since(started),
                request_id=rid,
            )
            raise
        finally:
            trace_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        log_event(
            "http.request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=ms_since(started),
            request_id=rid,
        )
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        response = JSONResponse(
            status_code=exc.status,
            content={
                "error": {"code": exc.code, "message": exc.message},
                "request_id": request.state.request_id,
            },
        )
        if exc.status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        if exc.clear_refresh:
            clear_cookie(response, settings)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请检查输入内容",
                    "details": details,
                },
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error("Database request failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "error": {"code": "DATABASE_UNAVAILABLE", "message": "服务暂时不可用，请稍后重试"},
                "request_id": request.state.request_id,
            },
        )

    @app.get("/health")
    async def health():
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
        return {"service": "enterprise-kb-agent", "phase": "M6", "database": "ok"}

    app.include_router(router)
    app.include_router(document_router)
    app.include_router(conversation_router)
    app.include_router(fee_router)
    app.include_router(audit_router)
    return app
