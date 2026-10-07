# Cryptic support assistant clone

## Setup

Requirements: Git and Docker.

```powershell
git clone https://github.com/4zure09/my-cryptic-clone.git
cd my-cryptic-clone
Copy-Item .env.sample .env
```

Set these values in `.env` without quotes or spaces around the values:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_VECTOR_STORE_ID=your_vector_store_id
```

The store ID is validated through the OpenAI API before upload, so an invalid ID fails instead of creating a duplicate. Docker provides the remaining defaults: 40 articles, static chunks of 800 tokens with 120-token overlap, and temporary Markdown under `.runtime/articles`. Chunk estimates are logged with `tiktoken`.

For the daily job, add `OPENAI_API_KEY` as a GitHub Actions repository secret and `OPENAI_VECTOR_STORE_ID` as a repository variable.

## How to run locally

```powershell
docker build -t cryptic-clone .

# Test: scrape, normalize, and sync 40 articles
docker run --rm --env-file .env -e UPLOAD_ENABLED=true -v cryptic-articles:/app/.runtime/articles cryptic-clone main.py

# Daily-production equivalent: sync every published Zendesk article
docker run --rm --env-file .env -e UPLOAD_ENABLED=true -e ARTICLE_LIMIT=0 -v cryptic-articles:/app/.runtime/articles cryptic-clone main.py
```

Each run compares the Zendesk article ID and content hash stored in OpenAI file attributes. It uploads only added or updated files, skips unchanged files, and removes an article only after it is missing from two consecutive complete runs. The Docker volume preserves local Markdown; OpenAI attributes remain the source of truth for upload delta. Logs include `added`, `updated`, `skipped`, `deleted`, uploaded files, and estimated chunks.

Optional local verification:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check .
```

## Link to daily job logs

[GitHub Actions — Daily support sync](https://github.com/4zure09/my-cryptic-clone/actions) builds and runs the Docker image daily at 06:35 UTC (13:35 Vietnam time). Each run publishes `last-run.log` as a downloadable artifact.

**Deployment Strategy: Why GitHub Actions?**
While platforms like Railway, Render, AWS, or GCP are excellent for application hosting, I chose GitHub Actions for this specific daily job to optimally fulfill the requirement of providing a *publicly accessible link to job logs and artifacts*.

* **Public Visibility & Native Artifacts:** PaaS platforms and IaaS setups typically keep execution logs private. GitHub Actions seamlessly provides public log URLs and natively generates downloadable log artifacts (`last-run.log`) after each run, without requiring additional infrastructure like external S3 buckets.
* **Simplified Configuration:** For a standalone script that executes once a day, a serverless CI/CD runner offers a straightforward cron configuration. It effectively eliminates the overhead of maintaining a dedicated VPS or running a continuous PaaS instance just to trigger a daily schedule.

## Screenshot of assistant answering a sample question

The assistant answers **“How do I add a YouTube video?”** from the uploaded documents and cites the source article URL.

![Assistant answering with cited article URLs](artifacts/screenshots/assistant-youtube-answer.png)
