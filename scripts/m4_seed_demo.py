"""Explicit local demo initialization: two reviewed fictitious policies and five rules."""

import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def seed(args):
    credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
    report = {
        "documents": [],
        "rules": [],
        "note": "Fictitious demo fixtures, not real company policy. Only newly created rules may be activated by this script.",
    }
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1",
        headers={"Origin": "http://127.0.0.1:5173"},
        timeout=40,
        trust_env=False,
    ) as client:

        def checked(response, status=200):
            if response.status_code != status:
                raise RuntimeError(f"HTTP {response.status_code} at {response.request.url.path}")
            return response.json()["data"]

        auth = checked(
            client.post("/auth/login", json={"username": "admin", "password": credentials["admin"]})
        )
        client.headers["Authorization"] = "Bearer " + auth["access_token"]
        kb = next(
            k
            for k in checked(client.get("/knowledge-bases"))["items"]
            if k["name"] == "企业公共制度"
        )
        existing = checked(client.get(f"/knowledge-bases/{kb['id']}/documents"))["items"]
        sources = {}
        for code, title, needle in [
            ("travel", "虚构演示制度-住宿标准", "300元/晚"),
            ("daily", "虚构演示制度-差旅补贴", "80元/天"),
        ]:
            path = ROOT / f"data/demo_policies/m4-{code}.md"
            document = next((d for d in existing if d["title"] == title), None)
            if document is None:
                uploaded = checked(
                    client.post(
                        f"/admin/knowledge-bases/{kb['id']}/documents",
                        data={"title": title},
                        files={"file": (path.name, path.read_bytes())},
                    ),
                    202,
                )
                for _ in range(100):
                    job = checked(client.get(f"/admin/ingestion-jobs/{uploaded['job_id']}"))
                    if job["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.3)
                if job["status"] != "completed":
                    raise RuntimeError("Demo ingestion failed; inspect document status")
                doc_id, version_id = uploaded["document_id"], uploaded["version_id"]
            else:
                if not document["current_version"] or not document["current_version"]["effective"]:
                    raise RuntimeError(
                        "Existing demo document is unavailable; refusing to overwrite it"
                    )
                doc_id, version_id = document["id"], document["current_version"]["id"]
            chunks = checked(client.get(f"/admin/document-versions/{version_id}/chunks"))["items"]
            chunk = next(c for c in chunks if needle in c["content"])
            if not all(line in path.read_text("utf-8") for line in chunk["content"].splitlines()):
                raise RuntimeError(
                    "Existing policy differs from the reviewed fixture; manual review required"
                )
            sources[code] = chunk
            if code == "travel":
                sources["travel_a"] = chunk
                sources["travel_b"] = next(c for c in chunks if "200元/晚" in c["content"])
            report["documents"].append(
                {
                    "title": title,
                    "document_id": doc_id,
                    "version_id": version_id,
                    "source_chunk_id": chunk["chunk_id"],
                }
            )
        existing_rules = checked(client.get("/admin/fee-rules"))["items"]
        definitions = [
            ("演示-S1-北京上海住宿", "lodging", "S1", ["北京", "上海"], "300.00", "travel_a"),
            ("演示-S2-北京上海住宿", "lodging", "S2", ["北京", "上海"], "500.00", "travel_a"),
            ("演示-S1-杭州南京住宿", "lodging", "S1", ["杭州", "南京"], "200.00", "travel_b"),
            ("演示-S2-杭州南京住宿", "lodging", "S2", ["杭州", "南京"], "350.00", "travel_b"),
            ("演示-每日补贴", "daily", None, [], "80.00", "daily"),
        ]
        assert all(s in sources["travel_a"]["content"] for s in ["300", "500", "北京", "上海"])
        assert all(s in sources["travel_b"]["content"] for s in ["200", "350", "杭州", "南京"])
        for name, kind, grade, cities, rate, code in definitions:
            rule = next((r for r in existing_rules if r["name"] == name), None)
            if rule is None:
                chunk = sources[code]
                rule = checked(
                    client.post(
                        "/admin/fee-rules",
                        json={
                            "name": name,
                            "kind": kind,
                            "grade": grade,
                            "cities": cities,
                            "unit_rate": rate,
                            "source_chunk_id": chunk["chunk_id"],
                            "source_quote": chunk["content"],
                        },
                    ),
                    201,
                )
                if args.activate_demo_rules:
                    rule = checked(
                        client.post(
                            f"/admin/fee-rules/{rule['id']}/activate",
                            json={"expected_revision": rule["revision"], "source_checked": True},
                        )
                    )
            report["rules"].append(
                {k: rule[k] for k in ["id", "name", "status", "revision", "unit_rate"]}
            )
        report["estimated_usage"] = checked(client.get("/admin/usage"))
    (ROOT / ".m4").mkdir(exist_ok=True)
    (ROOT / ".m4/demo-setup.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="Permit bounded live document embedding requests"
    )
    parser.add_argument(
        "--activate-demo-rules",
        action="store_true",
        help="Activate only newly created rules after reviewed fixture checks",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required")
    seed(args)
