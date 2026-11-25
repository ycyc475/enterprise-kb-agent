"""Check dependency wiring offline, or the local database only. No model API calls."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import importlib.metadata as metadata
import io
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


def check_dependencies() -> dict:
    import jieba
    import jwt
    import pdfplumber
    from docx import Document
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from markdown_it import MarkdownIt
    from pwdlib import PasswordHash

    checks: dict[str, bool] = {}
    print("M0 stage: in-process ASGI", flush=True)
    app = FastAPI()

    @app.get("/__m0/health")
    def health() -> dict:
        return {"probe": "ok"}

    with TestClient(app) as client:
        checks["fastapi_in_process_request"] = client.get("/__m0/health").json() == {"probe": "ok"}

    key = secrets.token_urlsafe(48)
    print("M0 stage: JWT and password libraries", flush=True)
    now = datetime.now(UTC)
    claims = {
        "sub": "m0-probe",
        "iss": "m0",
        "aud": "m0",
        "iat": now,
        "exp": now + timedelta(minutes=1),
    }
    token = jwt.encode(claims, key, algorithm="HS256")
    decoded = jwt.decode(token, key, algorithms=["HS256"], issuer="m0", audience="m0")
    checks["jwt_signature_verification"] = decoded["sub"] == "m0-probe"
    parts = token.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    try:
        jwt.decode(".".join(parts), key, algorithms=["HS256"], issuer="m0", audience="m0")
        checks["jwt_tampering_rejected"] = False
    except jwt.InvalidSignatureError:
        checks["jwt_tampering_rejected"] = True
    expired = jwt.encode({**claims, "exp": now - timedelta(seconds=1)}, key, algorithm="HS256")
    try:
        jwt.decode(expired, key, algorithms=["HS256"], issuer="m0", audience="m0")
        checks["expired_jwt_rejected"] = False
    except jwt.ExpiredSignatureError:
        checks["expired_jwt_rejected"] = True

    password = secrets.token_urlsafe(24)
    hasher = PasswordHash.recommended()
    password_hash = hasher.hash(password)
    checks["argon2_password_roundtrip"] = hasher.verify(password, password_hash)

    @tool
    def m0_echo(text: str) -> str:
        """Return generic probe text without external side effects."""
        return text

    print("M0 stage: offline Agent construction", flush=True)
    model = ChatOpenAI(
        model="m0-offline",
        api_key="m0-not-a-real-key",
        base_url="http://127.0.0.1:9/v1",
        max_retries=0,
    )
    bound = model.bind_tools([m0_echo])
    agent = create_agent(model=model, tools=[m0_echo])
    checks["langchain_tool_binding_and_graph_compile"] = bool(
        bound and agent.get_input_jsonschema()
    )
    checks["markdown_parser"] = any(
        item.type == "heading_open" for item in MarkdownIt().parse("# M0\n\nCompatibility")
    )
    stream = io.BytesIO()
    print("M0 stage: document and tokenizer libraries", flush=True)
    document = Document()
    document.add_paragraph("M0 compatibility")
    document.save(stream)
    stream.seek(0)
    checks["docx_in_memory_roundtrip"] = Document(stream).paragraphs[0].text == "M0 compatibility"
    checks["pdf_parser_import"] = callable(pdfplumber.open)
    tokenizer = jieba.Tokenizer()
    tokenizer.tmp_dir = str(ROOT / ".cache" / "jieba")
    tokenizer.cache_file = "m0-jieba.cache"
    checks["chinese_tokenizer"] = bool(tokenizer.lcut("中文分词验证"))
    packages = (
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "PyJWT",
        "pwdlib",
        "langchain",
        "langchain-core",
        "langchain-openai",
        "pgvector",
        "pdfplumber",
        "python-docx",
        "markdown-it-py",
        "jieba",
    )
    return {
        "phase": "dependencies",
        "passed": all(checks.values()),
        "checks": checks,
        "versions": {name: metadata.version(name) for name in packages},
        "python": sys.version.split()[0],
        "model_api_requests": 0,
    }


def check_database() -> dict:
    import psycopg
    from dotenv import dotenv_values
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import cast, literal, select
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = dotenv_values(ROOT / ".env")
    with psycopg.connect(
        host=settings["POSTGRES_HOST"],
        port=int(settings["POSTGRES_PORT"]),
        dbname=settings["POSTGRES_DB"],
        user=settings["POSTGRES_USER"],
        password=settings["POSTGRES_PASSWORD"],
        connect_timeout=5,
    ) as connection:
        version = connection.execute("SELECT current_setting('server_version')").fetchone()[0]
        extension = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        distance = connection.execute("SELECT '[1,0,0]'::vector <=> '[1,0,0]'::vector").fetchone()[
            0
        ]
        keyword = connection.execute(
            "SELECT to_tsvector('simple', %s) @@ plainto_tsquery('simple', %s)",
            ("中文 分词 验证", "验证"),
        ).fetchone()[0]

    async def query_orm() -> bool:
        url = URL.create(
            "postgresql+psycopg",
            username=settings["POSTGRES_USER"],
            password=settings["POSTGRES_PASSWORD"],
            host=settings["POSTGRES_HOST"],
            port=int(settings["POSTGRES_PORT"]),
            database=settings["POSTGRES_DB"],
        )
        engine = create_async_engine(url, connect_args={"connect_timeout": 5})
        try:
            async with engine.connect() as conn:
                left = cast(literal([1.0, 0.0, 0.0]), Vector(3))
                right = cast(literal([1.0, 0.0, 0.0]), Vector(3))
                return (await conn.execute(select(left.cosine_distance(right)))).scalar_one() == 0
        finally:
            await engine.dispose()

    # Psycopg requires a selector loop on Windows; no global event-loop policy change.
    factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        orm_ok = runner.run(query_orm())
    checks = {
        "postgres_connection": True,
        "pgvector_extension": bool(extension),
        "vector_cosine_query": distance == 0,
        "segmented_keyword_query": keyword is True,
        "async_sqlalchemy_pgvector_query": orm_ok,
    }
    return {
        "phase": "database",
        "passed": all(checks.values()),
        "checks": checks,
        "postgres": version,
        "pgvector": extension[0] if extension else None,
        "business_tables_created": 0,
        "model_api_requests": 0,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("dependencies", "database"), default="dependencies")
    options = parser.parse_args()
    faulthandler.dump_traceback_later(45, exit=True)
    try:
        result = check_dependencies() if options.phase == "dependencies" else check_database()
    except Exception as exc:
        # No environment dumps, DSNs, tokens, or provider credentials in diagnostic output.
        result = {"phase": options.phase, "passed": False, "error_type": type(exc).__name__}
    faulthandler.cancel_dump_traceback_later()
    target = ROOT / ".m0" / f"backend_{options.phase}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
