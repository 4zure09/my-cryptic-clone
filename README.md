# Cryptic support assistant clone

## Setup

Requires Python 3.12+ and an OpenAI API key.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.sample .env
```

Set `OPENAI_API_KEY` in `.env`. `OPENAI_VECTOR_STORE_ID` is optional: when it is blank, the first upload creates a store and saves its ID in the durable `STATE_FILE`; later runs reuse it. `ASSISTANT_ID` is also optional and must belong to the same OpenAI project as the API key.

> *To run this script, please provide your OpenAI API Key. If you have already created an Assistant on the Playground, provide the `ASSISTANT_ID` as well via environment variables. If `ASSISTANT_ID` is not provided, the script will only upload files to a new Vector Store.*

Important for the current OpenAI API: a legacy Playground Assistant ID starts with `asst_`; the supplied `agent_...` ID is a newer Agents API resource and is not interchangeable. OpenAI retired the legacy Assistants API on 26 August 2026, so the supported runtime sanity check uses Responses API `file_search` directly with the vector-store ID. The optional `ASSISTANT_ID` attachment remains for compatibility with grading environments where the legacy endpoint is available.

For the daily GitHub job, add repository secret `OPENAI_API_KEY`; optionally add repository variables `OPENAI_VECTOR_STORE_ID` and `ASSISTANT_ID`. The workflow restores `data/` and `state/` from its latest cache, runs the complete Zendesk snapshot, saves the new state, and publishes `last-run.log` as an artifact.

Files use static chunks of 800 tokens with a 120-token overlap. This keeps most support procedures together while retaining context across boundaries. The logged chunk count is a local lexical estimate with the same boundaries because OpenAI's file API does not expose a chunk total. Every Markdown file contains an `Article URL:` line so retrieved answers can cite the source.

## How to run locally

```powershell
# Scrape/normalize the 40-article test corpus; no OpenAI calls
.\.venv\Scripts\python.exe main.py --limit 40 --no-upload

# Scrape 40 articles, upload only added/updated files, and reuse unchanged files
.\.venv\Scripts\python.exe main.py --limit 40 --upload

# Also ask the required sample question through Responses file_search
.\.venv\Scripts\python.exe main.py --limit 40 --upload --sanity-check

# Daily production run: reconcile every paginated Zendesk article
.\.venv\Scripts\python.exe main.py --limit 0 --upload

# Verification
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
```

The manifest compares stable Zendesk article IDs and SHA-256 hashes, so it detects additions and same-ID content changes even when the total pagination count is unchanged. Unchanged records are skipped. Deletion is considered only after two consecutive complete (`--limit 0`) snapshots; limited test runs never delete unseen records. Keep `DATA_DIR` and `STATE_FILE` on persistent storage for daily jobs. Each run logs `added`, `updated`, `skipped`, deleted files, uploaded files, and embedded chunks.

```powershell
docker build -t cryptic-clone .
docker run --rm `
  -e API_KEY=$env:OPENAI_API_KEY `
  -e OPENAI_VECTOR_STORE_ID=$env:OPENAI_VECTOR_STORE_ID `
  -e ARTICLE_LIMIT=0 -e UPLOAD_ENABLED=true `
  -v "${PWD}/data:/app/data" -v "${PWD}/state:/app/state" `
  cryptic-clone main.py
```

## Link to daily job logs

[GitHub Actions runs](https://github.com/4zure09/my-cryptic-clone/actions) — configure the repository secret above, push this repository, and trigger **Daily support sync** once with `article_limit=40` before leaving the UTC cron to run the complete daily snapshot.

## Screenshot of assistant answering a sample question

Run the sanity check above, then ask **“How do I add a YouTube video?”** in the OpenAI Playground with the same vector store. Save the cited-answer screenshot at `artifacts/screenshots/assistant-youtube-answer.png` and enable the image below.

<!-- ![Assistant answering with cited article URLs](artifacts/screenshots/assistant-youtube-answer.png) -->
