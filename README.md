# Cryptic support assistant clone

## Setup

Requires Python 3.12+, an OpenAI API key, and one OpenAI Vector Store shared by local and scheduled runs.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.sample .env
```

Set `OPENAI_API_KEY` and `OPENAI_VECTOR_STORE_ID` in `.env`. The code retrieves the configured store before uploading, so a missing ID or an ID from another OpenAI project fails instead of creating a duplicate. If the ID is intentionally blank during first-time local setup, the code finds the project-managed store or creates `cryptic-optibot-kb`.

For GitHub Actions, add repository secret `OPENAI_API_KEY` and repository variable `OPENAI_VECTOR_STORE_ID`, using the same ID as local. The workflow refuses to run when the variable is missing. Markdown is temporary under ignored `.runtime/`; no local state/cache is required.

Files use static chunks of 800 tokens with a 120-token overlap. Each successful upload logs its filename and an overlap-aware chunk estimate calculated locally with `tiktoken` (`cl100k_base`). Uploaded file attributes hold `article_id`, SHA-256 `document_hash`, source URL, and deletion state.

## How to run locally

```powershell
# Confirm these two values exist in .env, then sync a 40-article test batch
.\.venv\Scripts\python.exe main.py --limit 40 --upload

# Scrape/normalize only, without OpenAI
.\.venv\Scripts\python.exe main.py --limit 40 --no-upload

# Production-equivalent full sync
.\.venv\Scripts\python.exe main.py --limit 0 --upload

# Verification
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
```

The remote file attributes are the delta manifest: same ID plus same hash is skipped, same ID plus new hash is replaced, and a new ID is added. Legacy files from this project are adopted without duplicate uploads. Only complete `--limit 0` runs check deletions; an article must be absent from two consecutive complete runs before removal.

```powershell
docker build -t cryptic-clone .
docker run --rm -e API_KEY=$env:OPENAI_API_KEY -e OPENAI_VECTOR_STORE_ID=$env:OPENAI_VECTOR_STORE_ID -e ARTICLE_LIMIT=0 -e UPLOAD_ENABLED=true cryptic-clone main.py
```

## Link to daily job logs

[GitHub Actions runs](https://github.com/4zure09/my-cryptic-clone/actions) — add the `OPENAI_API_KEY` secret and `OPENAI_VECTOR_STORE_ID` variable, then run **Daily support sync** manually once. It runs daily at 02:17 in `Asia/Ho_Chi_Minh` and publishes `last-run.log` as an artifact.

## Screenshot of assistant answering a sample question

In the OpenAI UI, attach the configured Vector Store to the bot, ask **“How do I add a YouTube video?”**, and save the cited-answer screenshot as `artifacts/screenshots/assistant-youtube-answer.png`.

![Assistant answering with cited article URLs](artifacts/screenshots/assistant-youtube-answer.png)
