"""Frozen, independently specified acceptance fixtures shared by both evaluation modes."""

import hashlib
import json
from pathlib import Path

from tests.test_m2 import process, upload
from tests.test_m4 import create_rule, payload

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / "data/sample_documents/m6"
API = "/api/v1"


def manifest():
    return {
        p.stem: {
            "file": str(p.relative_to(ROOT)),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(MATERIALS.glob("*.md"))
    }


def prepare(client, records, admin):
    frozen = json.loads((ROOT / "data/evaluation/m6-manifest.json").read_text("utf-8"))
    assert manifest() == frozen["materials"], (
        "Frozen materials changed; review and re-freeze explicitly"
    )
    documents, chunks = {}, {}
    for code in ["TRAVEL", "DAILY", "PROCESS", "RD"]:
        kb = records["restricted" if code == "RD" else "common"]
        item = upload(
            client,
            {"common": kb},
            admin,
            name=f"POL-{code}.md",
            content=(MATERIALS / f"POL-{code}.md").read_bytes(),
            title=f"POL-{code} 虚构演示制度",
            effective_from="2026-11-01T00:00:00+08:00",
        )
        process(client, item)
        assert (
            client.get(API + f"/admin/ingestion-jobs/{item['job_id']}", headers=admin).json()[
                "data"
            ]["status"]
            == "completed"
        )
        documents[code] = item
        chunks[code] = client.get(
            API + f"/admin/document-versions/{item['version_id']}/chunks", headers=admin
        ).json()["data"]["items"]
    rules = []
    for grade, city, rate, needle in [
        ("S1", ["北京", "上海"], "300.00", "300元/晚"),
        ("S2", ["北京", "上海"], "500.00", "500元/晚"),
        ("S1", ["杭州", "南京"], "200.00", "200元/晚"),
        ("S2", ["杭州", "南京"], "350.00", "350元/晚"),
    ]:
        source = next(c for c in chunks["TRAVEL"] if needle in c["content"])
        rules.append(
            create_rule(
                client,
                admin,
                payload(source, name=grade + city[0], grade=grade, cities=city, unit_rate=rate),
            )
        )
    source = next(c for c in chunks["DAILY"] if "统一80元/天" in c["content"])
    rules.append(
        create_rule(
            client,
            admin,
            payload(
                source, name="每日补贴", kind="daily", grade=None, cities=[], unit_rate="80.00"
            ),
        )
    )
    response = client.put(
        API + f"/admin/knowledge-bases/{records['common']}/members",
        headers=admin,
        json={"expected_revision": 1, "user_ids": [records["alice"], records["bob"]]},
    )
    assert response.status_code == 200
    return documents, chunks, rules


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), "utf-8")
    temporary.replace(path)
