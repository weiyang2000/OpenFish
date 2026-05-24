FROM python:3.11-slim

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_APT_MIRROR=https://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=https://deb.debian.org/debian-security

# Prevent Python from writing .pyc files, buffer stdout/stderr, and pin common tooling paths
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/root/.local/bin:${PATH}"

# Install system dependencies required by the scientific Python stack, CloakBrowser, and WeasyPrint PDF
RUN set -euo pipefail; \
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
        ffmpeg; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# Install the latest uv release and expose it on PATH
RUN curl -LsSf --retry 3 --retry-delay 2 --proto '=https' --proto-redir '=https' --tlsv1.2 https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching
COPY requirements.txt ./
RUN uv pip install --system -r requirements.txt

# Copy .env
COPY .env.example .env

# Copy application source
COPY . .

# Ensure runtime directories exist even if ignored in build context
RUN mkdir -p logs final_reports engine_reports

EXPOSE 8000

# Default command launches the FastAPI service layer
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
