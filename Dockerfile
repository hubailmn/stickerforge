FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libwebp/zlib runtime for Pillow wheels are bundled; no apt packages needed.
COPY requirements.txt ./
RUN pip install -r requirements.txt gunicorn

COPY pyproject.toml README.md LICENSE ./
COPY stickerforge ./stickerforge

RUN useradd --create-home --uid 1000 app
USER app

ENV STICKERFORGE_HOST=0.0.0.0 \
    STICKERFORGE_PORT=8000

EXPOSE 8000

# One worker only: packs and sources are held in memory, so multiple
# workers would not share state. Threads handle concurrent requests.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "stickerforge.web:create_app()"]
