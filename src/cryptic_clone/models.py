"""Shared data models for the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Article:
    id: int
    title: str
    html_url: str
    body: str
    locale: str
    updated_at: str
    labels: tuple[str, ...]

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Article:
        return cls(
            id=int(payload["id"]),
            title=str(payload.get("title") or payload.get("name") or "Untitled"),
            html_url=str(payload["html_url"]),
            body=str(payload.get("body") or ""),
            locale=str(payload.get("locale") or "en-us"),
            updated_at=str(payload.get("updated_at") or ""),
            labels=tuple(str(label) for label in payload.get("label_names", [])),
        )


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    article_id: str
    article_url: str
    path: Path
    document_hash: str
