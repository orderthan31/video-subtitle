FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY packages/shared /app/packages/shared
COPY apps/api /app/apps/api
COPY workers/media /app/workers/media
RUN pip install --no-cache-dir /app/packages/shared /app/apps/api /app/workers/media \
    && useradd --uid 10001 --create-home media \
    && mkdir -p /data/video-jobs && chown -R media:media /data
USER media
ENV VIDEO_STORAGE_ROOT=/data/video-jobs
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
