FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tezcat/ tezcat/
COPY --from=frontend /app/frontend/dist frontend/dist
ENV TEZCAT_STORE=local TEZCAT_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "tezcat.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
