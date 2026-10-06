import json
from types import SimpleNamespace

from cryptic_clone.openai_sync import (
    attach_vector_store_to_assistant,
    resolve_vector_store,
    sync_vector_store,
)


class FakePage:
    def __init__(self, chunk_count: int) -> None:
        self.data = [object() for _ in range(chunk_count)]

    def iter_pages(self):
        yield self


class FakeVectorFiles:
    def __init__(self) -> None:
        self.uploaded: list[str] = []
        self.deleted: list[str] = []
        self.chunks: dict[str, int] = {}

    def upload_and_poll(self, *, vector_store_id, file, attributes, chunking_strategy):
        del vector_store_id, attributes, chunking_strategy
        file_id = f"file-{len(self.uploaded) + 1}"
        self.uploaded.append(file.name)
        self.chunks[file_id] = 3
        return SimpleNamespace(id=file_id, status="completed", usage_bytes=123)

    def content(self, file_id, *, vector_store_id):
        del vector_store_id
        return FakePage(self.chunks[file_id])

    def delete(self, file_id, *, vector_store_id):
        del vector_store_id
        self.deleted.append(file_id)
        return SimpleNamespace(deleted=True)


class FakeClient:
    def __init__(self) -> None:
        self.vector_files = FakeVectorFiles()
        self.vector_stores = SimpleNamespace(files=self.vector_files)


class Dumpable:
    def __init__(self, value) -> None:
        self.value = value

    def model_dump(self, *, exclude_none):
        del exclude_none
        return self.value


class FakeOpenAIClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.created_stores: list[dict] = []
        self.assistant_updates: list[tuple[str, dict]] = []
        self.vector_stores.create = self.create_vector_store
        self.beta = SimpleNamespace(
            assistants=SimpleNamespace(
                retrieve=self.retrieve_assistant,
                update=self.update_assistant,
            )
        )

    def create_vector_store(self, **payload):
        self.created_stores.append(payload)
        return SimpleNamespace(id="vs_created")

    def retrieve_assistant(self, assistant_id):
        del assistant_id
        return SimpleNamespace(
            tools=[
                Dumpable(
                    {
                        "type": "function",
                        "function": {
                            "name": "existing_tool",
                            "description": "kept",
                            "parameters": {"type": "object"},
                        },
                    }
                )
            ],
            tool_resources=Dumpable({"code_interpreter": {"file_ids": ["file-existing"]}}),
        )

    def update_assistant(self, assistant_id, **payload):
        self.assistant_updates.append((assistant_id, payload))
        return SimpleNamespace(id=assistant_id)


def write_manifest(state_file, article_path, document_hash="hash-1", **overrides):
    record = {
        "title": "Article 1",
        "article_url": "https://support.optisigns.com/hc/en-us/articles/1",
        "updated_at": "2026-01-01T00:00:00Z",
        "filename": article_path.name,
        "document_hash": document_hash,
        "deleted": False,
        "vector_file_id": None,
        "uploaded_hash": None,
    }
    record.update(overrides)
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "last_successful_run": None,
                "last_run_was_full_snapshot": False,
                "articles": {"1": record},
            }
        ),
        encoding="utf-8",
    )


