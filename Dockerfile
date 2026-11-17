# One small image: the app and, optionally, the postgres source's drivers.
#   docker build -t ente-admin .                          # museum + demo sources
#   docker build --build-arg EXTRAS=postgres -t ente-admin .
#   docker build --target test .                          # ruff + pytest (CI)
FROM python:3.13-slim@sha256:27b7c9d4f13ca20bb1170148f02095d50a5dfce1f7f2dabf5ca273727e43b7ea AS build
ARG EXTRAS=""
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --prefix=/install \
      ".${EXTRAS:+[$EXTRAS]}"

FROM python:3.13-slim@sha256:27b7c9d4f13ca20bb1170148f02095d50a5dfce1f7f2dabf5ca273727e43b7ea AS test
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore -e '.[dev,postgres]' \
 && ruff check . && pytest -q -p no:cacheprovider

FROM python:3.13-slim@sha256:27b7c9d4f13ca20bb1170148f02095d50a5dfce1f7f2dabf5ca273727e43b7ea
COPY --from=build /install /usr/local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ENTE_ADMIN_HOST=0.0.0.0 ENTE_ADMIN_PORT=8000 ENTE_ADMIN_STATE_DIR=/state
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin ente-admin \
 && mkdir /state && chown ente-admin /state
USER 10001
VOLUME /state
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
LABEL org.opencontainers.image.title="ente-admin" org.opencontainers.image.licenses="MIT"
ENTRYPOINT ["ente-admin"]
CMD ["serve"]
