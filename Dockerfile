# One image for the engine, the API and the example control tower.
# Runs as root so the engine can write the dataset into the ./data bind mount.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for better layer caching
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY schema ./schema
COPY engine ./engine
COPY adapters ./adapters
COPY api ./api
COPY examples ./examples
COPY scenarios ./scenarios
RUN uv sync --frozen --no-dev


EXPOSE 8000
CMD ["uvicorn", "--factory", "api.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
