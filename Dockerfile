FROM python:3.12-slim-bookworm AS builder
WORKDIR /opt
RUN python -m venv ./venv
COPY pyproject.toml ./
RUN ./venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY *.py ./
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["fastapi", "run", "main.py", "--proxy-headers", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