def test_uploads_new_file_then_skips_unchanged_file(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    output_dir.mkdir()
    article_path = output_dir / "1-article.md"
    article_path.write_text("# Article", encoding="utf-8")
    state_file = tmp_path / "state" / "articles.json"
    write_manifest(state_file, article_path)
    client = FakeClient()

    first = sync_vector_store(
        client=client,
        vector_store_id="vs-test",
        output_dir=output_dir,
        state_file=state_file,
    )
    second = sync_vector_store(
        client=client,
        vector_store_id="vs-test",
        output_dir=output_dir,
        state_file=state_file,
    )

    assert (first.added, first.files_embedded, first.chunks_embedded) == (1, 1, 1)
    assert (second.skipped, second.files_embedded) == (1, 0)
    assert len(client.vector_files.uploaded) == 1


def test_update_uploads_first_then_removes_superseded_file(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    output_dir.mkdir()
    article_path = output_dir / "1-article.md"
    article_path.write_text("# Changed", encoding="utf-8")
    state_file = tmp_path / "state" / "articles.json"
    write_manifest(
        state_file,
        article_path,
        document_hash="new-hash",
        vector_file_id="file-old",
        uploaded_hash="old-hash",
        vector_chunk_count=2,
    )
    client = FakeClient()

    result = sync_vector_store(
        client=client,
        vector_store_id="vs-test",
        output_dir=output_dir,
        state_file=state_file,
    )

    manifest = json.loads(state_file.read_text(encoding="utf-8"))
    assert result.updated == 1
    assert result.files_removed == 1
    assert client.vector_files.deleted == ["file-old"]
    assert manifest["articles"]["1"]["vector_file_id"] == "file-1"
    assert manifest["articles"]["1"]["uploaded_hash"] == "new-hash"


def test_deleted_article_is_detached_without_reupload(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    output_dir.mkdir()
    article_path = output_dir / "1-article.md"
    state_file = tmp_path / "state" / "articles.json"
    write_manifest(
        state_file,
        article_path,
        deleted=True,
        vector_file_id="file-old",
        uploaded_hash="hash-1",
    )
    client = FakeClient()

    result = sync_vector_store(
        client=client,
        vector_store_id="vs-test",
        output_dir=output_dir,
        state_file=state_file,
    )

    assert result.deleted == 1
    assert result.files_removed == 1
    assert result.files_embedded == 0
    assert client.vector_files.deleted == ["file-old"]


def test_missing_active_markdown_aborts_before_any_api_call(tmp_path) -> None:
    output_dir = tmp_path / "articles"
    output_dir.mkdir()
    article_path = output_dir / "1-article.md"
    state_file = tmp_path / "state" / "articles.json"
    write_manifest(state_file, article_path)
    client = FakeClient()

    try:
        sync_vector_store(
            client=client,
            vector_store_id="vs-test",
            output_dir=output_dir,
            state_file=state_file,
        )
    except FileNotFoundError as exc:
        assert "aborted before upload" in str(exc)
    else:
        raise AssertionError("missing Markdown should abort vector sync")

    assert client.vector_files.uploaded == []


def test_resolve_vector_store_creates_once_and_reuses_manifest(tmp_path) -> None:
    state_file = tmp_path / "state" / "articles.json"
    client = FakeOpenAIClient()

    first_id, first_created = resolve_vector_store(
        client=client,
        state_file=state_file,
        configured_id=None,
    )
    second_id, second_created = resolve_vector_store(
        client=client,
        state_file=state_file,
        configured_id=None,
    )

    assert (first_id, first_created) == ("vs_created", True)
    assert (second_id, second_created) == ("vs_created", False)
    assert len(client.created_stores) == 1


def test_attaches_store_without_removing_existing_assistant_tools() -> None:
    client = FakeOpenAIClient()

    attach_vector_store_to_assistant(
        client=client,
        assistant_id="asst_test",
        vector_store_id="vs_test",
    )

    assistant_id, payload = client.assistant_updates[0]
    assert assistant_id == "asst_test"
    assert [tool["type"] for tool in payload["tools"]] == ["function", "file_search"]
    assert payload["tool_resources"]["code_interpreter"] == {
        "file_ids": ["file-existing"]
    }
    assert payload["tool_resources"]["file_search"] == {
        "vector_store_ids": ["vs_test"]
    }


def test_rejects_agent_id_as_legacy_assistant_id() -> None:
    client = FakeOpenAIClient()

    try:
        attach_vector_store_to_assistant(
            client=client,
            assistant_id="agent_test",
            vector_store_id="vs_test",
        )
    except ValueError as exc:
        assert "agent_" in str(exc)
        assert "asst_" in str(exc)
    else:
        raise AssertionError("agent_ ID should not be accepted as ASSISTANT_ID")
