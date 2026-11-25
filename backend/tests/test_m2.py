import asyncio
import io
import uuid
from contextlib import suppress
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from docx import Document as WordDocument
from pydantic import SecretStr
from sqlalchemy import select

from app.core.errors import ApiError
from app.core.security import now
from app.models import DocumentVersion, IngestionJob, KBGrant, UsageRecord, User
from app.services.documents import run_job, worker
from app.services.embedding import Embedder
from app.services.parsing import parse
from tests.conftest import login

API = "/api/v1"
TEXT = "# 差旅制度\n\n北京出差住宿标准为每晚300元。\n\n| 城市 | 标准 |\n| --- | --- |\n| 上海 | 500元 |\n"


class FakeEmbedder:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def embed(self, texts, **kwargs):
        self.calls += 1
        if self.fail:
            raise ApiError(502, "EMBEDDING_UNAVAILABLE", "模拟向量服务故障")
        return [[1.0] + [0.0] * 1023 for _ in texts]


@pytest.fixture
def setup(client, records):
    fake = FakeEmbedder()
    client.app.state.embedder = fake
    return client, records, fake, login(client)


def upload(client, records, admin, name="travel.md", content=TEXT.encode(), **fields):
    response = client.post(
        API + f"/admin/knowledge-bases/{records['common']}/documents",
        headers=admin,
        data={"title": "差旅制度", **fields},
        files={"file": (name, content)},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def process(client, item):
    client.portal.call(run_job, client.app, uuid.UUID(item["job_id"]))


def test_upload_index_hybrid_source_and_download(setup):
    client, records, fake, admin = setup
    item = upload(client, records, admin)
    process(client, item)
    job = client.get(API + f"/admin/ingestion-jobs/{item['job_id']}", headers=admin).json()["data"]
    assert job["status"] == "completed"
    alice = login(client, "alice")
    found = client.post(
        API + "/search",
        headers=alice,
        json={"query": "北京住宿标准", "kb_ids": [records["common"]]},
    )
    assert found.status_code == 200, found.text
    hits = found.json()["data"]["items"]
    assert any("北京" in h["content"] and set(h["branches"]) == {"keyword", "vector"} for h in hits)
    assert all(h["anchor"]["type"] == "markdown" and h["anchor"]["line_start"] > 0 for h in hits)
    assert fake.calls >= 2
    hit = hits[0]
    assert client.get(API + f"/document-chunks/{hit['chunk_id']}", headers=alice).status_code == 200
    response = client.get(API + hit["download_path"], headers=alice)
    assert (
        response.content == TEXT.encode()
        and "attachment" in response.headers["content-disposition"]
    )
    assert (
        client.post(
            API + "/search",
            headers=alice,
            json={"query": "北京", "kb_ids": [records["restricted"]]},
        ).status_code
        == 404
    )


def test_revoke_blocks_list_search_source_and_download(setup):
    client, records, fake, admin = setup
    item = upload(client, records, admin)
    process(client, item)
    alice = login(client, "alice")
    hit = client.post(
        API + "/search", headers=alice, json={"query": "住宿", "kb_ids": [records["common"]]}
    ).json()["data"]["items"][0]
    calls = fake.calls
    client.put(
        API + f"/admin/knowledge-bases/{records['common']}/members",
        headers=admin,
        json={"user_ids": [], "expected_revision": 1},
    )
    assert (
        client.get(
            API + f"/knowledge-bases/{records['common']}/documents", headers=alice
        ).status_code
        == 404
    )
    assert (
        client.post(
            API + "/search", headers=alice, json={"query": "住宿", "kb_ids": [records["common"]]}
        ).status_code
        == 404
    )
    assert fake.calls == calls
    assert client.get(API + hit["download_path"], headers=alice).status_code == 404
    assert client.get(API + f"/document-chunks/{hit['chunk_id']}", headers=alice).status_code == 404


def test_failed_replacement_keeps_old_and_retry_publishes_atomically(setup):
    client, records, fake, admin = setup
    old = upload(client, records, admin)
    process(client, old)
    fake.fail = True
    replacement = client.post(
        API + f"/admin/documents/{old['document_id']}/versions",
        headers=admin,
        data={"expected_current_version_id": old["version_id"]},
        files={"file": ("new.md", "# 新制度\n\n深圳住宿标准600元".encode())},
    )
    assert replacement.status_code == 202
    new = replacement.json()["data"]
    process(client, new)
    listed = client.get(
        API + f"/knowledge-bases/{records['common']}/documents", headers=admin
    ).json()["data"]["items"][0]
    assert (
        listed["current_version"]["id"] == old["version_id"] and listed["job"]["status"] == "failed"
    )
    fake.fail = False
    retry = client.post(API + f"/admin/ingestion-jobs/{new['job_id']}/retry", headers=admin)
    assert retry.status_code == 202
    assert (
        client.post(API + f"/admin/ingestion-jobs/{new['job_id']}/retry", headers=admin).status_code
        == 409
    )
    process(client, new)
    alice = login(client, "alice")
    hits = client.post(
        API + "/search", headers=alice, json={"query": "住宿", "kb_ids": [records["common"]]}
    ).json()["data"]["items"]
    assert hits and all(h["version_id"] == new["version_id"] for h in hits)
    assert (
        client.get(
            API + f"/documents/{old['document_id']}/versions/{old['version_id']}/download",
            headers=alice,
        ).status_code
        == 404
    )
    stale = client.post(
        API + f"/admin/documents/{old['document_id']}/versions",
        headers=admin,
        data={"expected_current_version_id": old["version_id"]},
        files={"file": ("a.md", TEXT.encode())},
    )
    assert stale.status_code == 409


def test_delete_hides_existing_sources_and_pending_job(setup):
    client, records, fake, admin = setup
    item = upload(client, records, admin)
    process(client, item)
    alice = login(client, "alice")
    hit = client.post(
        API + "/search", headers=alice, json={"query": "北京", "kb_ids": [records["common"]]}
    ).json()["data"]["items"][0]
    assert (
        client.delete(API + f"/admin/documents/{item['document_id']}", headers=admin).status_code
        == 204
    )
    assert client.get(API + hit["download_path"], headers=alice).status_code == 404
    assert client.get(API + f"/document-chunks/{hit['chunk_id']}", headers=alice).status_code == 404
    assert not client.post(
        API + "/search", headers=alice, json={"query": "北京", "kb_ids": [records["common"]]}
    ).json()["data"]["items"]
    pending = upload(client, records, admin)
    client.delete(API + f"/admin/documents/{pending['document_id']}", headers=admin)
    before = fake.calls
    process(client, pending)
    assert fake.calls == before


def test_invalid_files_limits_and_employee_write(setup):
    client, records, fake, admin = setup
    endpoint = API + f"/admin/knowledge-bases/{records['common']}/documents"
    for name, content, status in [
        ("bad.exe", b"test", 422),
        ("empty.md", b"", 422),
        ("big.md", b"a" * 10_000_001, 413),
    ]:
        assert (
            client.post(
                endpoint, headers=admin, data={"title": "x"}, files={"file": (name, content)}
            ).status_code
            == status
        )
    alice = login(client, "alice")
    assert (
        client.post(
            endpoint, headers=alice, data={"title": "x"}, files={"file": ("a.md", b"text")}
        ).status_code
        == 403
    )
    item = upload(client, records, admin, "bad.pdf", b"fake pdf")
    process(client, item)
    assert (
        client.get(API + f"/admin/ingestion-jobs/{item['job_id']}", headers=admin).json()["data"][
            "status"
        ]
        == "failed"
    )
    assert fake.calls == 0
    client.app.state.settings.max_current_documents = 1
    assert (
        client.post(
            endpoint, headers=admin, data={"title": "x"}, files={"file": ("a.md", b"test")}
        ).status_code
        == 409
    )


def test_effective_dates_filter_before_paid_call(setup):
    client, records, fake, admin = setup
    item = upload(client, records, admin, effective_from=(now() + timedelta(days=1)).isoformat())
    process(client, item)
    before = fake.calls
    alice = login(client, "alice")
    assert (
        client.get(API + f"/knowledge-bases/{records['common']}/documents", headers=alice).json()[
            "data"
        ]["total"]
        == 0
    )
    assert (
        client.post(
            API + "/search", headers=alice, json={"query": "北京", "kb_ids": [records["common"]]}
        ).json()["data"]["total"]
        == 0
    )
    assert fake.calls == before


def test_parsers_preserve_word_table_markdown_and_pdf(test_settings):
    word = WordDocument()
    word.add_heading("报销制度", 1)
    word.add_paragraph("员工需要提交行程证明。")
    table = word.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "城市", "标准"
    table.cell(1, 0).text, table.cell(1, 1).text = "北京", "300元"
    stream = io.BytesIO()
    word.save(stream)
    parts, _ = parse(stream.getvalue(), "docx", test_settings)
    assert any(
        p.anchor.get("table") == 1 and p.anchor.get("row") == 2 and "标准: 300元" in p.content
        for p in parts
    )
    parts, _ = parse(TEXT.encode(), "md", test_settings)
    assert any(
        p.anchor.get("table_row") and "城市" in p.content and "500元" in p.content for p in parts
    )
    # Minimal one-page PDF with real text and correct cross-reference offsets.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    text = b"BT /F1 12 Tf 72 720 Td (Travel hotel standard 300 CNY) Tj ET"
    objects.append(
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream"
    )
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(
        b"xref\n0 6\n0000000000 65535 f \n"
        + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    )
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    parts, _ = parse(bytes(pdf), "pdf", test_settings)
    assert parts[0].anchor["page"] == 1 and "300 CNY" in parts[0].content
    with pytest.raises(ApiError) as error:
        parse(b"a" * 101, "md", test_settings.model_copy(update={"max_extracted_chars": 100}))
    assert error.value.code == "TEXT_TOO_LONG"
    with pytest.raises(ApiError) as error:
        parse(bytes(pdf), "pdf", test_settings.model_copy(update={"max_pdf_pages": 0}))
    assert error.value.code == "PDF_TOO_MANY_PAGES"


def test_vector_dimension_validation_usage_and_budget(client, monkeypatch):
    cfg = client.app.state.settings
    cfg.dashscope_api_key = SecretStr("test_key_no_network")
    cfg.dashscope_base_url = "https://example.invalid/v1"

    class StubClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [1, 0]}], "usage": {"total_tokens": 3}},
            )

    monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", StubClient)
    embedder = Embedder(cfg, client.app.state.sessions)
    with pytest.raises(ApiError) as error:
        client.portal.call(embedder.embed, ["北京"])
    assert error.value.code == "EMBEDDING_UNAVAILABLE"

    async def remaining():
        async with client.app.state.sessions() as db:
            record = await db.scalar(select(UsageRecord))
            assert record.status == "failed_unknown" and record.estimated_cost > 0

    client.portal.call(remaining)
    cfg.budget_limit_cny = Decimal("0.00000001")
    with pytest.raises(ApiError) as error:
        client.portal.call(embedder.embed, ["北京"])
    assert error.value.code == "BUDGET_PAUSED"


