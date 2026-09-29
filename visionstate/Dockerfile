# VisionState — Home Assistant app image (amd64 / aarch64).
# Home Assistant passes BUILD_FROM/BUILD_ARCH/BUILD_VERSION; we use our own base images
# because onnxruntime needs glibc (the default HA base images are Alpine/musl).

FROM node:22-bookworm-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim-bookworm
ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VISIONSTATE_VERSION=${BUILD_VERSION} \
    VISIONSTATE_FRONTEND=/app/frontend/dist \
    VISIONSTATE_BUNDLED_MODELS=/opt/visionstate/models

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/visionstate ./visionstate
# Bundle the default AI model so the app works offline.
RUN python -m visionstate.backbones /opt/visionstate/models

COPY --from=frontend /src/dist /app/frontend/dist

LABEL io.hass.type="addon" \
      io.hass.version="${BUILD_VERSION}" \
      io.hass.arch="${BUILD_ARCH}" \
      org.opencontainers.image.source="https://github.com/oleost/VisionState" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 8099
CMD ["python", "-m", "visionstate"]
