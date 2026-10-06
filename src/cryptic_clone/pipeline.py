"""Orchestration for scraping and normalizing public support articles."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cryptic_clone.manifest import load_manifest, save_manifest
from cryptic_clone.markdown import article_filename, normalize_article
from cryptic_clone.models import Article
from cryptic_clone.zendesk import ZendeskClient

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    fetched: int
    added: int
    updated: int
    skipped: int
    deleted: int
    pending_deletion: int
    full_snapshot: bool


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _remove_generated_article(output_dir: Path, filename: str) -> None:
    """Remove only a direct child Markdown file recorded by this pipeline."""
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError(f"Unsafe article filename in manifest: {filename!r}")
    candidate = output_dir / filename
    if candidate.is_file():
        candidate.unlink()


def scrape_articles(
    *,
    base_url: str,
    locale: str,
    output_dir: Path,
    state_file: Path,
    limit: int = 0,
) -> ScrapeResult:
    client = ZendeskClient(base_url=base_url, locale=locale)
    articles = client.fetch_articles(limit=limit)
    if not articles:
        raise RuntimeError("Zendesk returned no published articles")

    return reconcile_articles(
        articles=articles,
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=limit == 0,
    )


def reconcile_articles(
    *,
    articles: Sequence[Article],
    output_dir: Path,
    state_file: Path,
    full_snapshot: bool,
) -> ScrapeResult:
    """Write article files and update a manifest without false partial-run deletions."""
    if not articles:
        raise RuntimeError("Cannot reconcile an empty article collection")

    output_dir.mkdir(parents=True, exist_ok=True)
    filenames_by_id = {article.id: article_filename(article) for article in articles}
    manifest = load_manifest(state_file)
    manifest_articles = manifest["articles"]
    fetched_ids: set[str] = set()

    added = updated = skipped = 0
    for article in articles:
        article_id = str(article.id)
        fetched_ids.add(article_id)
        filename = filenames_by_id[article.id]
        destination = output_dir / filename
        markdown = normalize_article(article, filenames_by_id)
        document_hash = sha256(markdown.encode()).hexdigest()
        previous = manifest_articles.get(article_id)

        if not isinstance(previous, dict) or previous.get("deleted", False):
            added += 1
            status = "added"
        elif previous.get("document_hash") != document_hash:
            updated += 1
            status = "updated"
        else:
            skipped += 1
            status = "skipped"

        old_filename = previous.get("filename") if isinstance(previous, dict) else None
        if old_filename and old_filename != filename:
            _remove_generated_article(output_dir, old_filename)

        if not destination.exists() or destination.read_text(encoding="utf-8") != markdown:
            _write_text_atomic(destination, markdown)
        LOGGER.debug("%s article %d -> %s", status, article.id, destination)

        remote_state = previous if isinstance(previous, dict) else {}
        manifest_articles[article_id] = {
            "title": article.title,
            "article_url": article.html_url,
            "updated_at": article.updated_at,
            "filename": filename,
            "document_hash": document_hash,
            "deleted": False,
            "vector_file_id": remote_state.get("vector_file_id"),
            "uploaded_hash": remote_state.get("uploaded_hash"),
        }

    deleted = 0
    pending_deletion = 0
    if full_snapshot:
        for article_id, record in manifest_articles.items():
            if article_id in fetched_ids or not isinstance(record, dict):
                continue
            if not record.get("deleted", False):
                missing_full_runs = record.get("missing_full_runs", 0)
                if not isinstance(missing_full_runs, int) or missing_full_runs < 0:
                    missing_full_runs = 0
                missing_full_runs += 1
                record["missing_full_runs"] = missing_full_runs
                record.setdefault("missing_since", datetime.now(UTC).isoformat())

                if missing_full_runs >= 2:
                    deleted += 1
                    record["deleted"] = True
                    record["deleted_at"] = datetime.now(UTC).isoformat()
                    filename = record.get("filename")
                    if isinstance(filename, str):
                        _remove_generated_article(output_dir, filename)
                else:
                    pending_deletion += 1

    manifest["last_run_was_full_snapshot"] = full_snapshot
    save_manifest(state_file, manifest)

    return ScrapeResult(
        fetched=len(articles),
        added=added,
        updated=updated,
        skipped=skipped,
        deleted=deleted,
        pending_deletion=pending_deletion,
        full_snapshot=full_snapshot,
    )
