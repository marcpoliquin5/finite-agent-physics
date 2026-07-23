ARG PYTHON_IMAGE=python:3.12-alpine3.24@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python -m venv /opt/finite-venv
COPY pyproject.toml README.md LICENSE ./
COPY requirements/container.txt requirements/container.txt
COPY src src
RUN /opt/finite-venv/bin/python -m pip install \
        --constraint requirements/container.txt \
        pip \
    && /opt/finite-venv/bin/python -m pip install \
        --constraint requirements/container.txt \
        ".[api]" \
    && /opt/finite-venv/bin/python -m pip check \
    && /opt/finite-venv/bin/python -m pip uninstall -y \
        pip setuptools wheel jaraco.context

FROM ${PYTHON_IMAGE} AS runtime

ARG VCS_REF=unverified
LABEL org.opencontainers.image.title="FINITE control plane" \
      org.opencontainers.image.description="Constraint-native FINITE v5 REST/SSE control plane" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/marcpoliquin5/finite-agent-physics"

ENV FINITE_STATE_DIR=/var/lib/finite \
    PATH=/opt/finite-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m pip uninstall -y pip setuptools wheel jaraco.context \
    && addgroup -S -g 10001 finite \
    && adduser -S -D -H -u 10001 -G finite -s /sbin/nologin finite \
    && mkdir -p /var/lib/finite \
    && chown 10001:10001 /var/lib/finite \
    && chmod 0750 /var/lib/finite

COPY --from=builder --chown=10001:10001 /opt/finite-venv /opt/finite-venv

USER 10001:10001
WORKDIR /var/lib/finite
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/readyz',timeout=2); p=json.load(r); assert r.status==200 and p.get('status')=='ready' and p.get('checks',{}).get('run_store')=='ok'"]

CMD ["finite-api", "--host", "0.0.0.0", "--port", "8080"]
