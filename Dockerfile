# Multi-stage doesn't buy us much here (Playwright's browser cache must
# ship to the runtime stage anyway), so we keep a single image.
#
# Base: Playwright's official Python image. Bundles a working Chromium
# plus all OS libs Chromium needs, so we don't have to apt-get a long
# list of mesa/x11 packages and discover what's missing later.
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:/root/.local/bin:${PATH}"

# Install uv (same package manager used in dev)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

# Install Python deps first so they cache independently of source changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source (bfd package + static UI). tests/ and docs/ stay out of
# the image.
COPY bfd ./bfd

# The Playwright image already has Chromium pre-installed at the version
# matching playwright==v1.49, so no extra `playwright install` needed.

EXPOSE 8080

# Railway provides $PORT at runtime; default to 8080 for local docker run.
CMD ["sh", "-c", "uv run uvicorn bfd.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
