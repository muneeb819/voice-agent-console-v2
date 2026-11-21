# Shared image for the CPU services (gateway, agent-runtime, audio-engine, model-registry, llm-fake).
# Build from the repo root:
#   docker build -f docker/service.Dockerfile --build-arg SERVICE_DIR=services/gateway --build-arg MODULE=mmavie_gateway .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd -m -u 10001 app
WORKDIR /srv

COPY packages/mmavie-common /tmp/mmavie-common
RUN pip install /tmp/mmavie-common && rm -rf /tmp/mmavie-common

ARG EXTRA_PIP=""
RUN if [ -n "$EXTRA_PIP" ]; then pip install $EXTRA_PIP; fi

ARG SERVICE_DIR
ARG MODULE
COPY ${SERVICE_DIR}/${MODULE} /srv/${MODULE}
COPY db /srv/db
COPY config /srv/config

USER app
ENV MODULE=${MODULE}
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1
CMD ["sh", "-c", "exec uvicorn --factory \"${MODULE}.main:app_factory\" --host 0.0.0.0 --port 8000 --workers 1 --no-server-header"]
