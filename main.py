"""One-shot entry point for the OptiSigns support-content ingestion job."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from cryptic_clone.openai_sync import (
    attach_vector_store_to_assistant,
    resolve_vector_store,
    run_sanity_check,
    sync_vector_store,
)
from cryptic_clone.pipeline import scrape_articles

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 \"Article URL:\" lines per reply."""


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
        description="Download public Zendesk articles and normalize them to Markdown."
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
        default=Path(os.getenv("DATA_DIR", "data/articles")),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.getenv("STATE_FILE", "state/articles.json")),
        help="durable manifest used for delta detection",
    )
    parser.add_argument(
        "--upload",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("UPLOAD_ENABLED"),
        help="synchronize changed Markdown files to the configured OpenAI vector store",
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
    parser.add_argument(
        "--sanity-check",
        action="store_true",
        help='ask "How do I add a YouTube video?" after vector synchronization',
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        result = scrape_articles(
            base_url=args.base_url,
            locale=args.locale,
            output_dir=args.output_dir,
            state_file=args.state_file,
            limit=args.limit,
        )
    except Exception:
        logging.getLogger(__name__).exception("Article ingestion failed")
        return 1

    logging.getLogger(__name__).info(
        "Article ingestion complete: fetched=%d added=%d updated=%d skipped=%d "
        "pending_deletion=%d deleted=%d full_snapshot=%s output=%s",
        result.fetched,
        result.added,
        result.updated,
        result.skipped,
        result.pending_deletion,
        result.deleted,
        result.full_snapshot,
        args.output_dir,
    )

    if args.upload:
        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
        if not api_key:
            logging.getLogger(__name__).error(
                "Upload enabled but OPENAI_API_KEY (or API_KEY) is missing"
            )
            return 1
        from openai import OpenAI

        try:
            client = OpenAI(api_key=api_key)
            vector_store_id, vector_store_created = resolve_vector_store(
                client=client,
                state_file=args.state_file,
                configured_id=os.getenv("OPENAI_VECTOR_STORE_ID"),
                name=os.getenv("OPENAI_VECTOR_STORE_NAME", "opb-store"),
            )
            logging.getLogger(__name__).info(
                "Using OpenAI vector store: id=%s created=%s",
                vector_store_id,
                vector_store_created,
            )
            vector_result = sync_vector_store(
                client=client,
                vector_store_id=vector_store_id,
                output_dir=args.output_dir,
                state_file=args.state_file,
                chunk_max_tokens=args.chunk_max_tokens,
                chunk_overlap_tokens=args.chunk_overlap_tokens,
            )
            logging.getLogger(__name__).info(
                "Vector sync complete: added=%d updated=%d skipped=%d deleted=%d "
                "files_removed=%d files_embedded=%d chunks_embedded=%d vector_store=%s",
                vector_result.added,
                vector_result.updated,
                vector_result.skipped,
                vector_result.deleted,
                vector_result.files_removed,
                vector_result.files_embedded,
                vector_result.chunks_embedded,
                vector_store_id,
            )

            assistant_id = os.getenv("ASSISTANT_ID", "").strip()
            if assistant_id:
                attach_vector_store_to_assistant(
                    client=client,
                    assistant_id=assistant_id,
                    vector_store_id=vector_store_id,
                )
                logging.getLogger(__name__).info(
                    "Attached vector store %s to Assistant %s",
                    vector_store_id,
                    assistant_id,
                )
            else:
                logging.getLogger(__name__).info(
                    "ASSISTANT_ID not provided; vector upload completed without attachment"
                )

            if args.sanity_check:
                answer = run_sanity_check(
                    client=client,
                    vector_store_id=vector_store_id,
                    model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                    question="How do I add a YouTube video?",
                    instructions=SYSTEM_PROMPT,
                )
                logging.getLogger(__name__).info("Sanity-check answer:\n%s", answer)
        except Exception:
            logging.getLogger(__name__).exception("OpenAI upload/attachment/check failed")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
