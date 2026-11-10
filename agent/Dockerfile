FROM ghcr.io/github/github-mcp-server:v2.1.0 AS github-mcp
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY buddy ./buddy
COPY --from=github-mcp /server/github-mcp-server /usr/local/bin/github-mcp-server
COPY github_mcp.py /app/github_mcp.py
RUN pip install --no-cache-dir uv==0.9.5 && uv sync --frozen --no-dev --no-editable
RUN useradd --system --uid 10001 buddy
USER buddy
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8081
CMD ["python", "-m", "buddy"]
