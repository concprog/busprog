FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN pip install uv && uv sync --frozen --no-dev

COPY cloud_logger.py .

CMD ["uv", "run", "python", "cloud_logger.py"]
