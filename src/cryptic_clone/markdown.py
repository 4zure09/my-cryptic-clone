"""Normalize Zendesk article HTML into deterministic, retrieval-friendly Markdown."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from cryptic_clone.models import Article

ARTICLE_PATH_RE = re.compile(r"/hc/[A-Za-z-]+/articles/(\d+)(?:-[^/?#]*)?")
MULTIPLE_BLANK_LINES_RE = re.compile(r"\n{3,}")
TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 90) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = NON_SLUG_RE.sub("-", ascii_value.lower()).strip("-")
    return slug[:max_length].rstrip("-") or "article"


def article_filename(article: Article) -> str:
    return f"{article.id}-{slugify(article.title)}.md"


def _rewrite_article_href(
    href: str,
    current_article: Article,
    filenames_by_id: Mapping[int, str],
) -> str:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return href

    absolute = urljoin(current_article.html_url, href)
    parsed = urlparse(absolute)
    current_host = urlparse(current_article.html_url).netloc.lower()
    known_hosts = {current_host, "support.optisigns.com", "optisignshelp.zendesk.com"}
    if parsed.netloc.lower() not in known_hosts:
        return href

    match = ARTICLE_PATH_RE.search(parsed.path)
    if not match:
        return href

    article_id = int(match.group(1))
    local_filename = filenames_by_id.get(article_id)
    if not local_filename:
        return href

    if article_id == current_article.id and parsed.fragment:
        return f"#{parsed.fragment}"
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{local_filename}{fragment}"


def _clean_html(
    article: Article,
    filenames_by_id: Mapping[int, str],
) -> BeautifulSoup:
    soup = BeautifulSoup(article.body, "html.parser")

    for unwanted in soup.select("script, style, nav, aside, form, button, noscript"):
        unwanted.decompose()

    for element in soup.find_all(True):
        if not isinstance(element, Tag):
            continue
        for attribute in list(element.attrs):
            if attribute.lower().startswith("on") or attribute in {
                "style",
                "class",
                "data-list-item-id",
            }:
                del element.attrs[attribute]

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if isinstance(href, str):
            anchor["href"] = _rewrite_article_href(href, article, filenames_by_id)
        if not anchor.get_text(strip=True) and not anchor.find("img"):
            anchor.unwrap()

    for image in soup.find_all("img"):
        src = image.get("src")
        if isinstance(src, str):
            image["src"] = urljoin(article.html_url, src)
        if not image.get("alt"):
            image["alt"] = ""

    for empty_paragraph in soup.find_all("p"):
        if not empty_paragraph.get_text(strip=True) and not empty_paragraph.find(("img", "video")):
            empty_paragraph.decompose()

    return soup


def _clean_markdown(value: str) -> str:
    value = value.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    value = TRAILING_SPACE_RE.sub("", value)
    value = MULTIPLE_BLANK_LINES_RE.sub("\n\n", value)
    return value.strip() + "\n"


def normalize_article(article: Article, filenames_by_id: Mapping[int, str]) -> str:
    """Return a complete Markdown document with source metadata and a stable hash."""
    soup = _clean_html(article, filenames_by_id)
    body = markdownify(
        str(soup),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    )
    body = _clean_markdown(body)
    content_hash = hashlib.sha256(
        f"{article.title}\n{article.html_url}\n{body}".encode()
    ).hexdigest()

    metadata = [
        "---",
        f"article_id: {article.id}",
        f"title: {json.dumps(article.title, ensure_ascii=False)}",
        f"article_url: {json.dumps(article.html_url)}",
        f"locale: {json.dumps(article.locale)}",
        f"updated_at: {json.dumps(article.updated_at)}",
        f"content_hash: {json.dumps(content_hash)}",
        f"labels: {json.dumps(list(article.labels), ensure_ascii=False)}",
        "---",
        "",
        f"# {article.title}",
        "",
        f"Article URL: {article.html_url}",
        "",
    ]
    return "\n".join(metadata) + "\n" + body

