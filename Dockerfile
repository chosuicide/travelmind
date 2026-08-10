FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend
RUN corepack enable && corepack prepare pnpm@10.15.1 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
ARG VITE_API_BASE_URL=""
ARG VITE_AMAP_JS_KEY=""
ARG VITE_AMAP_SECURITY_CODE=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_AMAP_JS_KEY=${VITE_AMAP_JS_KEY}
ENV VITE_AMAP_SECURITY_CODE=${VITE_AMAP_SECURITY_CODE}
RUN pnpm build


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVE_FRONTEND=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

CMD ["python", "production.py"]
