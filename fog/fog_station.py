import json
import time
from datetime import datetime

import paho.mqtt.client as paho_mqtt

from demand_estimator import DemandEstimator, ArrivalTracker
from congestion_predictor import CongestionPredictor, RouteDelayAggregator
from choke_detector import detect_choke

# ── Configuration ─────────────────────────────────────────────────

ROUTE_ID = "29"
STOP_ID = "14S001"
IDEAL_HEADWAY_SEC = 180.0
EVAL_INTERVAL = 5
RIDERSHIP_CSV = "Stop_Boarding_Alighting.csv"

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_USER = "ttc_fog"
MQTT_PASS = "fog_secret"

# ── Components ────────────────────────────────────────────────────

demand_est = DemandEstimator(STOP_ID, RIDERSHIP_CSV)
tracker = ArrivalTracker()
cong_pred = CongestionPredictor(IDEAL_HEADWAY_SEC)
delay_agg = RouteDelayAggregator()

last_choke_state = "NOMINAL"

# ── MQTT Setup ────────────────────────────────────────────────────

SUB_DELAY = f"ttc/edge/{ROUTE_ID}/+/delay"
SUB_TELEMETRY = f"ttc/edge/{ROUTE_ID}/+/telemetry"


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe([(SUB_DELAY, 1), (SUB_TELEMETRY, 1)])


def on_message(client, userdata, msg):
    p = json.loads(msg.payload)
    vid = p.get("vehicle_id", "")
    ts = time.time()

    if msg.topic.endswith("/delay"):
        if p.get("stop_id") == STOP_ID:
            tracker.record(vid, ts)
            cong_pred.record_arrival(vid, ts)
        delay_agg.record(vid, ts, p.get("delay_min", 0))

    elif msg.topic.endswith("/telemetry"):
        delay_agg.record(vid, ts, p.get("pred_delay", 0))


# ── Publishers ────────────────────────────────────────────────────


def publish_demand(client, queue_length, arrival_freq):
    payload = {
        "stop_id": STOP_ID,
        "route_id": ROUTE_ID,
        "ts": time.time(),
        "queue_length": queue_length,
        "arrival_freq": round(arrival_freq, 3),
    }
    client.publish(f"ttc/fog/{ROUTE_ID}/demand", json.dumps(payload), qos=1)


def publish_congestion(client, congestion_sec, route_mean_pred_delay):
    payload = {
        "stop_id": STOP_ID,
        "route_id": ROUTE_ID,
        "ts": time.time(),
        "congestion_sec": round(congestion_sec, 1),
        "mean_headway_sec": round(cong_pred.mean_headway(), 1),
        "ideal_headway_sec": IDEAL_HEADWAY_SEC,
        "route_mean_pred_delay": round(route_mean_pred_delay, 2),
    }
    client.publish(f"ttc/fog/{ROUTE_ID}/congestion", json.dumps(payload), qos=1)


def publish_advisory(client, choke_state, arrival_freq, queue_length, congestion_sec):
    global last_choke_state

    action = (
        "SKIP"
        if choke_state == "OVERSUPPLY"
        else ("STOP" if choke_state == "STARVATION" else "STOP")
    )
    advisory = {
        "stop_id": STOP_ID,
        "route_id": ROUTE_ID,
        "ts": time.time(),
        "action": action,
        "reason": choke_state,
        "queue_length": queue_length,
        "arrival_freq": round(arrival_freq, 3),
        "congestion_sec": congestion_sec,
    }
    client.publish(
        f"ttc/fog/{ROUTE_ID}/advisory", json.dumps(advisory), qos=1, retain=True
    )

    if choke_state != last_choke_state:
        client.publish(
            f"ttc/fog/{ROUTE_ID}/choke",
            json.dumps(
                {
                    "stop_id": STOP_ID,
                    "ts": time.time(),
                    "choke_type": choke_state,
                    "action": action,
                }
            ),
            qos=1,
        )
        last_choke_state = choke_state


# ── Main Loop ─────────────────────────────────────────────────────


def evaluate(client):
    hour = datetime.utcnow().hour
    queue = demand_est.queue_length(hour)
    freq = tracker.frequency()
    cong = cong_pred.congestion()
    choke = detect_choke(freq, queue)

    publish_demand(client, queue, freq)
    publish_congestion(client, cong, delay_agg.route_mean_pred_delay())
    publish_advisory(client, choke, freq, queue, cong)


def main():
    client = paho_mqtt.Client(
        paho_mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"ttc-fog-{STOP_ID}",
    )
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()

    try:
        while True:
            evaluate(client)
            time.sleep(EVAL_INTERVAL)
    except KeyboardInterrupt:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
