import uuid
from datetime import timedelta

import jwt
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access, now, token_hash
from app.models import Base, RefreshToken, User
from tests.conftest import ORIGIN, PASSWORD, csrf, login

API = "/api/v1"


def data(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def test_migration_matches_models(db_engine):
    with db_engine.connect() as connection:
        diffs = compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
        )
    assert diffs == []


def test_login_and_profile(client):
    assert client.get("/health").json()["database"] == "ok"
    headers = login(client, "alice")
    me = data(client.get(API + "/auth/me", headers=headers))
    assert me["profile"]["grade"] == "S1"
    assert [k["name"] for k in me["knowledge_bases"]] == ["common"]
    assert "password_hash" not in str(me) and "token_hash" not in str(me)
    assert PASSWORD not in str(me)
    assert client.get(API + "/auth/me", headers=headers).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("username,password", [("missing", PASSWORD), ("alice", "bad")])
def test_generic_bad_login(client, username, password):
    response = client.post(API + "/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_missing_tampered_expired_access(client, test_settings):
    assert client.get(API + "/auth/me").status_code == 401
    headers = login(client)
    raw = headers["Authorization"].split()[1]
    assert (
        client.get(
            API + "/auth/me", headers={"Authorization": "Bearer " + raw[:-5] + "xxxxx"}
        ).status_code
        == 401
    )
    claims = decode_access(raw, test_settings)
    claims["exp"] = now() - timedelta(seconds=10)
    expired = jwt.encode(claims, test_settings.jwt_secret_key.get_secret_value(), algorithm="HS256")
    assert (
        client.get(API + "/auth/me", headers={"Authorization": "Bearer " + expired}).status_code
        == 401
    )


def test_rotation_hash_only_and_replay_revokes_family(client, db_engine):
    login(client, "alice")
    old_cookie = client.cookies.get("kb_refresh")
    csrf_headers = csrf(client)
    response = client.post(API + "/auth/refresh", headers=csrf_headers)
    refreshed = data(response)
    new_cookie = client.cookies.get("kb_refresh")
    assert old_cookie != new_cookie
    assert (
        "HttpOnly" in response.headers["set-cookie"]
        and "SameSite=lax" in response.headers["set-cookie"]
    )
    with Session(db_engine) as db:
        tokens = db.scalars(select(RefreshToken)).all()
        assert {t.token_hash for t in tokens} == {token_hash(old_cookie), token_hash(new_cookie)}
        assert all(
            len(t.token_hash) == 64 and t.token_hash not in {old_cookie, new_cookie} for t in tokens
        )
    client.cookies.clear()
    client.cookies.set("kb_refresh", old_cookie, path="/api/v1/auth")
    replay = client.post(API + "/auth/refresh", headers=csrf_headers)
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "REFRESH_REPLAY"
    assert (
        client.get(
            API + "/auth/me", headers={"Authorization": "Bearer " + refreshed["access_token"]}
        ).status_code
        == 401
    )


def test_logout_revokes_access(client):
    headers = login(client)
    assert client.post(API + "/auth/logout", headers=csrf(client)).status_code == 204
    assert not client.cookies.get("kb_refresh")
    assert client.get(API + "/auth/me", headers=headers).status_code == 401


def test_csrf_and_origin(client):
    assert client.get(API + "/auth/csrf").json()["data"]["csrf_token"] is None
    login(client)
    assert client.post(API + "/auth/refresh").status_code == 403
    assert client.post(API + "/auth/logout").status_code == 403
    valid = csrf(client)
    assert (
        client.post(
            API + "/auth/refresh", headers={**valid, "Origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            API + "/auth/login",
            headers={"Origin": "https://evil.invalid"},
            json={"username": "admin", "password": PASSWORD},
        ).status_code
        == 403
    )
    assert (
        client.post(API + "/auth/refresh", headers={**valid, "Origin": ORIGIN}).status_code == 200
    )


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/admin/users", None),
        ("post", "/admin/users", {"username": "hacker", "display_name": "H", "department": "H"}),
        ("post", "/admin/knowledge-bases", {"name": "hack"}),
        ("get", "/knowledge-bases?include_inactive=true", None),
    ],
)
def test_employee_cannot_admin(client, method, path, body):
    headers = login(client, "alice")
    assert client.request(method, API + path, headers=headers, json=body).status_code == 403


def test_employee_cannot_modify_profile(client, records):
    headers = login(client, "alice")
    response = client.patch(
        API + f"/admin/employees/{records['alice']}",
        headers=headers,
        json={"display_name": "hack", "department": "H", "grade": "S2", "expected_revision": 1},
    )
    assert response.status_code == 403
    assert data(client.get(API + "/auth/me", headers=headers))["profile"]["grade"] == "S1"


