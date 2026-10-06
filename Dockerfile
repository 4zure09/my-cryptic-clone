FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir .

COPY main.py /app/
RUN mkdir -p /app/data/articles

ENTRYPOINT ["python"]
CMD ["main.py"]
