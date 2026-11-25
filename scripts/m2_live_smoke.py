"""Explicit live smoke test: three small local fixtures and two retrieval requests."""

import argparse
import io
import json
import sys
import time
from pathlib import Path

import httpx
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def make_pdf():
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = b"BT /F1 14 Tf 72 720 Td (M2 PDF fixture - Travel receipts must include invoice and itinerary.) Tj ET"
    objects.append(
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"
    )
    pdf, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(
        b"xref\n0 6\n0000000000 65535 f \n"
        + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    )
    pdf.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(pdf)


def fixtures():
    text = "# M2 差旅检索样例\n\n本文件仅用于技术验证，不能作为正式报销制度。\n\n## 住宿标准\n\n| 城市 | S1每晚标准 | S2每晚标准 |\n| --- | --- | --- |\n| 北京、上海 | 300元 | 500元 |\n| 杭州、南京 | 200元 | 350元 |\n\n## 每日补贴\n\n出差补贴为80元/天。员工职级由管理员维护。\n"
    doc = Document()
    doc.add_heading("M2 报销材料样例", 0)
    doc.add_paragraph("本文件仅用于技术验证，不能作为正式报销制度。")
    doc.add_paragraph("出差报销需要准备发票和行程证明，返回后向财务提交。")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "材料", "要求"
    row = table.add_row().cells
    row[0].text, row[1].text = "住宿发票", "抬头和日期应与出差记录一致"
    stream = io.BytesIO()
    doc.save(stream)
    return [
        ("M2技术样例-差旅标准", "m2-travel.md", text.encode()),
        ("M2技术样例-报销材料", "m2-receipts.docx", stream.getvalue()),
        ("M2技术样例-PDF提取", "m2-receipts.pdf", make_pdf()),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Authorize the bounded live embedding requests",
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Replace only the three named technical sample documents",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live explicitly; this smoke test can consume API quota.")
    credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
    report = {"uploads": [], "searches": [], "chat_calls": 0}
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1",
        headers={"Origin": "http://127.0.0.1:5173"},
        timeout=60,
        trust_env=False,
    ) as client:

        def checked(response, expected=200):
            if response.status_code != expected:
                raise RuntimeError(f"HTTP {response.status_code}: {response.request.url.path}")
            return response.json()["data"] if expected != 204 else None

        admin = checked(
            client.post(
                "/auth/login",
                json={"username": "admin", "password": credentials["admin"]},
            )
        )
        admin_headers = {"Authorization": "Bearer " + admin["access_token"]}
        kb = next(
            k
            for k in checked(client.get("/knowledge-bases", headers=admin_headers))["items"]
            if k["name"] == "企业公共制度"
        )
        before = checked(client.get("/admin/usage", headers=admin_headers))
        existing = checked(
            client.get(f"/knowledge-bases/{kb['id']}/documents", headers=admin_headers)
        )["items"]
        for title, filename, content in fixtures():
            sample_dir = ROOT / "data/sample_documents"
            sample_dir.mkdir(parents=True, exist_ok=True)
            (sample_dir / filename).write_bytes(content)
            old = next((d for d in existing if d["title"] == title), None)
            if old and old["current_version"] and not args.reindex:
                report["uploads"].append(
                    {
                        "format": filename.split(".")[-1],
                        "status": "already_ready",
                        "document_id": old["id"],
                    }
                )
                continue
            if old and old["current_version"]:
                item = checked(
                    client.post(
                        f"/admin/documents/{old['id']}/versions",
                        headers=admin_headers,
                        data={"expected_current_version_id": old["current_version"]["id"]},
                        files={"file": (filename, content)},
                    ),
                    202,
                )
            else:
                item = checked(
                    client.post(
                        f"/admin/knowledge-bases/{kb['id']}/documents",
                        headers=admin_headers,
                        data={"title": title},
                        files={"file": (filename, content)},
                    ),
                    202,
                )
            for _ in range(100):
                job = checked(
                    client.get(f"/admin/ingestion-jobs/{item['job_id']}", headers=admin_headers)
                )
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.5)
            if job["status"] != "completed":
                raise RuntimeError("Ingestion failed: " + str(job.get("error_code")))
            chunks = checked(
                client.get(
                    f"/admin/document-versions/{item['version_id']}/chunks",
                    headers=admin_headers,
                )
            )["items"]
            assert chunks and all(
                c["anchor"]["type"] in {"markdown", "docx", "pdf"} for c in chunks
            )
            report["uploads"].append(
                {
                    "format": filename.split(".")[-1],
                    "status": "completed",
                    "chunks": len(chunks),
                    "document_id": item["document_id"],
                }
            )
        employee = checked(
            client.post(
                "/auth/login",
                json={
                    "username": "employee_li",
                    "password": credentials["employee_li"],
                },
            )
        )
        headers = {"Authorization": "Bearer " + employee["access_token"]}
        for query, required in [
            ("北京住宿标准", "300"),
            ("出差报销需要什么材料", "行程"),
        ]:
            result = checked(
                client.post(
                    "/search",
                    headers=headers,
                    json={"query": query, "kb_ids": [kb["id"]]},
                )
            )
            assert any(required in h["content"] for h in result["items"]), (
                "Expected evidence not retrieved"
            )
            source = result["items"][0]
            checked(client.get(f"/document-chunks/{source['chunk_id']}", headers=headers))
            download = client.get(source["download_path"], headers=headers)
            assert download.status_code == 200 and download.content
            report["searches"].append(
                {"query": query, "hit_count": result["total"], "evidence_found": True}
            )
        after = checked(client.get("/admin/usage", headers=admin_headers))
        report.update(
            requests_this_run=after["requests"] - before["requests"],
            estimated_cost_cny=after["estimated_cost_cny"],
            status="passed",
        )
    (ROOT / ".m2").mkdir(exist_ok=True)
    (ROOT / ".m2/live-smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider response bodies and keys are intentionally excluded.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "type": type(exc).__name__,
                    "message": str(exc)
                    if isinstance(exc, (RuntimeError, AssertionError))
                    else "See local diagnostics",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