@pytest.mark.parametrize("change,expected", [("revoke", 404), ("disable", 401)])
def test_permission_rechecked_after_embedding(setup, change, expected):
    client, records, fake, admin = setup
    item = upload(client, records, admin)
    process(client, item)
    alice = login(client, "alice")

    class ChangingEmbedder(FakeEmbedder):
        async def embed(self, texts, **kwargs):
            async with client.app.state.sessions() as db:
                if change == "revoke":
                    grant = await db.get(
                        KBGrant, (uuid.UUID(records["alice"]), uuid.UUID(records["common"]))
                    )
                    grant.revoked_at = now()
                else:
                    user = await db.get(User, uuid.UUID(records["alice"]))
                    user.is_active = False
                    user.auth_version += 1
                await db.commit()
            return await super().embed(texts, **kwargs)

    client.app.state.embedder = ChangingEmbedder()
    result = client.post(
        API + "/search", headers=alice, json={"query": "北京住宿", "kb_ids": [records["common"]]}
    )
    assert result.status_code == expected
    assert "300元" not in result.text


def test_worker_recovers_interrupted_jobs(setup):
    client, records, fake, admin = setup
    item = upload(client, records, admin)

    async def restart():
        async with client.app.state.sessions() as db:
            job = await db.get(IngestionJob, uuid.UUID(item["job_id"]))
            job.status = "processing"
            (await db.get(DocumentVersion, job.version_id)).status = "processing"
            await db.commit()
        task = asyncio.create_task(worker(client.app))
        try:
            for _ in range(50):
                await asyncio.sleep(0.02)
                async with client.app.state.sessions() as db:
                    job = await db.get(IngestionJob, uuid.UUID(item["job_id"]))
                    if job.status == "failed":
                        assert job.error_code == "INTERRUPTED"
                        return
            pytest.fail("Worker did not recover interrupted job")
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    client.portal.call(restart)
    assert (
        client.post(
            API + f"/admin/ingestion-jobs/{item['job_id']}/retry", headers=admin
        ).status_code
        == 202
    )
    process(client, item)
    assert (
        client.get(API + f"/admin/ingestion-jobs/{item['job_id']}", headers=admin).json()["data"][
            "status"
        ]
        == "completed"
    )
