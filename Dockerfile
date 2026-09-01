FROM python:3.14.7-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml requirements.lock README.md ./
COPY src ./src
RUN python -m pip wheel --constraint requirements.lock --wheel-dir /wheels .

FROM python:3.14.7-slim-bookworm AS runtime

ARG POSTGRESQL_CLIENT_VERSION=17.11-1.pgdg12+2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --show-error --silent \
      --output /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
      "postgresql-client-17=${POSTGRESQL_CLIENT_VERSION}" \
    && apt-get purge --yes --auto-remove curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 homefinder \
    && useradd --system --uid 10001 --gid homefinder --home-dir /app homefinder \
    && python -m venv /opt/venv

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* \
    && python -m pip uninstall --yes pip setuptools \
    && rm -rf /wheels

WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "homefinder.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
