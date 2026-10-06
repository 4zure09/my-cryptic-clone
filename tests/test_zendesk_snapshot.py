import pytest

from cryptic_clone.zendesk import InconsistentSnapshotError, ZendeskClient


def raw_article(article_id: int) -> dict[str, object]:
    return {
        "id": article_id,
        "title": f"Article {article_id}",
        "html_url": f"https://support.optisigns.com/hc/en-us/articles/{article_id}",
        "body": "<p>Body</p>",
        "locale": "en-us",
        "updated_at": "2026-01-01T00:00:00Z",
        "label_names": [],
        "draft": False,
    }


def test_full_snapshot_accepts_same_count_with_one_added_and_one_removed(monkeypatch) -> None:
    client = ZendeskClient("https://support.optisigns.com", snapshot_retries=0)
    responses = iter(
        [
            {
                "count": 2,
                "next_page": "https://support.optisigns.com/page-2",
                "articles": [raw_article(1)],
            },
            {"count": 2, "next_page": None, "articles": [raw_article(3)]},
        ]
    )
    monkeypatch.setattr(client, "_get_json", lambda _url: next(responses))

    articles = client.fetch_articles(limit=0)

    assert {article.id for article in articles} == {1, 3}


def test_full_snapshot_rejects_duplicate_ids_caused_by_page_shift(monkeypatch) -> None:
    client = ZendeskClient("https://support.optisigns.com", snapshot_retries=0)
    responses = iter(
        [
            {
                "count": 2,
                "next_page": "https://support.optisigns.com/page-2",
                "articles": [raw_article(1)],
            },
            {"count": 2, "next_page": None, "articles": [raw_article(1)]},
        ]
    )
    monkeypatch.setattr(client, "_get_json", lambda _url: next(responses))

    with pytest.raises(InconsistentSnapshotError):
        client.fetch_articles(limit=0)


def test_full_snapshot_rejects_count_changing_between_pages(monkeypatch) -> None:
    client = ZendeskClient("https://support.optisigns.com", snapshot_retries=0)
    responses = iter(
        [
            {
                "count": 2,
                "next_page": "https://support.optisigns.com/page-2",
                "articles": [raw_article(1)],
            },
            {"count": 3, "next_page": None, "articles": [raw_article(2)]},
        ]
    )
    monkeypatch.setattr(client, "_get_json", lambda _url: next(responses))

    with pytest.raises(InconsistentSnapshotError):
        client.fetch_articles(limit=0)


def test_full_snapshot_retries_entire_crawl_after_page_shift(monkeypatch) -> None:
    client = ZendeskClient("https://support.optisigns.com", snapshot_retries=1)
    responses = iter(
        [
            {
                "count": 2,
                "next_page": "https://support.optisigns.com/page-2",
                "articles": [raw_article(1)],
            },
            {"count": 2, "next_page": None, "articles": [raw_article(1)]},
            {
                "count": 2,
                "next_page": "https://support.optisigns.com/page-2",
                "articles": [raw_article(1)],
            },
            {"count": 2, "next_page": None, "articles": [raw_article(2)]},
        ]
    )
    monkeypatch.setattr(client, "_get_json", lambda _url: next(responses))
    monkeypatch.setattr("cryptic_clone.zendesk.time.sleep", lambda _seconds: None)

    articles = client.fetch_articles(limit=0)

    assert {article.id for article in articles} == {1, 2}
