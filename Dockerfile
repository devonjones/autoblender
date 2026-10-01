FROM python:3.13-slim-bookworm AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxi6 \
    libxrender1 \
    libxxf86vm1 \
    libxfixes3 \
    libxkbcommon0 \
    libgl1 \
    libegl1 \
    libsm6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir .

# STL tools API for AWS Lambda: docker build --target lambda
FROM base AS lambda
RUN pip install --no-cache-dir awslambdaric boto3
ENV HOME=/tmp
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["autoblender.api.handler"]

# Default target: the `ab` CLI (bin/ab)
FROM base AS cli
WORKDIR /work
ENTRYPOINT ["ab"]
