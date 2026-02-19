FROM mcr.microsoft.com/devcontainers/python:1-3.14

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml /tmp/uv-tmp/pyproject.toml

USER vscode

WORKDIR /tmp/uv-tmp
RUN uv sync

USER root
RUN rm -rf /tmp/uv-tmp
