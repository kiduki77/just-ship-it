FROM ghcr.io/astral-sh/uv:python3.13-trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends git git-lfs socat tar ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
ENV UV_PROJECT_ENVIRONMENT=/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR=/uv-cache \
    PYTHONDONTWRITEBYTECODE=1
COPY entrypoint.sh summary.py /opt/runner/
ENTRYPOINT ["bash", "/opt/runner/entrypoint.sh"]
