# Tezcat container: API + dashboard + research CLI in one image.
# Deterministic startup; all state lives under the mounted /data volume.
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY tezcat/ tezcat/
RUN pip install --no-cache-dir .
COPY scripts/ scripts/
COPY examples/ examples/
COPY benchmarks/ benchmarks/
COPY --from=frontend /app/frontend/dist frontend/dist
ENV TEZCAT_STORE=local \
    TEZCAT_DATA_DIR=/data \
    TEZCAT_FRONTEND_DIST=/app/frontend/dist
VOLUME /data
EXPOSE 8000
CMD ["sh", "-c", "python3 scripts/seed_public_demo.py && exec uvicorn tezcat.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
