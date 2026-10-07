# Cryptic support assistant clone

## Setup

Requires Python 3.12+ and an OpenAI API key.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.sample .env
```

Set only `OPENAI_API_KEY` in `.env`. No vector-store ID or Assistant ID is required. On the first upload, the code creates `cryptic-optibot-kb` with project metadata; later runs find that same store through the OpenAI API. Markdown is generated under the ignored `.runtime/` directory and is not repository state.

For GitHub Actions, add the repository secret `OPENAI_API_KEY`. The daily workflow needs no cache and no repository variables. Existing untagged stores such as an older `opb-store` are intentionally ignored; they can be deleted manually after confirming the new managed store works.

Files use static chunks of 800 tokens with a 120-token overlap. Every uploaded file records `article_id`, SHA-256 `document_hash`, source URL, and deletion state as OpenAI file attributes. Each successful upload logs its filename and an overlap-aware chunk estimate calculated locally with `tiktoken` (`cl100k_base`), because the upload response does not expose a chunk total.

## How to run locally

```powershell
# Pull and normalize 40 articles without calling OpenAI
.\.venv\Scripts\python.exe main.py --limit 40 --no-upload

# Pull 40 articles and create/find/sync the managed vector store
.\.venv\Scripts\python.exe main.py --limit 40 --upload

# Production-equivalent run: paginate and sync every published article
.\.venv\Scripts\python.exe main.py --limit 0 --upload

# Verification
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
```

The remote file attributes are the delta manifest: same `article_id` plus same hash is skipped; same ID plus a new hash is replaced; a new ID is added. Only complete `--limit 0` runs check deletions, and a missing article must be absent from two consecutive complete runs before its remote file is removed. This also handles the “one deleted, one added, total page count unchanged” case.

```powershell
docker build -t cryptic-clone .
docker run --rm -e API_KEY=$env:OPENAI_API_KEY -e ARTICLE_LIMIT=0 -e UPLOAD_ENABLED=true cryptic-clone main.py
```

## Link to daily job logs

[GitHub Actions runs](https://github.com/4zure09/my-cryptic-clone/actions) — add the `OPENAI_API_KEY` secret, push, then run **Daily support sync** manually once. The scheduled run is daily at 02:17 in `Asia/Ho_Chi_Minh`; every run publishes `last-run.log` as a downloadable artifact.

## Screenshot of assistant answering a sample question

In the OpenAI UI, attach the managed `cryptic-optibot-kb` store to the bot, ask **“How do I add a YouTube video?”**, and save the cited-answer screenshot as `artifacts/screenshots/assistant-youtube-answer.png`.

<!-- ![Assistant answering with cited article URLs](artifacts/screenshots/assistant-youtube-answer.png) -->
