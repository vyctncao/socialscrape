FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# UV_NO_CACHE: the image keeps uv's download cache otherwise, a second copy of every package (about 0.45 GB).
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app

# Dependencies first, with faster-whisper for AI transcription (PyAV decodes the audio, so no ffmpeg is needed).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra whisper --no-install-project

# The model is baked into the image. Otherwise the first transcription downloads it (about 0.5 GB for small) in the
# middle of a request, and again after every restart. Build with --build-arg WHISPER_MODEL=<name> for another one.
ARG WHISPER_MODEL=small
ENV HF_HOME=/opt/huggingface \
    SOCIALSCRAPE_TRANSCRIBE=faster-whisper \
    SOCIALSCRAPE_WHISPER_MODEL=${WHISPER_MODEL}
RUN .venv/bin/python -c "import os, faster_whisper; faster_whisper.download_model(os.environ['SOCIALSCRAPE_WHISPER_MODEL'])"

COPY socialscrape ./socialscrape
RUN uv sync --frozen --no-dev --extra whisper

# HF_HUB_OFFLINE: load the baked model without asking huggingface.co for a newer revision first. Another
# SOCIALSCRAPE_WHISPER_MODEL needs HF_HUB_OFFLINE=0 (it downloads on first use) or a rebuild with WHISPER_MODEL.
ENV PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8000 \
    SOCIALSCRAPE_CACHE_PATH= \
    HF_HUB_OFFLINE=1

EXPOSE 8000

CMD ["socialscrape"]
