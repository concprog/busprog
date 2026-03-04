import json
import time
import os

import paho.mqtt.client as paho_mqtt
import psycopg2
from psycopg2.extras import execute_batch

# ── Configuration ─────────────────────────────────────────────────

MQTT_BROKER = os.environ.get("MQTT_BROKER_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("POSTGRES_DB", "ttc_fleet")
DB_USER = os.environ.get("POSTGRES_USER", "ttc")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "ttc_pass")

BATCH_SIZE = 50
FLUSH_INTERVAL = 10

SUB_ALL = [
    ("ttc/fog/+/demand", 1),
    ("ttc/fog/+/congestion", 1),
    ("ttc/fog/+/advisory", 1),
    ("ttc/fog/+/choke", 1),
    ("ttc/edge/+/+/delay", 1),
    ("ttc/edge/+/+/telemetry", 1),
]

# ── Database ──────────────────────────────────────────────────────

buffer: list[dict] = []
last_flush = time.time()
db = None
cur = None


def connect_db():
    global db, cur
    db = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    db.autocommit = False
    cur = db.cursor()


def flush():
    global last_flush
    if not buffer:
        return
    try:
        execute_batch(
            cur,
            """
            INSERT INTO raw_events (time, topic, route_id, stop_id, vehicle_id, payload)
            VALUES (to_timestamp(%s), %s, %s, %s, %s, %s)
            """,
            [
                (
                    r.get("ts", r["_ingest_ts"]),
                    r["_topic"],
                    r.get("route_id"),
                    r.get("stop_id"),
                    r.get("vehicle_id"),
                    json.dumps(r),
                )
                for r in buffer
            ],
        )
        db.commit()
    except Exception as e:
        print(f"[FLUSH ERROR] {e}")
        db.rollback()
    buffer.clear()
    last_flush = time.time()


# ── MQTT ──────────────────────────────────────────────────────────


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe(SUB_ALL)


def on_message(client, userdata, msg):
    try:
        p = json.loads(msg.payload)
    except json.JSONDecodeError:
        return
    p["_topic"] = msg.topic
    p["_ingest_ts"] = time.time()
    buffer.append(p)

    if len(buffer) >= BATCH_SIZE or (time.time() - last_flush) > FLUSH_INTERVAL:
        flush()


# ── Main ──────────────────────────────────────────────────────────


def main():
    connect_db()

    client = paho_mqtt.Client(
        paho_mqtt.CallbackAPIVersion.VERSION2,
        client_id="ttc-cloud-logger",
    )
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, 60)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        flush()
        cur.close()
        db.close()
        client.disconnect()


if __name__ == "__main__":
    main()
