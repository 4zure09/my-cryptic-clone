import logging
from types import SimpleNamespace

import pytest

from cryptic_clone.models import MarkdownDocument
from cryptic_clone.openai_sync import resolve_vector_store, sync_vector_store


@pytest.fixture(autouse=True)
def deterministic_tokenizer(monkeypatch):
    encoding = SimpleNamespace(encode=lambda text: text.split())
    monkeypatch.setattr(
        "cryptic_clone.openai_sync.tiktoken.get_encoding",
        lambda _name: encoding,
    )


class FakePage:
    def __init__(self, data) -> None:
        self.data = data

    def iter_pages(self):
        yield self


class FakeVectorFiles:
    def __init__(self) -> None:
        self.items = []
        self.deleted = []
        self.updated = []

    def list(self, vector_store_id, *, limit):
        del vector_store_id, limit
        return FakePage(list(self.items))

    def upload_and_poll(self, *, vector_store_id, file, attributes, chunking_strategy):
        del vector_store_id, file, chunking_strategy
        item = SimpleNamespace(
            id=f"file-{len(self.items) + 1}",
            status="completed",
            attributes=dict(attributes),
            created_at=len(self.items) + 1,
        )
        self.items.append(item)
        return item

    def update(self, file_id, *, vector_store_id, attributes):
        del vector_store_id
        item = next(item for item in self.items if item.id == file_id)
        item.attributes = dict(attributes)
        self.updated.append(file_id)
        return item

    def delete(self, file_id, *, vector_store_id):
        del vector_store_id
        self.items = [item for item in self.items if item.id != file_id]
        self.deleted.append(file_id)
        return SimpleNamespace(deleted=True)


class FakeVectorStores:
    def __init__(self) -> None:
        self.items = []
        self.files = FakeVectorFiles()

    def list(self, *, limit, order):
        del limit, order
        return FakePage(list(self.items))

    def retrieve(self, *, vector_store_id):
        item = next((item for item in self.items if item.id == vector_store_id), None)
        if item is None:
            raise RuntimeError("not found")
        return item

    def create(self, **payload):
        item = SimpleNamespace(
            id=f"vs_{len(self.items) + 1}",
            name=payload["name"],
            metadata=dict(payload["metadata"]),
        )
        self.items.append(item)
        return item


class FakeClient:
    def __init__(self) -> None:
        self.vector_stores = FakeVectorStores()


def make_document(tmp_path, article_id="1", content="# Article"):
    path = tmp_path / f"{article_id}.md"
    path.write_text(content, encoding="utf-8")
    return MarkdownDocument(
        article_id=article_id,
        article_url=f"https://support.optisigns.com/hc/en-us/articles/{article_id}",
        path=path,
        document_hash=f"hash-{content}",
    )


def test_store_is_created_once_then_found_from_remote_metadata() -> None:
    client = FakeClient()

    first = resolve_vector_store(client=client, name="test-store")
    second = resolve_vector_store(client=client, name="renamed-locally")

    assert first == ("vs_1", True)
    assert second == ("vs_1", False)
    assert len(client.vector_stores.items) == 1


def test_configured_store_id_is_retrieved_instead_of_creating() -> None:
    client = FakeClient()
    existing = client.vector_stores.create(
        name="existing",
        metadata={},
        description="existing store",
    )

    resolved = resolve_vector_store(
        client=client,
        name="ignored",
        configured_id=existing.id,
    )

    assert resolved == (existing.id, False)
    assert len(client.vector_stores.items) == 1


def test_missing_configured_store_fails_instead_of_creating() -> None:
    client = FakeClient()

    with pytest.raises(RuntimeError, match="could not be retrieved"):
        resolve_vector_store(
            client=client,
            name="must-not-be-created",
            configured_id="vs_missing",
        )

    assert client.vector_stores.items == []


def test_multiple_managed_stores_fail_instead_of_guessing() -> None:
    client = FakeClient()
    resolve_vector_store(client=client, name="one")
    client.vector_stores.items.append(
        SimpleNamespace(
            id="vs-duplicate",
            metadata={
                "managed_by": "cryptic-support-clone",
                "source": "support.optisigns.com",
            },
        )
    )

    with pytest.raises(RuntimeError, match="Multiple project-managed"):
        resolve_vector_store(client=client, name="two")


def test_upload_then_skip_uses_remote_file_attributes(tmp_path, caplog) -> None:
    client = FakeClient()
    document = make_document(tmp_path)

    with caplog.at_level(logging.INFO):
        first = sync_vector_store(
            client=client,
            vector_store_id="vs-1",
            documents=(document,),
            full_snapshot=False,
        )
    second = sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(document,),
        full_snapshot=False,
    )

    assert (first.added, first.files_embedded) == (1, 1)
    assert (second.skipped, second.files_embedded) == (1, 0)
    assert len(client.vector_stores.files.items) == 1
    assert "Uploaded article: 1.md | Files: 1 | Estimated Chunks: 1" in caplog.text
    assert "Strategy: 800 tokens/chunk, 120 overlap" in caplog.text


def test_matching_legacy_file_is_reused_and_marked_as_managed(tmp_path) -> None:
    client = FakeClient()
    document = make_document(tmp_path)
    legacy = SimpleNamespace(
        id="file-legacy",
        status="completed",
        created_at=1,
        attributes={
            "article_id": document.article_id,
            "source_url": document.article_url,
            "document_hash": document.document_hash,
        },
    )
    client.vector_stores.files.items.append(legacy)

    result = sync_vector_store(
        client=client,
        vector_store_id="vs-existing",
        documents=(document,),
        full_snapshot=False,
    )

    assert (result.added, result.skipped) == (0, 1)
    assert legacy.attributes["managed_by"] == "zendesk-optisigns"
    assert client.vector_stores.files.updated == ["file-legacy"]


def test_update_uploads_before_removing_old_file(tmp_path) -> None:
    client = FakeClient()
    original = make_document(tmp_path, content="# Original")
    sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(original,),
        full_snapshot=False,
    )
    changed = make_document(tmp_path, content="# Changed")

    result = sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(changed,),
        full_snapshot=False,
    )

    assert result.updated == 1
    assert result.files_removed == 1
    assert client.vector_stores.files.deleted == ["file-1"]
    assert client.vector_stores.files.items[0].attributes["document_hash"] == changed.document_hash


def test_delete_requires_two_complete_snapshots(tmp_path) -> None:
    client = FakeClient()
    first = make_document(tmp_path, article_id="1")
    second = make_document(tmp_path, article_id="2")
    sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(first, second),
        full_snapshot=True,
    )

    pending = sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(first,),
        full_snapshot=True,
    )
    deleted = sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(first,),
        full_snapshot=True,
    )

    assert (pending.pending_deletion, pending.deleted) == (1, 0)
    assert (deleted.pending_deletion, deleted.deleted) == (0, 1)
    assert len(client.vector_stores.files.items) == 1


def test_limited_snapshot_never_marks_unseen_files(tmp_path) -> None:
    client = FakeClient()
    first = make_document(tmp_path, article_id="1")
    second = make_document(tmp_path, article_id="2")
    sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(first, second),
        full_snapshot=True,
    )

    result = sync_vector_store(
        client=client,
        vector_store_id="vs-1",
        documents=(first,),
        full_snapshot=False,
    )

    assert result.pending_deletion == 0
    assert client.vector_stores.files.updated == []
