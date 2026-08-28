# Proxy + built UI.

# --- stage 1: build the UI ------------------------------------------------------
FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# --- stage 2: python proxy ----------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY proxy.py ./
COPY --from=ui /ui/dist ./ui/dist
ENV PORT=7861
EXPOSE 7861
CMD ["uv", "run", "--no-sync", "proxy.py"]
