"""Small, dependency-free client for the public Zendesk Help Center API."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from cryptic_clone.models import Article

LOGGER = logging.getLogger(__name__)


class InconsistentSnapshotError(RuntimeError):
    """Raised when page-number pagination did not produce a coherent snapshot."""


class ZendeskClient:
    """Fetch published Help Center articles with pagination and bounded retries."""

    def __init__(
        self,
        base_url: str,
        locale: str = "en-us",
        *,
        timeout_seconds: float = 30,
        retries: int = 3,
        snapshot_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.locale = locale
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.snapshot_retries = snapshot_retries

    def fetch_articles(self, limit: int = 0) -> list[Article]:
        """Return up to ``limit`` articles; zero means all published articles."""
        if limit:
            articles, _, _ = self._fetch_snapshot(limit=limit)
            return articles

        for attempt in range(self.snapshot_retries + 1):
            articles, expected_counts, duplicate_ids = self._fetch_snapshot(limit=0)
            snapshot_is_consistent = (
                len(expected_counts) == 1
                and not duplicate_ids
                and len(articles) == next(iter(expected_counts), -1)
            )
            if snapshot_is_consistent:
                return articles

            details = (
                f"counts={sorted(expected_counts)} unique={len(articles)} "
                f"duplicates={sorted(duplicate_ids)}"
            )
            if attempt >= self.snapshot_retries:
                raise InconsistentSnapshotError(
                    f"Zendesk pagination remained inconsistent after retries: {details}"
                )
            LOGGER.warning(
                "Inconsistent Zendesk snapshot (%s); retrying the full crawl (%d/%d)",
                details,
                attempt + 1,
                self.snapshot_retries,
            )
            time.sleep(2**attempt)

        raise AssertionError("snapshot retry loop exited unexpectedly")

    def _fetch_snapshot(self, limit: int) -> tuple[list[Article], set[int], set[int]]:
        query = urlencode({"per_page": 100, "sort_by": "updated_at", "sort_order": "desc"})
        next_url: str | None = (
            f"{self.base_url}/api/v2/help_center/{self.locale}/articles.json?{query}"
        )
        articles_by_id: dict[int, Article] = {}
        expected_counts: set[int] = set()
        duplicate_ids: set[int] = set()

        while next_url and (limit == 0 or len(articles_by_id) < limit):
            payload = self._get_json(self._canonicalize_url(next_url))
            page_articles = payload.get("articles", [])
            if not isinstance(page_articles, list):
                raise TypeError("Zendesk response field 'articles' is not a list")
            total_count = payload.get("count")
            if not isinstance(total_count, int):
                raise TypeError("Zendesk response field 'count' is not an integer")
            expected_counts.add(total_count)

            for raw_article in page_articles:
                if raw_article.get("draft"):
                    continue
                article = Article.from_api(raw_article)
                if article.id in articles_by_id:
                    duplicate_ids.add(article.id)
                else:
                    articles_by_id[article.id] = article
                if limit and len(articles_by_id) >= limit:
                    break

            next_page = payload.get("next_page")
            next_url = str(next_page) if next_page else None
            LOGGER.info("Fetched %d unique article records so far", len(articles_by_id))

        return list(articles_by_id.values()), expected_counts, duplicate_ids

    def _canonicalize_url(self, url: str) -> str:
        """Keep pagination on the configured public host."""
        parsed = urlparse(urljoin(f"{self.base_url}/", url))
        base = urlparse(self.base_url)
        return urlunparse((base.scheme, base.netloc, parsed.path, "", parsed.query, ""))

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "cryptic-support-clone/0.1 (+public take-home project)",
            },
        )

        for attempt in range(self.retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Zendesk returned HTTP {response.status} for {url}")
                    payload = json.load(response)
                if not isinstance(payload, dict):
                    raise TypeError("Zendesk response is not a JSON object")
                return payload
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt >= self.retries:
                    raise RuntimeError(f"Unable to fetch {url} after retries") from exc
                delay = 2**attempt
                LOGGER.warning(
                    "Fetch attempt %d failed for %s; retrying in %ds: %s",
                    attempt + 1,
                    url,
                    delay,
                    exc,
                )
                time.sleep(delay)

        raise AssertionError("retry loop exited unexpectedly")
