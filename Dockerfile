FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/data/hf

# Non-root runtime user. On Linux hosts the bind-mounted ./data and ./logs must
# be writable by this UID:  sudo chown -R 10001:10001 data logs
# (or build with --build-arg APP_UID=$(id -u) to match your host user).
ARG APP_UID=10001
ARG APP_GID=10001

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} app \
    && useradd --system --uid ${APP_UID} --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config ./config
COPY knowledge_base ./knowledge_base
COPY scripts ./scripts
COPY src ./src
COPY tree.md ./tree.md

RUN mkdir -p /app/data /app/logs /app/credentials && chown -R app:app /app/data /app/logs

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# One process per container: the in-process queue and state are per instance.
# Scale out with replicas + QUEUE_BACKEND=redis / STATE_BACKEND=redis.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "30", \
     "--no-server-header"]
