"""Explicitly import the generated fictional policy and its verified demo rules.

Only edits the named demo rules whose old sources were deleted, or resumes this
same import. Never resurrects deleted documents or changes users/KB grants.
"""

import argparse
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "output/knowledge_documents/xingzhou_expense_policy_v1.md"
STATE = ROOT / ".cache/expense-policy-import-state.json"
TITLE = "虚构演示制度-综合费用核算与报销"
SPECS = [
    (
        "演示-S1-北京上海住宿",
        "lodging",
        "S1",
        ["北京", "上海"],
        "300.00",
        None,
        "1",
        100,
        "TRV-S1-A",
    ),
    (
        "演示-S2-北京上海住宿",
        "lodging",
        "S2",
        ["北京", "上海"],
        "500.00",
        None,
        "1",
        100,
        "TRV-S2-A",
    ),
    (
        "演示-S1-杭州南京住宿",
        "lodging",
        "S1",
        ["杭州", "南京"],
        "200.00",
        None,
        "1",
        100,
        "TRV-S1-B",
    ),
    (
        "演示-S2-杭州南京住宿",
        "lodging",
        "S2",
        ["杭州", "南京"],
        "350.00",
        None,
        "1",
        100,
        "TRV-S2-B",
    ),
    ("演示-每日补贴", "daily", None, [], "80.00", None, "1", 100, "TRV-DAILY-01"),
    (
        "综合费用演示-办公采购",
        "office",
        None,
        [],
        "150.00",
        "1500.00",
        "1",
        20,
        "OFFICE-01",
    ),
    (
        "综合费用演示-培训报销",
        "training",
        None,
        [],
        "2000.00",
        "5000.00",
        "0.8",
        10,
        "TRAINING-01",
    ),
    (
        "综合费用演示-业务招待",
        "hospitality",
        None,
        [],
        "150.00",
        "2000.00",
        "1",
        30,
        "HOSPITALITY-01",
    ),
]


