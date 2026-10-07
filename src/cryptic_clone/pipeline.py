"""Orchestration for scraping and normalizing public support articles."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from cryptic_clone.markdown import article_filename, normalize_article
from cryptic_clone.models import Article, MarkdownDocument
from cryptic_clone.zendesk import ZendeskClient


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    fetched: int
    files_written: int
    files_reused: int
    local_files_removed: int
    full_snapshot: bool
    documents: tuple[MarkdownDocument, ...]


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


def scrape_articles(
    *,
    base_url: str,
    locale: str,
    output_dir: Path,
    limit: int = 0,
) -> ScrapeResult:
    client = ZendeskClient(base_url=base_url, locale=locale)
    articles = client.fetch_articles(limit=limit)
    if not articles:
        raise RuntimeError("Zendesk returned no published articles")
    return reconcile_articles(
        articles=articles,
        output_dir=output_dir,
        full_snapshot=limit == 0,
    )


def reconcile_articles(
    *,
    articles: Sequence[Article],
    output_dir: Path,
    full_snapshot: bool,
) -> ScrapeResult:
    """Normalize the current Zendesk snapshot into disposable Markdown files."""
    if not articles:
        raise RuntimeError("Cannot reconcile an empty article collection")

    output_dir.mkdir(parents=True, exist_ok=True)
    filenames_by_id = {article.id: article_filename(article) for article in articles}
    current_filenames = set(filenames_by_id.values())
    documents: list[MarkdownDocument] = []
    files_written = files_reused = 0

    for article in articles:
        destination = output_dir / filenames_by_id[article.id]
        markdown = normalize_article(article, filenames_by_id)
        document_hash = sha256(markdown.encode()).hexdigest()
        if not destination.exists() or destination.read_text(encoding="utf-8") != markdown:
            _write_text_atomic(destination, markdown)
            files_written += 1
        else:
            files_reused += 1
        documents.append(
            MarkdownDocument(
                article_id=str(article.id),
                article_url=article.html_url,
                path=destination,
                document_hash=document_hash,
            )
        )

    local_files_removed = 0
    if full_snapshot:
        for old_file in output_dir.glob("*.md"):
            if old_file.name not in current_filenames:
                old_file.unlink()
                local_files_removed += 1

    return ScrapeResult(
        fetched=len(articles),
        files_written=files_written,
        files_reused=files_reused,
        local_files_removed=local_files_removed,
        full_snapshot=full_snapshot,
        documents=tuple(documents),
    )
