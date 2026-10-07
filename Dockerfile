FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENAI_VECTOR_STORE_NAME=cryptic-optibot-kb \
    CHUNK_MAX_TOKENS=800 \
    CHUNK_OVERLAP_TOKENS=120 \
    SOURCE_BASE_URL=https://support.optisigns.com \
    ARTICLE_LIMIT=40 \
    SOURCE_LOCALE=en-us \
    DATA_DIR=.runtime/articles \
    LOG_LEVEL=INFO

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir .

COPY main.py /app/

ENTRYPOINT ["python"]
CMD ["main.py"]
