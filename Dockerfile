# DynaCalendar — multi-stage Docker build (M4 "Docker hardening")
#
# Stage 1: build the Vite frontend (committed package-lock.json).
# Stage 2: slim Python runtime serving the API + built static files.
#
# Build:  docker compose up --build
# Run:    docker compose up        (daemon must be running)

# --------------------------------------------------------------------------- Stage 1: frontend
FROM node:22-slim AS frontend

WORKDIR /app/web

# Install deps from the committed lock file (no node_modules in the image).
COPY web/package.json web/package-lock.json ./
RUN npm ci

# Build the Vite app -> web/dist (calendar/week/inbox + hashed assets).
COPY web/ ./
RUN npm run build

# --------------------------------------------------------------------------- Stage 2: python runtime
FROM python:3.12-slim AS app

WORKDIR /app

ENV DYNACALENDAR_DB=/data/dynacalendar.db \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install server dependencies only (no build-essential needed for slim wheels).
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt

# Application code.
COPY app/ ./app/
# Web HTML/JS source (fallback pages when dist is absent) + built assets.
COPY web/ ./web/
COPY --from=frontend /app/web/dist ./web/dist

# Non-root user; /data is the volume holding the SQLite DB.
RUN adduser --disabled-password --gecos "" dynacalendar \
    && mkdir -p /data \
    && chown -R dynacalendar:dynacalendar /data /app

USER dynacalendar

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
