FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/app/ ./app/
COPY backend/migrations/ ./migrations/
COPY backend/alembic.ini ./alembic.ini
COPY --from=frontend /build/dist /app/frontend/dist
RUN useradd --uid 10001 --create-home gauge && chown -R gauge:gauge /app
USER gauge
ENV PATH="/app/backend/.venv/bin:$PATH"
EXPOSE 9200
CMD ["sh","-c","exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-9200} --no-access-log"]
