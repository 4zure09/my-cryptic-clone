import json

from cryptic_clone.models import Article
from cryptic_clone.pipeline import reconcile_articles


def make_article(article_id: int, body: str) -> Article:
    return Article(
        id=article_id,
        title=f"Article {article_id}",
        html_url=f"https://support.optisigns.com/hc/en-us/articles/{article_id}",
        body=f"<h2>Instructions</h2><p>{body}</p>",
        locale="en-us",
        updated_at="2026-01-01T00:00:00Z",
        labels=(),
    )


def test_manifest_classifies_added_updated_and_skipped(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    state_file = tmp_path / "state" / "articles.json"
    first = [make_article(1, "One"), make_article(2, "Two")]

    initial = reconcile_articles(
        articles=first,
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=False,
    )
    unchanged = reconcile_articles(
        articles=first,
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=False,
    )
    changed = reconcile_articles(
        articles=[make_article(1, "Changed"), make_article(2, "Two")],
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=False,
    )

    assert (initial.added, initial.updated, initial.skipped) == (2, 0, 0)
    assert (unchanged.added, unchanged.updated, unchanged.skipped) == (0, 0, 2)
    assert (changed.added, changed.updated, changed.skipped) == (0, 1, 1)


def test_only_full_snapshot_marks_missing_articles_deleted(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    state_file = tmp_path / "state" / "articles.json"
    both = [make_article(1, "One"), make_article(2, "Two")]
    reconcile_articles(
        articles=both,
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=True,
    )

    partial = reconcile_articles(
        articles=[both[0]],
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=False,
    )
    assert partial.deleted == 0
    assert len(list(output_dir.glob("*.md"))) == 2

    first_complete = reconcile_articles(
        articles=[both[0]],
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=True,
    )
    first_manifest = json.loads(state_file.read_text(encoding="utf-8"))

    assert first_complete.deleted == 0
    assert first_complete.pending_deletion == 1
    assert first_manifest["articles"]["2"]["deleted"] is False
    assert first_manifest["articles"]["2"]["missing_full_runs"] == 1
    assert len(list(output_dir.glob("*.md"))) == 2

    second_complete = reconcile_articles(
        articles=[both[0]],
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=True,
    )
    manifest = json.loads(state_file.read_text(encoding="utf-8"))

    assert second_complete.deleted == 1
    assert second_complete.pending_deletion == 0
    assert manifest["articles"]["2"]["deleted"] is True
    assert len(list(output_dir.glob("*.md"))) == 1


def test_article_returning_before_confirmation_is_not_deleted(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    state_file = tmp_path / "state" / "articles.json"
    both = [make_article(1, "One"), make_article(2, "Two")]
    reconcile_articles(
        articles=both,
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=True,
    )
    reconcile_articles(
        articles=[both[0]],
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=True,
    )

    recovered = reconcile_articles(
        articles=both,
        output_dir=output_dir,
        state_file=state_file,
        full_snapshot=True,
    )
    manifest = json.loads(state_file.read_text(encoding="utf-8"))

    assert recovered.deleted == 0
    assert recovered.pending_deletion == 0
    assert manifest["articles"]["2"]["deleted"] is False
    assert "missing_full_runs" not in manifest["articles"]["2"]
