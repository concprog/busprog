import json
import time
import os
import logging
import socket

import paho.mqtt.client as paho_mqtt
import psycopg2
from psycopg2.extras import execute_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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
DB_MAX_RETRIES = 12
DB_RETRY_DELAY_BASE = 2

MQTT_MAX_RETRIES = 5
MQTT_RETRY_DELAY_BASE = 3

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
arima_buffer: list[dict] = []
headway_buffer: list[dict] = []
advisory_buffer: list[dict] = []
last_flush = time.time()
db = None
cur = None


def wait_for_db_port(host: str, port: int, timeout: int = 30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                logger.info(f"Port {port} is open on {host}")
                return True
        except socket.error:
            pass
        time.sleep(1)
    return False


def connect_db():
    global db, cur

    if not wait_for_db_port(DB_HOST, DB_PORT, timeout=30):
        logger.warning("PostgreSQL port not ready, attempting connection anyway")

    for attempt in range(DB_MAX_RETRIES):
        try:
            db = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASS,
            )
            db.autocommit = False
            cur = db.cursor()
            logger.info("Connected to PostgreSQL successfully")
            return
        except psycopg2.OperationalError as e:
            if attempt < DB_MAX_RETRIES - 1:
                delay = DB_RETRY_DELAY_BASE**attempt
                logger.warning(
                    f"DB connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(f"DB connection failed after {DB_MAX_RETRIES} attempts")
                raise


def flush():
    global last_flush
    if not buffer and not arima_buffer and not headway_buffer and not advisory_buffer:
        return

    try:
        if buffer:
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

        if arima_buffer:
            execute_batch(
                cur,
                """
                INSERT INTO arima_predictions (time, route_id, stop_id, vehicle_id, delay_min, pred_delay)
                VALUES (to_timestamp(%s), %s, %s, %s, %s, %s)
                """,
                [
                    (
                        r.get("ts", r["_ingest_ts"]),
                        r.get("route_id"),
                        r.get("stop_id"),
                        r.get("vehicle_id"),
                        r.get("delay_min"),
                        r.get("pred_delay"),
                    )
                    for r in arima_buffer
                ],
            )

        if headway_buffer:
            execute_batch(
                cur,
                """
                INSERT INTO headway_metrics (time, route_id, stop_id, mean_hw_sec, ideal_hw_sec, congestion_sec, choke_state)
                VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        r.get("ts", r["_ingest_ts"]),
                        r.get("route_id"),
                        r.get("stop_id"),
                        r.get("mean_headway_sec"),
                        r.get("ideal_headway_sec"),
                        r.get("congestion_sec"),
                        r.get("choke_state"),
                    )
                    for r in headway_buffer
                ],
            )

        if advisory_buffer:
            execute_batch(
                cur,
                """
                INSERT INTO advisories (time, route_id, stop_id, action, reason, queue_length, arrival_freq)
                VALUES (to_timestamp(%s), %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        r.get("ts", r["_ingest_ts"]),
                        r.get("route_id"),
                        r.get("stop_id"),
                        r.get("action"),
                        r.get("reason"),
                        r.get("queue_length"),
                        r.get("arrival_freq"),
                    )
                    for r in advisory_buffer
                ],
            )

        db.commit()
    except Exception as e:
        logger.error(f"[FLUSH ERROR] {e}")
        db.rollback()

    buffer.clear()
    arima_buffer.clear()
    headway_buffer.clear()
    advisory_buffer.clear()
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

    if msg.topic.endswith("/telemetry") and "delay_min" in p and "pred_delay" in p:
        arima_buffer.append(p)
    elif msg.topic.endswith("/congestion"):
        headway_buffer.append(p)
    elif msg.topic.endswith("/advisory"):
        advisory_buffer.append(p)
    else:
        buffer.append(p)

    if (
        len(buffer) + len(arima_buffer) + len(headway_buffer) + len(advisory_buffer)
    ) >= BATCH_SIZE or (time.time() - last_flush) > FLUSH_INTERVAL:
        flush()


# ── Main ──────────────────────────────────────────────────────────


def main():
    logger.info("Starting cloud logger, waiting for services...")

    logger.info("Waiting for PostgreSQL to be ready...")
    if not wait_for_db_port(DB_HOST, DB_PORT, timeout=60):
        logger.error("PostgreSQL not available after 60s timeout")

    logger.info("Connecting to database...")
    connect_db()

    logger.info(f"Connecting to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}...")
    client = paho_mqtt.Client(
        paho_mqtt.CallbackAPIVersion.VERSION2,
        client_id="ttc-cloud-logger",
    )
    client.on_connect = on_connect
    client.on_message = on_message

    for attempt in range(MQTT_MAX_RETRIES):
        try:
            result = client.connect(MQTT_BROKER, MQTT_PORT, 60)
            if result == 0:
                logger.info("MQTT connected successfully")
                break
        except Exception as e:
            if attempt < MQTT_MAX_RETRIES - 1:
                delay = MQTT_RETRY_DELAY_BASE**attempt
                logger.warning(
                    f"MQTT connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"MQTT connection failed after {MQTT_MAX_RETRIES} attempts"
                )
                raise

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        flush()
        cur.close()
        db.close()
        client.disconnect()


if __name__ == "__main__":
    main()
