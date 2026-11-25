"""Prepare workspace-local configuration; never print credentials."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    for relative in (".m0", ".cache/uv", ".cache/npm", ".cache/jieba"):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)
    target = ROOT / ".env"
    created = not target.exists()
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    content = content.replace(
        "POSTGRES_PASSWORD=generated-by-bootstrap",
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}",
    ).replace(
        "POSTGRES_APP_PASSWORD=generated-by-bootstrap",
        f"POSTGRES_APP_PASSWORD={secrets.token_urlsafe(32)}",
    ).replace(
        "JWT_SECRET_KEY=generated-by-bootstrap",
        f"JWT_SECRET_KEY={secrets.token_urlsafe(48)}",
    )
    added = 0
    if created:
        target.write_text(content, encoding="utf-8")
    else:
        existing = target.read_text(encoding="utf-8")
        keys = {line.partition("=")[0].strip() for line in existing.splitlines() if "=" in line}
        missing = [
            line
            for line in content.splitlines()
            if "=" in line and line.partition("=")[0] not in keys
        ]
        added = len(missing)
        if missing:
            target.write_text(
                existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n", encoding="utf-8"
            )
    print(
        json.dumps(
            {"local_env_created": created, "defaults_added": added, "model_api_tests": "deferred"}
        )
    )


if __name__ == "__main__":
    main()
