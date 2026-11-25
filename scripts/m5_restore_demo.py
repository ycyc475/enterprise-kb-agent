"""Recover the explicitly checkpointed browser fixture after an interrupted M5 test."""

import argparse
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def restore():
    checkpoint = json.loads((ROOT / ".m5/demo-checkpoint.json").read_text("utf-8"))
    body = (ROOT / ".m5/original-travel.md").read_bytes()
    if hashlib.sha256(body).hexdigest() != checkpoint["sha256"]:
        raise RuntimeError("Checkpoint content checksum mismatch")
    credentials = json.loads((ROOT / "data/runtime/demo-credentials.json").read_text("utf-8"))
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1",
        headers={"Origin": "http://127.0.0.1:5173"},
        timeout=15,
        trust_env=False,
    ) as client:

        def checked(response, status=200):
            if response.status_code != status:
                raise RuntimeError(f"HTTP {response.status_code} at {response.request.url.path}")
            return response.json()["data"]

        identity = checked(
            client.post("/auth/login", json={"username": "admin", "password": credentials["admin"]})
        )
        client.headers["Authorization"] = "Bearer " + identity["access_token"]

        def current():
            for _ in range(100):
                docs = checked(client.get(f"/knowledge-bases/{checkpoint['kb_id']}/documents"))[
                    "items"
                ]
                doc = next(d for d in docs if d["id"] == checkpoint["document_id"])
                if doc["title"] != "虚构演示制度-住宿标准":
                    raise RuntimeError("Checkpoint target is not the M5 fixture")
                if doc.get("job", {}).get("status") not in {"queued", "processing"}:
                    return doc
                time.sleep(0.3)
            raise RuntimeError("Fixture processing did not finish")

        doc = current()
        version = doc["current_version"]
        response = client.get(f"/documents/{doc['id']}/versions/{version['id']}/download")
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != checkpoint["sha256"]:
            if version["original_name"] not in {
                checkpoint["original_name"],
                "m5-updated-travel.md",
            }:
                raise RuntimeError("A different document update exists; refusing to overwrite it")
            checked(
                client.post(
                    f"/admin/documents/{doc['id']}/versions",
                    data={"expected_current_version_id": version["id"]},
                    files={"file": (checkpoint["original_name"], body)},
                ),
                202,
            )
            doc = current()
        for rule in checkpoint["rules"]:
            value = checked(client.get(f"/admin/fee-rules/{rule['id']}/source-review"))
            if value["rule"]["source_document_id"] != checkpoint["document_id"]:
                raise RuntimeError("Rule source was changed outside the checkpoint")
            if value["rule"]["status"] == "active":
                if Decimal(value["rule"]["unit_rate"]) != Decimal(rule["unit_rate"]):
                    raise RuntimeError("An active rule was changed outside the checkpoint")
                continue
            rate = format(Decimal(rule["unit_rate"]), "f").rstrip("0").rstrip(".")
            candidate = next(
                c
                for c in value["candidates"]
                if rule["cities"][0] in c["content"] and rate in c["content"]
            )
            checked(
                client.post(
                    f"/admin/fee-rules/{rule['id']}/recover",
                    json={
                        "expected_revision": value["rule"]["revision"],
                        "expected_source_version_id": value["current_version_id"],
                        "source_chunk_id": candidate["chunk_id"],
                        "source_quote": candidate["content"],
                        "unit_rate": rule["unit_rate"],
                        "source_checked": True,
                    },
                )
            )
        print("Restored checkpointed policy content and four lodging rules.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    if not args.restore:
        parser.error("Explicit --restore is required")
    restore()
