# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------- build : dépendances dans un venv isolé
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
RUN python -m venv /venv
COPY pyproject.toml ./
COPY app ./app
# Derrière un proxy TLS d'entreprise : docker build --secret id=extra_ca,src=ca.crt (jamais copié dans l'image).
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    /venv/bin/pip install --upgrade pip && /venv/bin/pip install .

# ---------------------------------------------------------------- runtime : minimal, non-root, FS en lecture seule
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH" \
    BANK_DATABASE_URL=sqlite:////data/bank.db
RUN groupadd --system --gid 10001 bank && useradd --system --uid 10001 --gid bank --home /nonexistent bank \
    && mkdir -p /data && chown bank:bank /data && chmod 700 /data
COPY --from=build /venv /venv
WORKDIR /srv
COPY --chown=root:root app ./app
COPY --chown=root:root scripts ./scripts
USER bank
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"
# --proxy-headers : l'IP client vient de nginx (seul conteneur qui peut joindre l'API).
# --no-access-log : notre propre journal d'accès ne contient jamais de query string.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log", "--no-server-header", "--workers", "1"]
