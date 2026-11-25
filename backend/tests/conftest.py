import asyncio
import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.cli import bootstrap_connection, migrate
from app.core.config import get_settings
from app.core.security import hasher
from app.main import create_app
from app.models import EmployeeProfile, KBGrant, KnowledgeBase, User

PASSWORD = "M1_test_password_2026!"
ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture(scope="session")
def test_settings():
    original = get_settings()
    name = "enterprise_kb_test_" + uuid.uuid4().hex[:10]
    assert re.fullmatch(r"enterprise_kb_test_[0-9a-f]{10}", name)
    settings = original.model_copy(
        update={
            "postgres_db": name,
            "worker_enabled": False,
            "dashscope_api_key": SecretStr(""),
            "dashscope_base_url": "https://example.invalid/v1",
        }
    )
    with bootstrap_connection(original, "postgres") as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(settings.postgres_app_user)
            )
        )
    try:
        with bootstrap_connection(settings) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        migrate(settings)
        yield settings
    finally:
        # Only the generated test database is eligible for cleanup; never the application DB.
        assert name != original.postgres_db and re.fullmatch(
            r"enterprise_kb_test_[0-9a-f]{10}", name
        )
        with bootstrap_connection(original, "postgres") as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def db_engine(test_settings):
    engine = create_engine(test_settings.database_url)
    assert re.fullmatch(r"enterprise_kb_test_[0-9a-f]{10}", engine.url.database)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE kb_grants, knowledge_bases, refresh_tokens, auth_token_families, employee_profiles, users CASCADE"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def records(db_engine):
    data = {}
    password_hash = hasher.hash(PASSWORD)
    with Session(db_engine) as db:
        for username, role, grade in [
            ("admin", "admin", "S2"),
            ("alice", "employee", "S1"),
            ("bob", "employee", "S2"),
        ]:
            user = User(
                username=username,
                role=role,
                password_hash=password_hash,
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            db.add(
                EmployeeProfile(
                    user_id=user.id, display_name=username, department="测试部", grade=grade
                )
            )
            data[username] = str(user.id)
        for name in ["common", "restricted"]:
            kb = KnowledgeBase(name=name, description="测试资料")
            db.add(kb)
            db.flush()
            data[name] = str(kb.id)
        db.add_all(
            [
                KBGrant(user_id=uuid.UUID(data["alice"]), kb_id=uuid.UUID(data["common"])),
                KBGrant(user_id=uuid.UUID(data["bob"]), kb_id=uuid.UUID(data["restricted"])),
            ]
        )
        db.commit()
    return data


@pytest.fixture
def client(test_settings, records, tmp_path):
    options = {"loop_factory": asyncio.SelectorEventLoop} if os.name == "nt" else {}
    with TestClient(
        create_app(test_settings.model_copy(update={"storage_dir": tmp_path})),
        backend_options=options,
        headers={"Origin": ORIGIN},
    ) as client:
        yield client


def login(client, username="admin", password=PASSWORD):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return {"Authorization": f"Bearer {data['access_token']}"}


def csrf(client):
    return {"X-CSRF-Token": client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]}
