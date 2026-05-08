FROM python:3.12-slim

WORKDIR /app

# Install system deps for tree-sitter language bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY orihime/ ./orihime/

RUN pip install --no-cache-dir -e .

# KuzuDB data directory — mount a volume here
ENV ORIHIME_DB_PATH=/data/orihime.db
VOLUME /data

EXPOSE 7700 7701 7702

# Default: run the write-server (most useful standalone service)
# Override CMD to run a different service:
#   docker run ... python -m orihime ui --port 7700
#   docker run ... python -m orihime serve-sse --port 7702
CMD ["python", "-m", "orihime", "write-server", "--port", "7701", "--db", "/data/orihime.db"]