def test_grant_revoke_disable_and_no_direct_access(client, records):
    alice = login(client, "alice")
    admin = login(client)
    kb_id = records["restricted"]
    assert client.get(API + f"/knowledge-bases/{kb_id}", headers=alice).status_code == 404
    endpoint = API + f"/admin/knowledge-bases/{kb_id}/members"
    result = data(
        client.put(
            endpoint,
            headers=admin,
            json={"user_ids": [records["alice"], records["bob"]], "expected_revision": 1},
        )
    )
    assert client.get(API + f"/knowledge-bases/{kb_id}", headers=alice).status_code == 200
    data(
        client.put(
            endpoint,
            headers=admin,
            json={"user_ids": [records["bob"]], "expected_revision": result["revision"]},
        )
    )
    assert client.get(API + f"/knowledge-bases/{kb_id}", headers=alice).status_code == 404
    assert len(data(client.get(API + "/knowledge-bases", headers=alice))["items"]) == 1
    assert (
        client.put(
            endpoint, headers=admin, json={"user_ids": [], "expected_revision": 1}
        ).status_code
        == 409
    )
    common = data(
        client.patch(
            API + f"/admin/knowledge-bases/{records['common']}",
            headers=admin,
            json={"name": "common", "description": "", "is_active": False, "expected_revision": 1},
        )
    )
    assert not common["is_active"]
    assert data(client.get(API + "/knowledge-bases", headers=alice))["items"] == []
    assert (
        client.get(API + f"/knowledge-bases/{records['common']}", headers=alice).status_code == 404
    )


def test_profile_revision_and_fresh_read(client, records):
    alice = login(client, "alice")
    admin = login(client)
    body = {
        "display_name": "Alice新名",
        "department": "产品部",
        "grade": "S2",
        "expected_revision": 1,
    }
    changed = data(
        client.patch(API + f"/admin/employees/{records['alice']}", headers=admin, json=body)
    )
    assert changed["profile"]["revision"] == 2
    assert data(client.get(API + "/auth/me", headers=alice))["profile"]["grade"] == "S2"
    assert (
        client.patch(
            API + f"/admin/employees/{records['alice']}", headers=admin, json=body
        ).status_code
        == 409
    )


def test_disable_revokes_access_refresh_and_login(client, records):
    alice = login(client, "alice")
    old_cookie = client.cookies.get("kb_refresh")
    valid = csrf(client)
    admin = login(client)
    data(
        client.patch(
            API + f"/admin/users/{records['alice']}", headers=admin, json={"is_active": False}
        )
    )
    assert client.get(API + "/auth/me", headers=alice).status_code == 401
    client.cookies.clear()
    client.cookies.set("kb_refresh", old_cookie, path="/api/v1/auth")
    assert client.post(API + "/auth/refresh", headers=valid).status_code == 401
    assert (
        client.post(
            API + "/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_reset_password_and_forced_change(client, records):
    alice = login(client, "alice")
    admin = login(client)
    temporary = data(
        client.post(API + f"/admin/users/{records['alice']}/password-reset", headers=admin)
    )["temporary_password"]
    assert client.get(API + "/auth/me", headers=alice).status_code == 401
    assert (
        client.post(
            API + "/auth/login", json={"username": "alice", "password": PASSWORD}
        ).status_code
        == 401
    )
    first = login(client, "alice", temporary)
    assert data(client.get(API + "/auth/me", headers=first))["must_change_password"]
    assert client.get(API + "/knowledge-bases", headers=first).status_code == 403
    assert (
        client.post(
            API + "/auth/password",
            headers=first,
            json={"old_password": "bad", "new_password": "New_password_1234"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            API + "/auth/password",
            headers=first,
            json={"old_password": temporary, "new_password": "New_password_1234"},
        ).status_code
        == 204
    )
    assert client.get(API + "/auth/me", headers=first).status_code == 401
    updated = login(client, "alice", "New_password_1234")
    assert not data(client.get(API + "/auth/me", headers=updated))["must_change_password"]
    assert client.get(API + "/knowledge-bases", headers=updated).status_code == 200


def test_create_duplicate_validation_and_self_protection(client, records, db_engine):
    admin = login(client)
    body = {
        "username": "new_employee",
        "display_name": "新同学",
        "department": "产品部",
        "grade": "S1",
    }
    created = data(client.post(API + "/admin/users", headers=admin, json=body), 201)
    assert len(created["temporary_password"]) >= 12
    assert client.post(API + "/admin/users", headers=admin, json=body).status_code == 409
    with Session(db_engine) as db:
        assert db.get(User, uuid.UUID(created["user"]["id"])).password_hash.startswith("$argon2id$")
    invalid = client.post(
        API + "/admin/users",
        headers=admin,
        json={**body, "username": "invalid", "password": "secret123"},
    )
    assert invalid.status_code == 422 and "secret123" not in invalid.text
    for body in [{"is_active": False}, {"role": "employee"}]:
        assert (
            client.patch(
                API + f"/admin/users/{records['admin']}", headers=admin, json=body
            ).status_code
            == 409
        )
    assert (
        client.post(
            API + f"/admin/users/{records['admin']}/password-reset", headers=admin
        ).status_code
        == 409
    )
    kb = data(
        client.post(API + "/admin/knowledge-bases", headers=admin, json={"name": "newkb"}), 201
    )
    assert (
        client.post(
            API + "/admin/knowledge-bases", headers=admin, json={"name": "newkb"}
        ).status_code
        == 409
    )
    assert (
        client.put(
            API + f"/admin/knowledge-bases/{kb['id']}/members",
            headers=admin,
            json={"user_ids": [records["admin"]], "expected_revision": 1},
        ).status_code
        == 422
    )


def test_role_change_revokes_old_session(client, records):
    alice = login(client, "alice")
    admin = login(client)
    data(
        client.patch(
            API + f"/admin/users/{records['alice']}", headers=admin, json={"role": "admin"}
        )
    )
    assert client.get(API + "/admin/users", headers=alice).status_code == 401
    fresh = login(client, "alice")
    assert client.get(API + "/admin/users", headers=fresh).status_code == 200
