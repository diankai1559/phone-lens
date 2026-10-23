FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PHONELENS_HOME=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY phonelens ./phonelens

RUN pip install ".[web,browser]" \
    && playwright install --with-deps chromium \
    && useradd -m -u 10001 app \
    && mkdir -p /data \
    && chown -R app:app /ms-playwright /data

USER app
VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "-m", "phonelens", "serve", "--host", "0.0.0.0", "--port", "8000"]
