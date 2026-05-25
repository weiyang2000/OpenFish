# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_APT_MIRROR=https://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=https://deb.debian.org/debian-security
ARG PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PYPI_EXTRA_INDEX_URLS=
# Docker build can only auto-detect CUDA when nvidia-smi is visible inside the build container.
ARG BETTAFISH_TORCH_VARIANT=auto
ARG PYTORCH_CUDA_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG TARGETPLATFORM

# Prevent Python from writing .pyc files, buffer stdout/stderr, and pin common tooling paths
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/root/.cache/uv \
    PATH="/root/.local/bin:${PATH}"

# Install system dependencies required by the scientific Python stack, CloakBrowser, and WeasyPrint PDF
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -euo pipefail; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    apt_source_files=(); \
    if [ -f /etc/apt/sources.list ]; then \
        apt_source_files+=("/etc/apt/sources.list"); \
    fi; \
    if [ -d /etc/apt/sources.list.d ]; then \
        while IFS= read -r -d '' source_file; do \
            apt_source_files+=("${source_file}"); \
        done < <(find /etc/apt/sources.list.d -type f \( -name '*.list' -o -name '*.sources' \) -print0); \
    fi; \
    if [ "${#apt_source_files[@]}" -gt 0 ]; then \
        sed -i \
            -e "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|https://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|https://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
            -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MIRROR}|g" \
            -e "s|https://deb.debian.org/debian|${DEBIAN_APT_MIRROR}|g" \
            "${apt_source_files[@]}"; \
    fi; \
    apt-get update; \
    if apt-cache show libgdk-pixbuf-2.0-0 >/dev/null 2>&1; then \
        GDK_PIXBUF_PKG=libgdk-pixbuf-2.0-0; \
    else \
        GDK_PIXBUF_PKG=libgdk-pixbuf2.0-0; \
    fi; \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libgl1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        "${GDK_PIXBUF_PKG}" \
        libffi-dev \
        libcairo2 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxtst6 \
        libnss3 \
        libxrandr2 \
        libxkbcommon0 \
        libasound2 \
        libx11-xcb1 \
        libxshmfence1 \
        libgbm1 \
        nodejs \
        ffmpeg

# Install the latest uv release and expose it on PATH
RUN curl -LsSf --retry 3 --retry-delay 2 --proto '=https' --proto-redir '=https' --tlsv1.2 https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked <<'BASH'
set -euo pipefail

pypi_args=(--index-url "${PYPI_INDEX_URL}")
for index_url in ${PYPI_EXTRA_INDEX_URLS}; do
  pypi_args+=(--extra-index-url "${index_url}")
done

torch_variant="${BETTAFISH_TORCH_VARIANT}"
if [ "${torch_variant}" = "auto" ]; then
  if [ "$(uname -s)" = "Darwin" ]; then
    torch_variant="cpu"
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    torch_variant="cuda"
  else
    torch_variant="cpu"
  fi
fi

if [ "$(uname -s)" = "Darwin" ] && [ "${torch_variant}" = "cuda" ]; then
  echo "macOS detected; CUDA/NVIDIA dependencies are disabled for this image build." >&2
  torch_variant="cpu"
fi

case "${torch_variant}" in
  cpu)
    echo "Installing Python dependencies from ${PYPI_INDEX_URL} with CPU PyTorch resolution."
    uv pip install --system "${pypi_args[@]}" -r requirements.txt
    ;;
  cuda)
    echo "Installing CUDA PyTorch wheels from ${PYTORCH_CUDA_INDEX_URL}."
    uv pip install --system --index-url "${PYTORCH_CUDA_INDEX_URL}" torch torchvision torchaudio
    temp_requirements="$(mktemp)"
    trap 'rm -f "${temp_requirements}"' EXIT
    awk 'BEGIN { IGNORECASE = 1 } /^[[:space:]]*(torch|torchvision|torchaudio)([<=>[:space:]]|$)/ { next } { print }' requirements.txt > "${temp_requirements}"
    echo "Installing non-CUDA Python dependencies from ${PYPI_INDEX_URL}."
    uv pip install --system "${pypi_args[@]}" -r "${temp_requirements}"
    ;;
  *)
    echo "Unsupported BETTAFISH_TORCH_VARIANT=${BETTAFISH_TORCH_VARIANT}; use auto, cpu, or cuda." >&2
    exit 2
    ;;
esac
BASH

# Copy the real runtime environment file into the image.
COPY .env .env

# Copy application source
COPY . .

# Ensure runtime directories exist even if ignored in build context
RUN mkdir -p logs final_reports engine_reports

EXPOSE 8000

# Default command launches the FastAPI service layer
CMD ["uvicorn", "apps.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