def parameters_match(rule, body):
    return (
        rule["kind"] == body["kind"]
        and rule["grade"] == body["grade"]
        and sorted(rule["cities"]) == sorted(body["cities"])
        and Decimal(rule["unit_rate"]) == Decimal(body["unit_rate"])
        and (
            None
            if rule.get("application_cap") is None
            else Decimal(rule["application_cap"])
        )
        == (
            None
            if body["application_cap"] is None
            else Decimal(body["application_cap"])
        )
        and Decimal(rule.get("reimbursement_ratio", "1"))
        == Decimal(body["reimbursement_ratio"])
        and rule.get("max_quantity", 100) == body["max_quantity"]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload fictional policy and enable its source-checked demo rules",
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error(
            "--apply is required; this writes the authorized demo document and rules"
        )
    raw = POLICY.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    checkpoints = (
        json.loads(STATE.read_text("utf-8")) if STATE.exists() else {"rules": {}}
    )
    if checkpoints.get("sha256") not in {None, fingerprint}:
        raise RuntimeError(
            "Import checkpoint refers to a different policy; review before proceeding"
        )
    credentials = json.loads(
        (ROOT / "data/runtime/demo-credentials.json").read_text("utf-8")
    )
    with httpx.Client(
        base_url="http://127.0.0.1:8000/api/v1",
        timeout=30,
        trust_env=False,
        headers={"Origin": "http://127.0.0.1:5173"},
    ) as client:

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                try:
                    code = (
                        response.json().get("error", {}).get("code", "REQUEST_FAILED")
                    )
                except (ValueError, AttributeError, TypeError):
                    code = "REQUEST_FAILED"
                raise RuntimeError(
                    f"Local API request failed: HTTP {response.status_code}, {code}"
                )
            return response.json()["data"] if response.content else None

        token = call(
            "POST",
            "/auth/login",
            json={"username": "admin", "password": credentials["admin"]},
        )["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        kbs = call("GET", "/knowledge-bases")["items"]
        kb = next(
            item for item in kbs if item["name"] == "企业公共制度" and item["is_active"]
        )
        rules = call("GET", "/admin/fee-rules")["items"]
        names = {row[0] for row in SPECS}
        for rule in rules:
            if (
                rule["name"] not in names
                and rule["status"] == "active"
                and rule["effective_status"] == "active"
            ):
                raise RuntimeError(
                    "An additional active policy exists; explicit rule conflict review required"
                )
        docs = call("GET", f"/knowledge-bases/{kb['id']}/documents")["items"]
        existing = [doc for doc in docs if doc["title"] == TITLE]
        if len(existing) > 1:
            raise RuntimeError(
                "Multiple documents match the policy title; review duplicates first"
            )
        if existing:
            doc = existing[0]
            version = doc["current_version"]
            if not version or version["status"] != "ready":
                raise RuntimeError(
                    "Existing policy is not ready; review the ingestion job first"
                )
            downloaded = client.get(
                f"/documents/{doc['id']}/versions/{version['id']}/download"
            )
            downloaded.raise_for_status()
            if hashlib.sha256(downloaded.content).hexdigest() != fingerprint:
                raise RuntimeError(
                    "An existing document has different contents; no overwrite performed"
                )
            doc_id, version_id = doc["id"], version["id"]
        else:
            uploaded = call(
                "POST",
                f"/admin/knowledge-bases/{kb['id']}/documents",
                data={"title": TITLE},
                files={"file": (POLICY.name, raw, "text/markdown")},
            )
            checkpoints.update(
                sha256=fingerprint,
                document_id=uploaded["document_id"],
                version_id=uploaded["version_id"],
            )
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(
                json.dumps(checkpoints, ensure_ascii=False, indent=2), "utf-8"
            )
            print(
                "Uploaded fictional policy; waiting for document indexing.", flush=True
            )
            for _ in range(120):
                job = call("GET", f"/admin/ingestion-jobs/{uploaded['job_id']}")
                if job["status"] == "completed":
                    break
                if job["status"] == "failed":
                    raise RuntimeError(
                        "Policy indexing failed: " + str(job["error_code"])
                    )
                time.sleep(1)
            else:
                raise RuntimeError("Policy indexing did not finish in the bounded wait")
            doc_id, version_id = uploaded["document_id"], uploaded["version_id"]
        chunks = call("GET", f"/admin/document-versions/{version_id}/chunks")["items"]
        bodies = []
        for name, kind, grade, cities, rate, cap, ratio, maximum, marker in SPECS:
            matches = [chunk for chunk in chunks if marker in chunk["content"]]
            if len(matches) != 1:
                raise RuntimeError(
                    "Rule source marker is missing or ambiguous: " + marker
                )
            source = matches[0]
            body = {
                "name": name,
                "kind": kind,
                "grade": grade,
                "cities": cities,
                "unit_rate": rate,
                "application_cap": cap,
                "reimbursement_ratio": ratio,
                "max_quantity": maximum,
                "source_chunk_id": source["chunk_id"],
                "source_quote": source["content"],
            }
            previous = [rule for rule in rules if rule["name"] == name]
            if len(previous) > 1:
                raise RuntimeError("Duplicate demo rules require review: " + name)
            old = previous[0] if previous else None
            if old:
                same_source = (
                    old.get("source_document_id") == doc_id
                    and old["source_chunk_id"] == body["source_chunk_id"]
                )
                if not parameters_match(old, body):
                    raise RuntimeError(
                        "Existing rule parameters changed; refusing to overwrite: "
                        + name
                    )
                resumable = old["id"] == checkpoints["rules"].get(name)
                if (
                    not same_source
                    and not resumable
                    and not (
                        old["status"] == "suspended"
                        and old["suspension_reason"] == "DOCUMENT_DELETED"
                    )
                ):
                    raise RuntimeError(
                        "Existing rule is not eligible for migration: " + name
                    )
            bodies.append((body, old))
        checkpoints.update(
            sha256=fingerprint,
            document_id=doc_id,
            version_id=version_id,
            kb_id=kb["id"],
        )
        for body, old in bodies:
            if (
                old
                and old["source_chunk_id"] == body["source_chunk_id"]
                and old["status"] == "active"
            ):
                value = old
            else:
                value = (
                    call(
                        "PUT",
                        f"/admin/fee-rules/{old['id']}",
                        json={**body, "expected_revision": old["revision"]},
                    )
                    if old
                    else call("POST", "/admin/fee-rules", json=body)
                )
                checkpoints["rules"][body["name"]] = value["id"]
                STATE.write_text(
                    json.dumps(checkpoints, ensure_ascii=False, indent=2), "utf-8"
                )
                value = call(
                    "POST",
                    f"/admin/fee-rules/{value['id']}/activate",
                    json={
                        "expected_revision": value["revision"],
                        "source_checked": True,
                    },
                )
            checkpoints["rules"][body["name"]] = value["id"]
            print("Verified and enabled fictional rule: " + body["name"], flush=True)
        STATE.write_text(json.dumps(checkpoints, ensure_ascii=False, indent=2), "utf-8")
        print(
            json.dumps(
                {
                    "document_id": doc_id,
                    "version_id": version_id,
                    "knowledge_base": "企业公共制度",
                    "enabled_rules": len(bodies),
                    "changed_grants": False,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
