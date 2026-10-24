# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    /venv/bin/pip install --upgrade pip && /venv/bin/pip install -r /tmp/requirements.txt \
    && /venv/bin/pip uninstall -y pip

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/venv/bin:$PATH"
RUN groupadd --system --gid 10002 fiscal && useradd --system --uid 10002 --gid fiscal --home /nonexistent fiscal \
    && python -m pip uninstall -y pip && rm -rf /usr/local/lib/python3.13/ensurepip /usr/local/bin/pip*
COPY --from=build /venv /venv
WORKDIR /srv
COPY engine.py ./
USER fiscal
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if b'\"ok\"' in urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).read() else 1)"
# Pas d'accès réseau sortant (réseau Docker « internal ») : la situation fiscale ne sort jamais.
CMD ["uvicorn", "engine:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log", "--no-server-header"]
