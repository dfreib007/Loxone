FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install --no-cache-dir uv==0.8.17

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

COPY src/ ./src/
RUN uv pip install --no-deps .

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

CMD ["uv", "run", "python", "-m", "loxone_voice"]
