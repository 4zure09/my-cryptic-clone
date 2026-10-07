from cryptic_clone.models import Article
from cryptic_clone.pipeline import reconcile_articles


def make_article(article_id: int, body: str = "Body") -> Article:
    return Article(
        id=article_id,
        title=f"Article {article_id}",
        html_url=f"https://support.optisigns.com/hc/en-us/articles/{article_id}",
        body=f"<h2>Instructions</h2><p>{body}</p>",
        locale="en-us",
        updated_at="2026-01-01T00:00:00Z",
        labels=(),
    )


def test_normalization_writes_then_reuses_same_markdown(tmp_path) -> None:
    first = reconcile_articles(
        articles=[make_article(1), make_article(2)],
        output_dir=tmp_path,
        full_snapshot=False,
    )
    second = reconcile_articles(
        articles=[make_article(1), make_article(2)],
        output_dir=tmp_path,
        full_snapshot=False,
    )

    assert (first.files_written, first.files_reused) == (2, 0)
    assert (second.files_written, second.files_reused) == (0, 2)
    assert len(second.documents) == 2


def test_only_complete_snapshot_removes_stale_local_markdown(tmp_path) -> None:
    reconcile_articles(
        articles=[make_article(1), make_article(2)],
        output_dir=tmp_path,
        full_snapshot=True,
    )
    limited = reconcile_articles(
        articles=[make_article(1)],
        output_dir=tmp_path,
        full_snapshot=False,
    )
    complete = reconcile_articles(
        articles=[make_article(1)],
        output_dir=tmp_path,
        full_snapshot=True,
    )

    assert limited.local_files_removed == 0
    assert complete.local_files_removed == 1
    assert len(list(tmp_path.glob("*.md"))) == 1
