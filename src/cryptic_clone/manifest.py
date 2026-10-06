"""Persistent article state used to plan safe source and vector-store deltas."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Any

MANIFEST_VERSION = 1


def empty_manifest() -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "last_successful_run": None,
        "last_run_was_full_snapshot": False,
        "articles": {},
    }


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_manifest()

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise ValueError(f"Unsupported or invalid manifest: {path}")
    if not isinstance(payload.get("articles"), dict):
        raise TypeError(f"Manifest field 'articles' must be an object: {path}")
    return payload


def save_manifest(
    path: Path,
    manifest: dict[str, Any],
    *,
    update_source_timestamp: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if update_source_timestamp:
        manifest["last_successful_run"] = datetime.now(UTC).isoformat()
    content = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        for attempt in range(4):
            try:
                os.replace(temporary_name, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                sleep(0.05 * (attempt + 1))
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
