FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.13.15-alpine3.23 AS base
RUN apk upgrade --no-cache
FROM base AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md /app/
COPY src /app/src
RUN uv sync --locked --no-dev --no-editable --no-cache \
    && uv pip uninstall --system pip setuptools wheel
FROM base AS runtime
# Only the interpreter and locked application environment are needed at runtime.
COPY --from=build /usr/local/lib/python3.13/site-packages /tmp/clean-site-packages
RUN rm -rf /usr/local/lib/python3.13/site-packages \
    && mv /tmp/clean-site-packages /usr/local/lib/python3.13/site-packages \
    && addgroup -g 10001 updater \
    && adduser -D -u 10001 -G updater updater \
    && mkdir /data && chown updater:updater /data
ENV PYTHONUNBUFFERED=1 CIVPULSE_STATE_DIR=/data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY sources /app/sources
USER updater
HEALTHCHECK --interval=60s --timeout=10s CMD ["/app/.venv/bin/civpulse-updater", "doctor"]
ENTRYPOINT ["/app/.venv/bin/civpulse-updater"]
CMD ["doctor"]
