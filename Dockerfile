FROM node:22-alpine AS web-build

WORKDIR /app/app-ui

COPY app-ui/package.json app-ui/package-lock.json ./
RUN npm ci

COPY app-ui/ ./

ARG VITE_API_BASE_URL=""
ARG VITE_CLERK_PUBLISHABLE_KEY=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY}

RUN npm run build


FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY core/ ./core/
COPY migrations/ ./migrations/
COPY README.md ./README.md

RUN pip install --no-cache-dir -e .

COPY --from=web-build /app/app-ui/dist ./app-ui/dist

ENV AFTERTASTE_SERVE_WEB=1
ENV AFTERTASTE_API_HOST=0.0.0.0
ENV AFTERTASTE_API_PORT=8765
ENV AFTERTASTE_DB_PATH=/data/aftertaste.db
ENV AFTERTASTE_CLOUD_TENANT_DB_DIR=/data/cloud-tenants

EXPOSE 8765

CMD ["python", "-m", "core.api"]
