from cryptic_clone.markdown import article_filename, normalize_article
from cryptic_clone.models import Article


def make_article(article_id: int, title: str, body: str) -> Article:
    return Article(
        id=article_id,
        title=title,
        html_url=f"https://support.optisigns.com/hc/en-us/articles/{article_id}-sample",
        body=body,
        locale="en-us",
        updated_at="2026-01-01T00:00:00Z",
        labels=("sample",),
    )


def test_normalizer_preserves_structure_and_rewrites_article_links() -> None:
    article = make_article(
        101,
        "Set Up a Screen",
        """
        <script>alert('remove me')</script>
        <h2 style="color:red">Install</h2>
        <p>Read <a href="https://support.optisigns.com/hc/en-us/articles/202-Next-Step">next</a>.</p>
        <pre><code>print(&quot;hello&quot;)</code></pre>
        <ul><li>First</li><li>Second</li></ul>
        """,
    )
    linked = make_article(202, "Next Step", "<p>Continue.</p>")
    filenames = {101: article_filename(article), 202: article_filename(linked)}

    result = normalize_article(article, filenames)

    assert "## Install" in result
    assert "[next](202-next-step.md)" in result
    assert 'print("hello")' in result
    assert "- First" in result
    assert "alert" not in result
    assert (
        "Article URL: https://support.optisigns.com/hc/en-us/articles/101-sample\n\n"
        in result
    )


def test_normalizer_keeps_external_links_and_makes_images_absolute() -> None:
    article = make_article(
        303,
        "Media",
        '<p><a href="https://example.com/help">External</a></p>'
        '<p><img src="/hc/article_attachments/99" alt="Example image"></p>',
    )

    result = normalize_article(article, {303: article_filename(article)})

    assert "[External](https://example.com/help)" in result
    assert "![Example image](https://support.optisigns.com/hc/article_attachments/99)" in result
