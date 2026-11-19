# Chat Media dashboard: React UI + FastAPI backend in one small image.
#   docker compose up -d        then open http://localhost:8000

# ---- 1. build the web app ---------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY dashboard/frontend/package.json dashboard/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/frontend/ ./
RUN npm run build

# ---- 2. the app ---------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    IGCM_CORE_DIR=/app \
    FRONTEND_DIST=/app/web \
    PYTHONPATH=/app \
    HOST=0.0.0.0 \
    PORT=8000 \
    WEB_WORKERS=4 \
    MAX_REQUESTS=2000 \
    MAX_REQUESTS_JITTER=200 \
    ENGINE_MAX_JOBS=50

# ffmpeg makes preview pictures for videos (optional but nice)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY dashboard/backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY ig_chat_media.py ./
COPY dashboard/backend/app ./app
COPY --from=web /web/dist ./web

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=4)"

# Launcher: N web workers (each replaced after MAX_REQUESTS requests) + one background engine.
CMD ["python", "-m", "app.serve"]
