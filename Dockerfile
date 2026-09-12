FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
ARG WITH_NVIDIA_VAD=0
ARG VAD_TORCH_VERSION=2.14.0+cpu
ARG VAD_TORCHAUDIO_VERSION=2.11.0+cpu
ARG VAD_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
COPY scripts/download-vad-model.py /app/scripts/download-vad-model.py
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fontconfig fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN if [ "$WITH_NVIDIA_VAD" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends build-essential libsndfile1 \
      && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir "torch==$VAD_TORCH_VERSION" "torchaudio==$VAD_TORCHAUDIO_VERSION" --index-url "$VAD_TORCH_INDEX_URL" \
      && pip install --no-cache-dir 'nemo_toolkit[asr]==3.0.0' \
      && python /app/scripts/download-vad-model.py /opt/models/vad_multilingual_marblenet.nemo; \
    fi
COPY packages/shared /app/packages/shared
COPY apps/api /app/apps/api
COPY workers/media /app/workers/media
COPY scripts/manage-account.py /app/scripts/manage-account.py
COPY scripts/check-container.py /app/scripts/check-container.py
COPY scripts/migrate-video-assets.py /app/scripts/migrate-video-assets.py
COPY scripts/backfill-thumbnails.py /app/scripts/backfill-thumbnails.py
RUN pip install --no-cache-dir /app/packages/shared /app/apps/api /app/workers/media \
    && useradd --uid 10001 --create-home media \
    && mkdir -p /data/video-jobs && chown -R media:media /data
USER media
ENV VIDEO_STORAGE_ROOT=/data/video-jobs
RUN python /app/scripts/check-container.py
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
