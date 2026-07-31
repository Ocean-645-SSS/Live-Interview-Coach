# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120 \
    PIP_DEFAULT_TIMEOUT=120 \
    HTTP_PROXY= \
    HTTPS_PROXY= \
    ALL_PROXY= \
    http_proxy= \
    https_proxy= \
    all_proxy= \
    DEBIAN_FRONTEND=noninteractive
WORKDIR /app

FROM base AS builder
ARG BUILD_HTTP_PROXY=
ARG BUILD_HTTPS_PROXY=${BUILD_HTTP_PROXY}
ENV HTTP_PROXY=${BUILD_HTTP_PROXY} \
    HTTPS_PROXY=${BUILD_HTTPS_PROXY} \
    ALL_PROXY=${BUILD_HTTP_PROXY} \
    http_proxy=${BUILD_HTTP_PROXY} \
    https_proxy=${BUILD_HTTPS_PROXY} \
    all_proxy=${BUILD_HTTP_PROXY}
RUN python -m pip install --no-cache-dir uv==0.8.15
RUN if [ -n "${BUILD_HTTP_PROXY}" ]; then \
        printf 'Acquire::http::Proxy "%s";\nAcquire::https::Proxy "%s";\nAcquire::ForceIPv4 "true";\nAcquire::Retries "5";\n' "${BUILD_HTTP_PROXY}" "${BUILD_HTTPS_PROXY}" > /etc/apt/apt.conf.d/99build-proxy; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl pkg-config \
    && rm -f /etc/apt/apt.conf.d/99build-proxy \
    && rm -rf /var/lib/apt/lists/*
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY liverag ./liverag
RUN uv sync --frozen --no-dev

FROM base AS runtime
ARG BUILD_HTTP_PROXY=
ARG BUILD_HTTPS_PROXY=${BUILD_HTTP_PROXY}
RUN HTTP_PROXY="${BUILD_HTTP_PROXY}" \
    HTTPS_PROXY="${BUILD_HTTPS_PROXY}" \
    ALL_PROXY="${BUILD_HTTP_PROXY}" \
    http_proxy="${BUILD_HTTP_PROXY}" \
    https_proxy="${BUILD_HTTPS_PROXY}" \
    all_proxy="${BUILD_HTTP_PROXY}" \
    sh -c 'if [ -n "$HTTP_PROXY" ]; then printf "Acquire::http::Proxy \"%s\";\nAcquire::https::Proxy \"%s\";\nAcquire::ForceIPv4 \"true\";\nAcquire::Retries \"5\";\n" "$HTTP_PROXY" "$HTTPS_PROXY" > /etc/apt/apt.conf.d/99build-proxy; fi' \
    && HTTP_PROXY="${BUILD_HTTP_PROXY}" HTTPS_PROXY="${BUILD_HTTPS_PROXY}" http_proxy="${BUILD_HTTP_PROXY}" https_proxy="${BUILD_HTTPS_PROXY}" \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libglib2.0-0 libgomp1 \
    && rm -f /etc/apt/apt.conf.d/99build-proxy \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    LIVERAG_USER_DATA_DIR=/data \
    LIVERAG_LOG_DIR=/data/logs \
    LIVERAG_API_HOST=0.0.0.0 \
    LIVERAG_API_PORT=9821 \
    KB_SERVICE_HOST=0.0.0.0 \
    KB_SERVICE_PORT=9721
VOLUME ["/data"]
EXPOSE 9821 9721
CMD ["liverag-api"]
