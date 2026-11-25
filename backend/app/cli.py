"""Local setup and server commands. Credentials are never printed."""

import argparse
import asyncio
import json
import os
import secrets

import psycopg
import uvicorn
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import BACKEND, ROOT, Settings, get_settings
from app.core.security import hasher
from app.models import EmployeeProfile, KBGrant, KnowledgeBase, User


def bootstrap_connection(settings: Settings, dbname: str | None = None):
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=dbname or settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=5,
        autocommit=True,
    )


def prepare_db(settings: Settings):
    with bootstrap_connection(settings) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname=%s", (settings.postgres_app_user,)
        ).fetchone()
        if not exists:
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(
                    sql.Identifier(settings.postgres_app_user),
                    sql.Literal(settings.postgres_app_password.get_secret_value()),
                )
            )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(settings.postgres_db), sql.Identifier(settings.postgres_app_user)
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(settings.postgres_app_user)
            )
        )
    print("Application database role ready (existing passwords are preserved).")


def migrate(settings: Settings):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["settings"] = settings
    command.upgrade(cfg, "head")
    print("Database migrations upgraded to head.")


def seed(settings: Settings):
    engine = create_engine(settings.database_url)
    credentials_path = ROOT / "data/runtime/demo-credentials.json"
    credentials = (
        json.loads(credentials_path.read_text("utf-8")) if credentials_path.exists() else {}
    )
    added = {}
    with Session(engine, expire_on_commit=False) as db:
        users = {}
        for username, role, name, department, grade in [
            ("admin", "admin", "演示管理员", "行政部", "S2"),
            ("employee_li", "employee", "李同学", "产品部", "S1"),
            ("employee_wang", "employee", "王同学", "研发部", "S2"),
        ]:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                password = secrets.token_urlsafe(16)
                user = User(
                    username=username,
                    role=role,
                    password_hash=hasher.hash(password),
                    must_change_password=False,
                )
                db.add(user)
                db.flush()
                db.add(
                    EmployeeProfile(
                        user_id=user.id, display_name=name, department=department, grade=grade
                    )
                )
                added[username] = password
            users[username] = user
        for name, description, members in [
            ("企业公共制度", "差旅、报销与日常办公制度", ["employee_li", "employee_wang"]),
            ("研发团队资料", "研发部门的内部流程与技术资料", ["employee_wang"]),
        ]:
            kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
            if kb is None:
                kb = KnowledgeBase(name=name, description=description, created_by=users["admin"].id)
                db.add(kb)
                db.flush()
                for username in members:
                    db.add(
                        KBGrant(
                            kb_id=kb.id, user_id=users[username].id, granted_by=users["admin"].id
                        )
                    )
        # Persist credentials before committing so an interrupted write cannot lose new passwords.
        if added:
            credentials_path.parent.mkdir(parents=True, exist_ok=True)
            credentials.update(added)
            temp = credentials_path.with_suffix(".tmp")
            temp.write_text(json.dumps(credentials, indent=2), "utf-8")
            temp.replace(credentials_path)
        db.commit()
    engine.dispose()
    print(
        f"Demo accounts ready; created {len(added)}. Local credentials: data/runtime/demo-credentials.json"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare-db", "migrate", "seed", "serve"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    settings = get_settings()
    if args.action == "serve":
        from app.main import create_app

        server = uvicorn.Server(
            uvicorn.Config(create_app(settings), host=args.host, port=args.port)
        )
        factory = asyncio.SelectorEventLoop if os.name == "nt" else asyncio.new_event_loop
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(server.serve())
    else:
        {"prepare-db": prepare_db, "migrate": migrate, "seed": seed}[args.action](settings)


if __name__ == "__main__":
    main()
