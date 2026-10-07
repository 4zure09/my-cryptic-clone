"""One-shot Zendesk-to-OpenAI ingestion job."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from cryptic_clone.openai_sync import resolve_vector_store, sync_vector_store
from cryptic_clone.pipeline import scrape_articles


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull Zendesk articles, normalize Markdown, and sync an OpenAI vector store."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("SOURCE_BASE_URL", "https://support.optisigns.com"),
    )
    parser.add_argument("--locale", default=os.getenv("SOURCE_LOCALE", "en-us"))
    parser.add_argument(
        "--limit",
        type=_non_negative_int,
        default=_non_negative_int(os.getenv("ARTICLE_LIMIT", "40")),
        help="maximum articles to download; 0 downloads every published article",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("DATA_DIR", ".runtime/articles")),
    )
    parser.add_argument(
        "--upload",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("UPLOAD_ENABLED"),
        help="synchronize the generated Markdown files to OpenAI",
    )
    parser.add_argument(
        "--chunk-max-tokens",
        type=int,
        default=int(os.getenv("CHUNK_MAX_TOKENS", "800")),
    )
    parser.add_argument(
        "--chunk-overlap-tokens",
        type=int,
        default=int(os.getenv("CHUNK_OVERLAP_TOKENS", "120")),
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    try:
        result = scrape_articles(
            base_url=args.base_url,
            locale=args.locale,
            output_dir=args.output_dir,
            limit=args.limit,
        )
    except Exception:
        logger.exception("Article ingestion failed")
        return 1

    logger.info(
        "Article ingestion complete: fetched=%d files_written=%d files_reused=%d "
        "local_files_removed=%d full_snapshot=%s output=%s",
        result.fetched,
        result.files_written,
        result.files_reused,
        result.local_files_removed,
        result.full_snapshot,
        args.output_dir,
    )

    if not args.upload:
        return 0

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        logger.error("Upload enabled but OPENAI_API_KEY (or API_KEY) is missing")
        return 1

    from openai import OpenAI

    try:
        client = OpenAI(api_key=api_key)
        vector_store_id, vector_store_created = resolve_vector_store(
            client=client,
            name=os.getenv("OPENAI_VECTOR_STORE_NAME", "cryptic-optibot-kb"),
            configured_id=os.getenv("OPENAI_VECTOR_STORE_ID"),
        )
        logger.info(
            "Using OpenAI vector store: id=%s created=%s",
            vector_store_id,
            vector_store_created,
        )
        vector_result = sync_vector_store(
            client=client,
            vector_store_id=vector_store_id,
            documents=result.documents,
            full_snapshot=result.full_snapshot,
            chunk_max_tokens=args.chunk_max_tokens,
            chunk_overlap_tokens=args.chunk_overlap_tokens,
        )
        logger.info(
            "Vector sync complete: added=%d updated=%d skipped=%d "
            "pending_deletion=%d deleted=%d files_removed=%d files_embedded=%d "
            "chunks_embedded=%d vector_store=%s",
            vector_result.added,
            vector_result.updated,
            vector_result.skipped,
            vector_result.pending_deletion,
            vector_result.deleted,
            vector_result.files_removed,
            vector_result.files_embedded,
            vector_result.chunks_embedded,
            vector_store_id,
        )
    except Exception:
        logger.exception("OpenAI vector-store sync failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
