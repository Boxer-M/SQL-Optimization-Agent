FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY sql ./sql
RUN --mount=type=cache,target=/root/.cache/pip pip install . && useradd --create-home app
USER app
CMD ["python","-m","uvicorn","sql_optimization_agent.api:app","--host","0.0.0.0","--port","8000"]
