"""Publish the four reviewed fictitious fixtures; never overwrite unrecognized user edits."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def seed():
    frozen = json.loads((ROOT / "data/evaluation/m6-manifest.json").read_text("utf-8"))
    credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
    output = ROOT / ".m6"
    output.mkdir(exist_ok=True)
    report = {
        "documents": [],
        "rules": [],
        "materials": "m6-v1; fictitious and user-reviewed",
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

        identity = checked(
            client.post(
                "/auth/login",
                json={"username": "admin", "password": credentials["admin"]},
            )
        )
        client.headers["Authorization"] = "Bearer " + identity["access_token"]
        bases = checked(client.get("/knowledge-bases"))["items"]
        public = next(k for k in bases if k["name"] == "企业公共制度")
        restricted = next(k for k in bases if "研发" in k["name"])
        sources = {}
        titles = {
            "TRAVEL": "住宿标准",
            "DAILY": "差旅补贴",
            "PROCESS": "报销材料与办理流程",
            "RD": "研发项目差旅归档要求",
        }
        for code, name in titles.items():
            kb = restricted if code == "RD" else public
            content = (ROOT / f"data/sample_documents/m6/POL-{code}.md").read_bytes()
            expected_hash = hashlib.sha256(content).hexdigest()
            if expected_hash != frozen["materials"][f"POL-{code}"]["sha256"]:
                raise RuntimeError(f"{code}: frozen material hash mismatch")
            title = "虚构演示制度-" + name
            existing = checked(client.get(f"/knowledge-bases/{kb['id']}/documents"))["items"]
            doc = next((d for d in existing if d["title"] == title), None)
            filename = f"m4-{code.lower()}.md" if code in {"TRAVEL", "DAILY"} else f"POL-{code}.md"
            if doc:
                version = doc["current_version"]
                if not version or not version["effective"]:
                    raise RuntimeError(
                        f"{code}: existing source unavailable; manual review required"
                    )
                download = client.get(f"/documents/{doc['id']}/versions/{version['id']}/download")
                assert download.status_code == 200
                actual_hash = hashlib.sha256(download.content).hexdigest()
                if actual_hash != expected_hash:
                    legacy = ROOT / f"data/demo_policies/m4-{code.lower()}.md"
                    if (
                        not legacy.exists()
                        or actual_hash != hashlib.sha256(legacy.read_bytes()).hexdigest()
                    ):
                        raise RuntimeError(
                            f"{code}: source differs from both reviewed fixtures; refusing overwrite"
                        )
                    item = checked(
                        client.post(
                            f"/admin/documents/{doc['id']}/versions",
                            data={"expected_current_version_id": version["id"]},
                            files={"file": (filename, content)},
                        ),
                        202,
                    )
                else:
                    item = {"document_id": doc["id"], "version_id": version["id"]}
            else:
                item = checked(
                    client.post(
                        f"/admin/knowledge-bases/{kb['id']}/documents",
                        data={
                            "title": title,
                            "effective_from": "2026-11-01T00:00:00+08:00",
                        },
                        files={"file": (filename, content)},
                    ),
                    202,
                )
            if item.get("job_id"):
                for _ in range(150):
                    job = checked(client.get(f"/admin/ingestion-jobs/{item['job_id']}"))
                    if job["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.3)
                if job["status"] != "completed":
                    raise RuntimeError(
                        f"{code}: ingestion incomplete; rerun after inspecting job status"
                    )
            chunks = checked(client.get(f"/admin/document-versions/{item['version_id']}/chunks"))[
                "items"
            ]
            sources[code] = chunks
            report["documents"].append({"code": code, **item, "sha256": expected_hash})
        definitions = [
            (
                "演示-S1-北京上海住宿",
                "lodging",
                "S1",
                ["北京", "上海"],
                "300.00",
                "TRAVEL",
                "300元/晚",
            ),
            (
                "演示-S2-北京上海住宿",
                "lodging",
                "S2",
                ["北京", "上海"],
                "500.00",
                "TRAVEL",
                "500元/晚",
            ),
            (
                "演示-S1-杭州南京住宿",
                "lodging",
                "S1",
                ["杭州", "南京"],
                "200.00",
                "TRAVEL",
                "200元/晚",
            ),
            (
                "演示-S2-杭州南京住宿",
                "lodging",
                "S2",
                ["杭州", "南京"],
                "350.00",
                "TRAVEL",
                "350元/晚",
            ),
            ("演示-每日补贴", "daily", None, [], "80.00", "DAILY", "统一80元/天"),
        ]
        existing_rules = checked(client.get("/admin/fee-rules"))["items"]
        for name, kind, grade, cities, rate, code, needle in definitions:
            source = next(c for c in sources[code] if needle in c["content"])
            rule = next((r for r in existing_rules if r["name"] == name), None)
            if rule is None:
                rule = checked(
                    client.post(
                        "/admin/fee-rules",
                        json={
                            "name": name,
                            "kind": kind,
                            "grade": grade,
                            "cities": cities,
                            "unit_rate": rate,
                            "source_chunk_id": source["chunk_id"],
                            "source_quote": source["content"],
                        },
                    ),
                    201,
                )
                rule = checked(
                    client.post(
                        f"/admin/fee-rules/{rule['id']}/activate",
                        json={
                            "expected_revision": rule["revision"],
                            "source_checked": True,
                        },
                    )
                )
            elif (
                rule["status"] == "suspended"
                and rule["unit_rate"] == rate
                and rule["suspension_reason"] == "SOURCE_CHANGED"
            ):
                review = checked(client.get(f"/admin/fee-rules/{rule['id']}/source-review"))
                rule = checked(
                    client.post(
                        f"/admin/fee-rules/{rule['id']}/recover",
                        json={
                            "expected_revision": rule["revision"],
                            "expected_source_version_id": review["current_version_id"],
                            "source_chunk_id": source["chunk_id"],
                            "source_quote": source["content"],
                            "unit_rate": rate,
                            "source_checked": True,
                        },
                    )
                )
            elif (
                rule["status"] != "active"
                or rule["unit_rate"] != rate
                or rule["source_chunk_id"] != source["chunk_id"]
            ):
                raise RuntimeError(f"{name}: unrecognized rule state; manual review required")
            report["rules"].append(
                {k: rule[k] for k in ["name", "status", "unit_rate", "revision"]}
            )
    (output / "demo-setup.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        json.dumps(
            {
                "documents": len(report["documents"]),
                "active_rules": len(report["rules"]),
                "report": ".m6/demo-setup.json",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    if not parser.parse_args().live:
        parser.error("--live is required for document embedding calls")
    seed()
