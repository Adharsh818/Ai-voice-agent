# Emma voice server (FastAPI + WebSocket) for a Linux cloud VM.
# Python 3.12 keeps the stdlib `audioop` module the telephony path needs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    EMMA_CACHE_DIR=/data/cache \
    EMMA_LOG_DIR=/data/logs \
    EMMA_DB_PATH=/data/emma.db

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --system --uid 10001 emma \
    && mkdir -p /data/cache /data/logs /secrets \
    && chown -R emma:emma /data
USER emma

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4); sys.exit(0)"

CMD ["python", "server.py"]
