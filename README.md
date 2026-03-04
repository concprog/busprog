# BUS Fleet Management — Walkthrough

## Repo Structure

```
busprog/
├── edge/
│   └── bus_node/
│       └── bus_node.ino          ← ESP32 sketch (all 8 sections)
├── fog/
│   ├── pyproject.toml            ← uv project: paho-mqtt, pandas
│   ├── demand_estimator.py       ← DemandEstimator + ArrivalTracker
│   ├── congestion_predictor.py   ← CongestionPredictor + RouteDelayAggregator
│   ├── choke_detector.py         ← detect_choke()
│   ├── fog_station.py            ← main entry point (MQTT + eval loop)
│   └── mosquitto.conf            ← station broker config + cloud bridge
├── cloud/
│   ├── pyproject.toml            ← uv project: paho-mqtt, psycopg2-binary
│   ├── cloud_logger.py           ← MQTT → TimescaleDB batch logger
│   ├── init.sql                  ← raw_events hypertable schema
│   ├── mosquitto_cloud.conf      ← cloud broker config
│   ├── docker-compose.yml        ← mosquitto + timescaledb + logger + nginx-proxy-manager
│   └── logger/
│       └── Dockerfile
├── .gitignore
├── pyproject.toml                ← root (minimal)
└── IMPLEMENTATION_REPORT.md
```

## Verification Results

| Check | Result |
|-------|--------|
| Fog module imports (`demand_estimator`, `congestion_predictor`, `choke_detector`) | ✅ exit 0 |
| Cloud logger imports (`cloud_logger`) | ✅ exit 0 |
| Arduino sketch compilation | 🔲 User handles via Arduino IDE |
| Docker stack (`docker compose up`) | 🔲 User runs when ready |

## Running

**Fog station:**
```bash
cd fog && uv run python fog_station.py
```

**Cloud stack:**
```bash
cd cloud && docker compose up
```

**Edge:** Flash [edge/bus_node/bus_node.ino](edge/bus_node/bus_node.ino) via Arduino IDE (ESP32 Dev Module, 115200 baud).
