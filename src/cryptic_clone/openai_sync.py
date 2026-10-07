"""Stateless synchronization of Markdown documents to an OpenAI vector store."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import tiktoken

from cryptic_clone.models import MarkdownDocument

LOGGER = logging.getLogger(__name__)
STORE_MANAGED_BY = "cryptic-support-clone"
STORE_SOURCE = "support.optisigns.com"
FILE_MANAGED_BY = "zendesk-optisigns"
TOKEN_ENCODING = "cl100k_base"


@dataclass(frozen=True, slots=True)
class VectorSyncResult:
    added: int
    updated: int
    skipped: int
    deleted: int
    pending_deletion: int
    files_removed: int
    files_embedded: int
    chunks_embedded: int


def _iter_data(page: Any):
    for current_page in page.iter_pages():
        yield from current_page.data


def _metadata(resource: Any, field: str = "attributes") -> dict[str, Any]:
    value = getattr(resource, field, None)
    return value if isinstance(value, dict) else {}


def resolve_vector_store(*, client: Any, name: str) -> tuple[str, bool]:
    """Find this project's store by remote metadata, or create it once."""
    stores = _iter_data(client.vector_stores.list(limit=100, order="desc"))
    matches = [
        store
        for store in stores
        if _metadata(store, "metadata").get("managed_by") == STORE_MANAGED_BY
        and _metadata(store, "metadata").get("source") == STORE_SOURCE
    ]
    if len(matches) > 1:
        ids = ", ".join(str(store.id) for store in matches)
        raise RuntimeError(
            "Multiple project-managed vector stores were found. Delete the duplicate "
            f"stores in OpenAI before retrying: {ids}"
        )
    if matches:
        return str(matches[0].id), False

    store = client.vector_stores.create(
        name=name,
        description="Normalized OptiSigns support articles from the public Zendesk API",
        metadata={"managed_by": STORE_MANAGED_BY, "source": STORE_SOURCE},
    )
    return str(store.id), True


def _estimate_chunks(
    document: MarkdownDocument,
    *,
    chunk_max_tokens: int,
    chunk_overlap_tokens: int,
) -> tuple[int, int]:
    """Return local token and overlap-aware chunk estimates for one document."""
    text = document.path.read_text(encoding="utf-8")
    token_count = len(tiktoken.get_encoding(TOKEN_ENCODING).encode(text))
    if token_count <= chunk_max_tokens:
        return token_count, 1
    stride = chunk_max_tokens - chunk_overlap_tokens
    return token_count, 1 + math.ceil((token_count - chunk_max_tokens) / stride)


def _delete_vector_file(client: Any, vector_store_id: str, file_id: str) -> None:
    try:
        client.vector_stores.files.delete(file_id, vector_store_id=vector_store_id)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        LOGGER.info("Vector-store file %s was already absent", file_id)


def _file_sort_key(vector_file: Any) -> tuple[int, str]:
    return int(getattr(vector_file, "created_at", 0) or 0), str(vector_file.id)


def sync_vector_store(
    *,
    client: Any,
    vector_store_id: str,
    documents: tuple[MarkdownDocument, ...],
    full_snapshot: bool,
    chunk_max_tokens: int = 800,
    chunk_overlap_tokens: int = 120,
) -> VectorSyncResult:
    """Reconcile by remote file attributes, without a local manifest or cache."""
    if chunk_max_tokens < 100 or chunk_max_tokens > 4096:
        raise ValueError("chunk_max_tokens must be between 100 and 4096")
    if chunk_overlap_tokens < 0 or chunk_overlap_tokens > chunk_max_tokens // 2:
        raise ValueError("chunk_overlap_tokens must be at most half the chunk size")
    if not documents:
        raise ValueError("Refusing to sync an empty document collection")

    current_by_id = {document.article_id: document for document in documents}
    if len(current_by_id) != len(documents):
        raise ValueError("The Markdown collection contains duplicate article IDs")
    missing_paths = [str(document.path) for document in documents if not document.path.is_file()]
    if missing_paths:
        raise FileNotFoundError(f"Markdown file is missing: {missing_paths[0]}")

    remote_files = list(
        _iter_data(client.vector_stores.files.list(vector_store_id, limit=100))
    )
    remote_by_article: dict[str, list[Any]] = {}
    for vector_file in remote_files:
        attributes = _metadata(vector_file)
        if attributes.get("managed_by") != FILE_MANAGED_BY:
            continue
        article_id = attributes.get("article_id")
        if isinstance(article_id, str):
            remote_by_article.setdefault(article_id, []).append(vector_file)

    added = updated = skipped = deleted = pending_deletion = 0
    files_removed = chunks_embedded = 0

    for article_id, document in current_by_id.items():
        previous_files = remote_by_article.get(article_id, [])
        matching = [
            vector_file
            for vector_file in previous_files
            if _metadata(vector_file).get("document_hash") == document.document_hash
        ]
        if matching:
            keep = max(matching, key=_file_sort_key)
            keep_attributes = _metadata(keep)
            if keep_attributes.get("missing_runs", 0) != 0:
                client.vector_stores.files.update(
                    keep.id,
                    vector_store_id=vector_store_id,
                    attributes={**keep_attributes, "missing_runs": 0},
                )
            for stale in [item for item in previous_files if item.id != keep.id]:
                _delete_vector_file(client, vector_store_id, stale.id)
                files_removed += 1
            skipped += 1
            continue

        with document.path.open("rb") as article_file:
            uploaded = client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store_id,
                file=article_file,
                attributes={
                    "managed_by": FILE_MANAGED_BY,
                    "article_id": article_id,
                    "source_url": document.article_url[:512],
                    "document_hash": document.document_hash,
                    "missing_runs": 0,
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

        for stale in previous_files:
            _delete_vector_file(client, vector_store_id, stale.id)
            files_removed += 1
        token_count, chunk_count = _estimate_chunks(
            document,
            chunk_max_tokens=chunk_max_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )
        chunks_embedded += chunk_count
        LOGGER.info(
            "Uploaded article: %s | Files: 1 | Estimated Chunks: %d "
            "(Strategy: %d tokens/chunk, %d overlap; Tokens: %d via %s)",
            document.path.name,
            chunk_count,
            chunk_max_tokens,
            chunk_overlap_tokens,
            token_count,
            TOKEN_ENCODING,
        )
        if previous_files:
            updated += 1
        else:
            added += 1

    if full_snapshot:
        for article_id, previous_files in remote_by_article.items():
            if article_id in current_by_id:
                continue
            for vector_file in previous_files:
                attributes = _metadata(vector_file)
                missing_runs = attributes.get("missing_runs", 0)
                missing_runs = missing_runs if isinstance(missing_runs, int) else 0
                if missing_runs >= 1:
                    _delete_vector_file(client, vector_store_id, vector_file.id)
                    files_removed += 1
                    deleted += 1
                else:
                    client.vector_stores.files.update(
                        vector_file.id,
                        vector_store_id=vector_store_id,
                        attributes={**attributes, "missing_runs": 1},
                    )
                    pending_deletion += 1

    return VectorSyncResult(
        added=added,
        updated=updated,
        skipped=skipped,
        deleted=deleted,
        pending_deletion=pending_deletion,
        files_removed=files_removed,
        files_embedded=added + updated,
        chunks_embedded=chunks_embedded,
    )
