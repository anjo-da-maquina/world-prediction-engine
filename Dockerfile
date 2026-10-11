FROM python:3.12-slim
WORKDIR /app
COPY wpe/ ./wpe/
COPY config/ ./config/
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "wpe.autonomous", "loop", "--db", "/app/data/runtime.sqlite", "--output", "/app/outputs"]
