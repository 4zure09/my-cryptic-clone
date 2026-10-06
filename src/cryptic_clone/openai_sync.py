"""Upload only changed Markdown documents to an OpenAI vector store."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptic_clone.manifest import load_manifest, save_manifest

LOGGER = logging.getLogger(__name__)
CHUNK_COUNT_METHOD = "lexical_static_v1"


@dataclass(frozen=True, slots=True)
class VectorSyncResult:
    added: int
    updated: int
    skipped: int
    deleted: int
    files_removed: int
    files_embedded: int
    chunks_embedded: int


def resolve_vector_store(
    *,
    client: Any,
    state_file: Path,
    configured_id: str | None,
    name: str = "opb-store",
) -> tuple[str, bool]:
    """Return a reusable vector store, creating and persisting one when needed."""
    manifest = load_manifest(state_file)
    openai_state = manifest.setdefault("openai", {})
    if not isinstance(openai_state, dict):
        raise TypeError("Manifest field 'openai' must be an object")

    stored_id = openai_state.get("vector_store_id")
    if stored_id is not None and not isinstance(stored_id, str):
        raise TypeError("Manifest OpenAI vector_store_id must be a string")

    requested_id = configured_id.strip() if configured_id else None
    if requested_id and stored_id and requested_id != stored_id:
        has_uploaded_files = any(
            isinstance(record, dict) and isinstance(record.get("vector_file_id"), str)
            for record in manifest["articles"].values()
        )
        if has_uploaded_files:
            raise ValueError(
                "OPENAI_VECTOR_STORE_ID differs from the store recorded in STATE_FILE. "
                "Use the recorded store or a fresh STATE_FILE to avoid corrupting delta state."
            )

    vector_store_id = requested_id or stored_id
    created = False
    if not vector_store_id:
        vector_store = client.vector_stores.create(
            name=name,
            description="Normalized OptiSigns support articles",
        )
        vector_store_id = vector_store.id
        created = True

    if not vector_store_id.startswith("vs_"):
        raise ValueError("OPENAI_VECTOR_STORE_ID must start with 'vs_'")

    if stored_id != vector_store_id:
        openai_state["vector_store_id"] = vector_store_id
        openai_state["vector_store_name"] = name
        openai_state["created_by_job"] = created
        save_manifest(state_file, manifest, update_source_timestamp=False)

    return vector_store_id, created


def attach_vector_store_to_assistant(
    *,
    client: Any,
    assistant_id: str,
    vector_store_id: str,
) -> None:
    """Attach a store to a legacy Playground Assistant without dropping its tools."""
    if assistant_id.startswith("agent_"):
        raise ValueError(
            "ASSISTANT_ID received an Agents API ID (agent_...). The legacy Assistant "
            "attachment endpoint requires an asst_... ID; use Responses file_search for "
            "the current API."
        )
    if not assistant_id.startswith("asst_"):
        raise ValueError("ASSISTANT_ID must start with 'asst_'")

    assistant = client.beta.assistants.retrieve(assistant_id)
    tools = [tool.model_dump(exclude_none=True) for tool in assistant.tools]
    if not any(tool.get("type") == "file_search" for tool in tools):
        tools.append({"type": "file_search"})

    tool_resources: dict[str, Any] = {}
    if assistant.tool_resources is not None:
        tool_resources = assistant.tool_resources.model_dump(exclude_none=True)
    tool_resources["file_search"] = {"vector_store_ids": [vector_store_id]}

    client.beta.assistants.update(
        assistant_id,
        tools=tools,
        tool_resources=tool_resources,
    )


def run_sanity_check(
    *,
    client: Any,
    vector_store_id: str,
    model: str,
    question: str,
    instructions: str,
) -> str:
    """Ask one grounded question through the supported Responses file-search API."""
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=question,
        tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
    )
    return response.output_text


def _save_vector_progress(state_file: Path, manifest: dict[str, Any]) -> None:
    manifest["last_vector_sync"] = datetime.now(UTC).isoformat()
    save_manifest(state_file, manifest, update_source_timestamp=False)


def _estimate_chunks(
    article_path: Path,
    *,
    chunk_max_tokens: int,
    chunk_overlap_tokens: int,
) -> int:
    """Estimate static chunks locally; the vector API exposes no chunk total."""
    text = article_path.read_text(encoding="utf-8")
    token_count = len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))
    if token_count <= chunk_max_tokens:
        return 1
    stride = chunk_max_tokens - chunk_overlap_tokens
    return 1 + math.ceil((token_count - chunk_max_tokens) / stride)


def _delete_vector_file(client: Any, vector_store_id: str, file_id: str) -> None:
    try:
        client.vector_stores.files.delete(file_id, vector_store_id=vector_store_id)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        LOGGER.info("Vector-store file %s was already absent", file_id)


def _cleanup_stale_files(
    client: Any,
    vector_store_id: str,
    record: dict[str, Any],
) -> int:
    raw_stale_ids = record.get("stale_vector_file_ids", [])
    if not isinstance(raw_stale_ids, list):
        raise TypeError("stale_vector_file_ids must be a list")
    stale_ids = [file_id for file_id in raw_stale_ids if isinstance(file_id, str)]
    deleted = 0
    for file_id in stale_ids:
        _delete_vector_file(client, vector_store_id, file_id)
        deleted += 1
    record["stale_vector_file_ids"] = []
    return deleted


def sync_vector_store(
    *,
    client: Any,
    vector_store_id: str,
    output_dir: Path,
    state_file: Path,
    chunk_max_tokens: int = 800,
    chunk_overlap_tokens: int = 120,
) -> VectorSyncResult:
    """Synchronize source manifest changes to one OpenAI vector store."""
    if chunk_max_tokens < 100 or chunk_max_tokens > 4096:
        raise ValueError("chunk_max_tokens must be between 100 and 4096")
    if chunk_overlap_tokens < 0 or chunk_overlap_tokens > chunk_max_tokens // 2:
        raise ValueError("chunk_overlap_tokens must be at most half the chunk size")

    manifest = load_manifest(state_file)
    manifest_articles = manifest["articles"]
    missing_files = []
    for article_id, record in manifest_articles.items():
        if not isinstance(record, dict):
            raise TypeError(f"Manifest article {article_id} must be an object")
        if record.get("deleted", False):
            continue
        filename = record.get("filename")
        if not isinstance(filename, str) or not (output_dir / filename).is_file():
            missing_files.append(str(filename or article_id))
    if missing_files:
        preview = ", ".join(missing_files[:3])
        raise FileNotFoundError(
            f"Vector sync aborted before upload: {len(missing_files)} active Markdown files "
            f"are missing (first: {preview}). Run a matching scraper snapshot first."
        )

    added = updated = skipped = deleted = files_removed = chunks_embedded = 0

    for article_id, record in manifest_articles.items():
        if not isinstance(record, dict):
            raise TypeError(f"Manifest article {article_id} must be an object")

        removed = _cleanup_stale_files(client, vector_store_id, record)
        if removed:
            files_removed += removed
            _save_vector_progress(state_file, manifest)

        active_file_id = record.get("vector_file_id")
        if record.get("deleted", False):
            if isinstance(active_file_id, str):
                record.setdefault("stale_vector_file_ids", []).append(active_file_id)
                record["vector_file_id"] = None
                record["uploaded_hash"] = None
                record["vector_chunk_count"] = 0
                _save_vector_progress(state_file, manifest)
                files_removed += _cleanup_stale_files(client, vector_store_id, record)
                deleted += 1
                record["vector_deleted_at"] = datetime.now(UTC).isoformat()
                _save_vector_progress(state_file, manifest)
            continue

        document_hash = record.get("document_hash")
        filename = record.get("filename")
        if not isinstance(document_hash, str) or not isinstance(filename, str):
            raise TypeError(f"Manifest article {article_id} is missing source state")

        if isinstance(active_file_id, str) and record.get("uploaded_hash") == document_hash:
            if (
                not isinstance(record.get("vector_chunk_count"), int)
                or record.get("vector_chunk_count_method") != CHUNK_COUNT_METHOD
            ):
                record["vector_chunk_count"] = _estimate_chunks(
                    output_dir / filename,
                    chunk_max_tokens=chunk_max_tokens,
                    chunk_overlap_tokens=chunk_overlap_tokens,
                )
                record["vector_chunk_count_method"] = CHUNK_COUNT_METHOD
                _save_vector_progress(state_file, manifest)
            skipped += 1
            continue

        article_path = output_dir / filename
        if not article_path.is_file():
            raise FileNotFoundError(f"Markdown article does not exist: {article_path}")

        was_update = isinstance(active_file_id, str)
        with article_path.open("rb") as article_file:
            uploaded = client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store_id,
                file=article_file,
                attributes={
                    "article_id": article_id,
                    "source_url": str(record.get("article_url", ""))[:512],
                    "document_hash": document_hash,
                },
                chunking_strategy={
                    "type": "static",
                    "static": {
                        "max_chunk_size_tokens": chunk_max_tokens,
                        "chunk_overlap_tokens": chunk_overlap_tokens,
                    },
                },
            )

        if uploaded.status != "completed":
            raise RuntimeError(
                f"Vector indexing failed for article {article_id}: status={uploaded.status}"
            )

        if was_update and active_file_id != uploaded.id:
            record.setdefault("stale_vector_file_ids", []).append(active_file_id)
        record["vector_file_id"] = uploaded.id
        record["uploaded_hash"] = document_hash
        record["vector_chunk_count"] = None
        record["vector_usage_bytes"] = getattr(uploaded, "usage_bytes", None)
        record["vector_uploaded_at"] = datetime.now(UTC).isoformat()
        _save_vector_progress(state_file, manifest)

        chunk_count = _estimate_chunks(
            article_path,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
        record["vector_chunk_count"] = chunk_count
        record["vector_chunk_count_method"] = CHUNK_COUNT_METHOD
        chunks_embedded += chunk_count
        _save_vector_progress(state_file, manifest)

        files_removed += _cleanup_stale_files(client, vector_store_id, record)
        _save_vector_progress(state_file, manifest)
        if was_update:
            updated += 1
        else:
            added += 1

    return VectorSyncResult(
        added=added,
        updated=updated,
        skipped=skipped,
        deleted=deleted,
        files_removed=files_removed,
        files_embedded=added + updated,
        chunks_embedded=chunks_embedded,
    )
