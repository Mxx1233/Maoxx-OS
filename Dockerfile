FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

RUN groupadd --gid 1000 maoxx \
    && useradd --uid 1000 --gid 1000 --create-home maoxx \
    && mkdir -p /app/storage \
    && chown -R maoxx:maoxx /app

USER maoxx

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
