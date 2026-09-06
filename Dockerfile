FROM python:3.13-slim-bookworm

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

WORKDIR /work
ENTRYPOINT ["ab"]
