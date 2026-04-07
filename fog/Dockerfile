FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./

RUN pip install uv && uv sync --no-dev

COPY . .

CMD ["uv", "run", "python", "fog_station.py"]
