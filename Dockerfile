# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- web build
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --fund=false
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DYNACALENDAR_DB=/app/data/dynacalendar.db

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY samples ./samples
COPY docs ./docs
COPY README.md AGENTS.md BLUEPRINT.md CONTRIBUTING.md ./
COPY web ./web
COPY --from=web /web/dist ./web/dist

RUN mkdir -p /app/data

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
