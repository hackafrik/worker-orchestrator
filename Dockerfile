FROM python:3.12-slim-bookworm

# Install docker CLI for docker-in-docker worker support
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy orchestrator scripts
COPY scripts/ ./scripts/
COPY SKILL.md ADAPTERS.md CAPABILITIES.md README.md LICENSE CHANGELOG.md ./

# Make scripts executable
RUN chmod +x scripts/*.py

# Default: run discover + print registry
CMD ["python3", "scripts/discover_workers.py"]
