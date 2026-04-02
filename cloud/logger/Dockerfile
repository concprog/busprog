FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./

RUN pip install uv && uv sync --no-dev

COPY cloud_logger.py .

CMD ["uv", "run", "python", "cloud_logger.py"]
