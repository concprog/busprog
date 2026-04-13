FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./

RUN apt-get update && apt-get install -y --no-install-recommends netcat-openbsd && rm -rf /var/lib/apt/lists/* && \
    pip install uv && uv sync --no-dev

COPY . .

RUN echo '#!/bin/bash\nhost="${MQTT_BROKER_HOST:-localhost}"\nport="${MQTT_PORT:-1883}"\nuntil nc -z "$host" "$port"; do\n  echo "Waiting for MQTT broker at $host:$port..."\n  sleep 2\ndone\nuv run python fog_station.py' > /entry.sh && chmod +x /entry.sh

CMD ["/entry.sh"]
