FROM python:3.12-slim-bookworm AS builder
WORKDIR /opt
RUN python -m venv ./venv
COPY pyproject.toml ./
RUN ./venv/bin/pip install .

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY main.py ./
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["fastapi", "run", "main.py", "--proxy-headers", "--host", "0.0.0.0", "--port", "8000"]
