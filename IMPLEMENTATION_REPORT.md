# BUS Fleet Management — Implementation Document (Rev 2)

---

## System Overview

**Three-layer architecture:** Edge (bus) → Fog (station) → Cloud (log).  
**No GPS on edge.** Position is known only when a hardware trigger fires at a stop.  
**Decision flow:** Fog broadcasts stop advisories; each bus edge node predicts its ETA to the next stop and decides whether to stop.  
**Congestion prediction** lives at the fog layer, using arrival timestamps of all buses.

```
Hardware trigger at stop ──> ESP32 (ETA prediction, stop decision)
                                      │ telemetry / delay
                             Station Fog Node (demand, congestion, choke, advisory)
                                      │ demand / congestion / advisory / choke
                                   Cloud (MQTT log → TimescaleDB)
```

---

## Layer 1 — Edge (Bus Node, ESP32, Arduino IDE)

### Sketch structure

**Single `.ino` file.** All logic lives in one sketch with clearly separated functional sections marked by block comments. Do not split into tabs or multiple files.

**Arduino IDE setup:** install ESP32 board support via `File → Preferences → Additional Board Manager URLs`:
```
https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
```
Then `Tools → Board → Boards Manager → search "esp32" → Install esp32 by Espressif`.  
**Board target:** `ESP32 Dev Module`. Monitor baud: `115200`.

**Library installs** via `Sketch → Include Library → Library Manager`:
- `PubSubClient` by Nick O'Leary ([github.com/knolleary/pubsubclient](https://github.com/knolleary/pubsubclient))
- `ArduinoJson` by Benoit Blanchon ([arduinojson.org](https://arduinojson.org))
---
The above has been done by the user.

**File layout — write in this order inside the single `.ino`:**
1. `#include` directives and config constants
2. Flash-resident stop table (`const struct` array)
3. Global state variables
4. ISR for hardware trigger
5. ARIMA functions
6. MQTT callback and publish functions
7. `setup()`
8. `loop()`

---

### Section 1 — `#include` and configuration constants

**All `#include` directives** must appear before any other code in the sketch:

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ── Identity — change per bus before flashing ─────────────────────
const char* ROUTE_ID    = "29";
const char* VEHICLE_ID  = "1401";
const char* WIFI_SSID   = "YOUR_SSID";
const char* WIFI_PASS   = "YOUR_PASS";
const char* MQTT_BROKER = "192.168.1.100";  // fog station IP
const int   MQTT_PORT   = 1883;

// ── Hardware ──────────────────────────────────────────────────────
const int STOP_TRIGGER_PIN  = 4;   // HIGH pulse from station hardware
const int STATUS_LED        = 2;   // onboard LED

// ── Timing ────────────────────────────────────────────────────────
const unsigned long TELEMETRY_MS = 10000UL;

// ── ARIMA hyper-parameters ────────────────────────────────────────
const uint8_t ARIMA_WIN     = 20;
const float   ARIMA_LR      = 0.05f;
const int     DELAY_OVERRIDE = 5;   // minutes: bus ignores SKIP if this late
```

---

### Section 2 — Flash-resident stop table

**On ESP32 with Arduino, `const` globals are placed in flash automatically** — no `PROGMEM` keyword or `pgm_read_*` calls needed. Access them like normal variables.

**Stop table struct** — one entry per stop in route order:

```cpp
struct StopEntry {
  uint8_t  index;           // sequential position on route, 0-based
  char     stop_id[8];      // TTC stop code, e.g. "3042"
  char     stop_name[24];   // human label, e.g. "Dufferin & Bloor"
  uint32_t sched_offset_s;  // scheduled seconds after route start time
};

// Fill this table for the flashed route before uploading
const StopEntry STOP_TABLE[] = {
  { 0, "14S001", "Dufferin Station",    0   },
  { 1, "14S002", "Dufferin & Bloor",    180 },
  { 2, "14S003", "Dufferin & College",  390 },
  { 3, "14S004", "Dufferin & Dundas",   600 },
  { 4, "14S005", "Dufferin & Queen",    810 },
  // ... all stops on this route
};
const uint8_t NUM_STOPS = sizeof(STOP_TABLE) / sizeof(STOP_TABLE[0]);
```

**Scheduled arrival at stop i:**
```
sched_arrival(i) = routeStartEpoch + STOP_TABLE[i].sched_offset_s
```

```cpp
// routeStartEpoch: unix epoch of scheduled trip departure
// Set at boot via fog MQTT command or hardcoded for testing
uint32_t routeStartEpoch = 0;

uint32_t scheduledArrival(uint8_t i) {
  return routeStartEpoch + STOP_TABLE[i].sched_offset_s;
}
```

---

### Section 3 — Global state variables

**Declare all mutable state** as globals after the stop table, before any function definition:

```cpp
// Stop state
uint8_t  currentStopIndex  = 0;
uint32_t lastArrivalTs     = 0;
int32_t  lastDelayMin      = 0;

// Hardware trigger (set by ISR, cleared by loop)
volatile bool     stopTriggerFired = false;
volatile uint32_t triggerTs        = 0;

// Advisory from fog
char pendingAction[8]  = "STOP";
char pendingReason[16] = "NOMINAL";

// ARIMA state
float   ar[2] = {0.4f, 0.2f};
float   ma[1] = {0.2f};
float   residual = 0.0f;
int32_t obsBuffer[ARIMA_WIN];
float   diffBuffer[ARIMA_WIN];
uint8_t obsHead = 0,  obsCount = 0;
uint8_t diffHead = 0, diffCount = 0;
float   arimaMSE = 0.0f;

// MQTT
WiFiClient   espClient;
PubSubClient mqtt(espClient);
unsigned long lastTelemetryMs = 0;
```

---

### Section 4 — Hardware trigger ISR

**`IRAM_ATTR`** places the ISR in fast internal RAM on ESP32, required for interrupt handlers:

```cpp
void IRAM_ATTR onStopTrigger() {
  stopTriggerFired = true;
  triggerTs        = millis() / 1000;  // coarse seconds; replace with NTP epoch
}
```

**Trigger handler** called from `loop()` — never block inside the ISR:

```cpp
void handleStopTrigger() {
  if (!stopTriggerFired) return;
  stopTriggerFired = false;

  lastArrivalTs = triggerTs;
  uint32_t sched  = scheduledArrival(currentStopIndex);
  lastDelayMin    = ((int32_t)lastArrivalTs - (int32_t)sched) / 60;

  Serial.printf("[STOP] %s  delay=%d min\n",
    STOP_TABLE[currentStopIndex].stop_id, lastDelayMin);

  publishDelay();
  arimaUpdate(lastDelayMin);
  currentStopIndex = (currentStopIndex + 1) % NUM_STOPS;
}
```

---

### Section 5 — ARIMA functions

**Purpose:** predict delay at the *next* stop so fog can anticipate congestion before the bus arrives.

**`arimaUpdate()`** ingests one delay observation, differences it, and runs an SGD update on AR and MA coefficients:

```cpp
void arimaUpdate(int32_t delay_min) {
  obsBuffer[obsHead] = delay_min;
  obsHead = (obsHead + 1) % ARIMA_WIN;
  if (obsCount < ARIMA_WIN) obsCount++;
  if (obsCount < 2) return;

  // First difference: y[t] = obs[t] - obs[t-1]
  uint8_t prev = (obsHead - 2 + ARIMA_WIN) % ARIMA_WIN;
  uint8_t curr = (obsHead - 1 + ARIMA_WIN) % ARIMA_WIN;
  float diff = (float)(obsBuffer[curr] - obsBuffer[prev]);

  // Forecast using last two diffs and last residual
  float y1 = (diffCount > 0) ? diffBuffer[(diffHead-1+ARIMA_WIN)%ARIMA_WIN] : 0;
  float y2 = (diffCount > 1) ? diffBuffer[(diffHead-2+ARIMA_WIN)%ARIMA_WIN] : 0;
  float forecast = ar[0]*y1 + ar[1]*y2 + ma[0]*residual;
  float error    = diff - forecast;

  // SGD update
  ar[0] += ARIMA_LR * error * y1;
  ar[1] += ARIMA_LR * error * y2;
  ma[0] += ARIMA_LR * error * residual;
  ar[0] = constrain(ar[0], -1.5f, 1.5f);
  ar[1] = constrain(ar[1], -1.5f, 1.5f);
  ma[0] = constrain(ma[0], -1.5f, 1.5f);

  residual = error;
  arimaMSE = 0.9f * arimaMSE + 0.1f * (error * error);

  diffBuffer[diffHead] = diff;
  diffHead = (diffHead + 1) % ARIMA_WIN;
  if (diffCount < ARIMA_WIN) diffCount++;
}
```

**`arimaPredict()`** returns predicted delay (minutes) at the next stop:

```cpp
int32_t arimaPredict() {
  if (obsCount < ARIMA_WIN) return lastDelayMin;  // warm-up: propagate last known
  float y1 = diffBuffer[(diffHead-1+ARIMA_WIN)%ARIMA_WIN];
  float y2 = diffBuffer[(diffHead-2+ARIMA_WIN)%ARIMA_WIN];
  float dForecast = ar[0]*y1 + ar[1]*y2 + ma[0]*residual;
  int32_t pred = obsBuffer[(obsHead-1+ARIMA_WIN)%ARIMA_WIN] + (int32_t)roundf(dForecast);
  return constrain(pred, -5, 60);
}
```

**ETA at next stop** combines scheduled arrival and predicted delay:

```cpp
uint32_t etaNextStop() {
  uint8_t next = currentStopIndex % NUM_STOPS;
  return scheduledArrival(next) + (uint32_t)(arimaPredict() * 60);
}
```

---

### Section 6 — MQTT callback and publish functions

**`mqttCallback`** handles incoming advisory and command messages:

```cpp
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  StaticJsonDocument<192> doc;
  deserializeJson(doc, payload, len);
  String t(topic);

  if (t.endsWith("/advisory")) {
    // Apply advisory only if it targets our next stop
    const char* sid = doc["stop_id"] | "";
    if (String(sid) == String(STOP_TABLE[currentStopIndex % NUM_STOPS].stop_id)) {
      strlcpy(pendingAction, doc["action"] | "STOP", sizeof(pendingAction));
      strlcpy(pendingReason, doc["reason"] | "NOMINAL", sizeof(pendingReason));
    }
  }

  // Fog can inject route start epoch (e.g. at trip start)
  if (t.endsWith("/command") && doc.containsKey("route_start_epoch")) {
    routeStartEpoch = doc["route_start_epoch"].as<uint32_t>();
  }
}
```

**Stop decision** overrides SKIP if bus is already late:

```cpp
const char* resolveAction() {
  if (strcmp(pendingAction, "SKIP") == 0 && lastDelayMin > DELAY_OVERRIDE)
    return "STOP";
  return pendingAction;
}
```

**`publishDelay()`** — fired on each stop trigger:

```cpp
void publishDelay() {
  StaticJsonDocument<128> doc;
  doc["vehicle_id"] = VEHICLE_ID;
  doc["route_id"]   = ROUTE_ID;
  doc["stop_id"]    = STOP_TABLE[max(0, (int)currentStopIndex-1) % NUM_STOPS].stop_id;
  doc["ts"]         = lastArrivalTs;
  doc["delay_min"]  = lastDelayMin;
  char buf[128]; serializeJson(doc, buf);
  String topic = "ttc/edge/" + String(ROUTE_ID) + "/" + VEHICLE_ID + "/delay";
  mqtt.publish(topic.c_str(), buf, false);
}
```

**`publishTelemetry()`** — fired every 10 s:

```cpp
void publishTelemetry() {
  uint8_t next = currentStopIndex % NUM_STOPS;
  StaticJsonDocument<256> doc;
  doc["vehicle_id"]   = VEHICLE_ID;
  doc["route_id"]     = ROUTE_ID;
  doc["ts"]           = lastArrivalTs;
  doc["stop_id"]      = STOP_TABLE[max(0,(int)currentStopIndex-1) % NUM_STOPS].stop_id;
  doc["delay_min"]    = lastDelayMin;
  doc["next_stop_id"] = STOP_TABLE[next].stop_id;
  doc["eta_next_ts"]  = etaNextStop();
  doc["pred_delay"]   = arimaPredict();
  doc["action"]       = resolveAction();
  doc["arima_mse"]    = arimaMSE;
  char buf[256]; serializeJson(doc, buf);
  String topic = "ttc/edge/" + String(ROUTE_ID) + "/" + VEHICLE_ID + "/telemetry";
  mqtt.publish(topic.c_str(), buf, false);
}
```

**MQTT topics:**

| Topic | Trigger | QoS |
|-------|---------|-----|
| `ttc/edge/{route}/{vehicle}/telemetry` | Every 10 s | 1 |
| `ttc/edge/{route}/{vehicle}/delay` | Each stop trigger | 1 |
| `ttc/fog/{route}/advisory` *(sub)* | Retained, on connect | 1 |
| `ttc/fog/{route}/command` *(sub)* | On demand | 1 |

---

### `setup()` and `loop()`

**`setup()`** runs once; initialize hardware, WiFi, and MQTT in this order:

```cpp
void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED, OUTPUT);
  pinMode(STOP_TRIGGER_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(STOP_TRIGGER_PIN), onStopTrigger, RISING);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\n[WiFi] Connected: " + WiFi.localIP().toString());

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  // Initial connect happens in loop()
}
```

**`loop()`** — keep it short and non-blocking; `mqtt.loop()` must be called every iteration:

```cpp
void loop() {
  // 1. Reconnect WiFi if dropped
  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();

  // 2. Reconnect MQTT if dropped
  if (!mqtt.connected()) {
    String cid = "ttc-bus-" + String(VEHICLE_ID);
    String lwt = "ttc/edge/" + String(ROUTE_ID) + "/" + VEHICLE_ID + "/heartbeat";
    if (mqtt.connect(cid.c_str(), "ttc_edge", "edge_secret",
                     lwt.c_str(), 1, true, "{\"status\":\"offline\"}")) {
      mqtt.subscribe(("ttc/fog/" + String(ROUTE_ID) + "/advisory").c_str(), 1);
      mqtt.subscribe(("ttc/fog/" + String(ROUTE_ID) + "/command").c_str(),  1);
      digitalWrite(STATUS_LED, HIGH);
    }
  }

  mqtt.loop();  // ← must be called every iteration; processes incoming messages

  // 3. Handle stop trigger (set in ISR, processed here)
  handleStopTrigger();

  // 4. Periodic telemetry
  if (millis() - lastTelemetryMs >= TELEMETRY_MS) {
    if (mqtt.connected()) publishTelemetry();
    lastTelemetryMs = millis();
  }

  delay(50);  // yield to WiFi/TCP stack; never delay() more than a few hundred ms
}
```

**Critical:** never call `delay()` for more than ~200 ms inside `loop()`. Long blocking will disconnect the MQTT client. Move any slow computation (none expected here) to a flag-and-handle pattern like the stop trigger.

---

## Layer 2 — Fog (Station Node, Python)

**One Python process per station.** Runs `fog_station.py` which imports the three modules. Mosquitto 2.0 runs as a system service on the same machine.

---

### Module 2.1 — MQTT Broker (`mosquitto.conf`)

**Mosquitto accepts** edge bus clients and bridges fog topics to cloud:

```
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd

connection ttc_cloud_bridge
address <CLOUD_IP>:1883
topic ttc/fog/+/demand       out 1
topic ttc/fog/+/congestion   out 1
topic ttc/fog/+/advisory     out 1
topic ttc/fog/+/choke        out 1
topic ttc/edge/+/+/delay     out 1
topic ttc/edge/+/+/telemetry out 1
```

---

### Module 2.2 — Demand Estimator (`demand_estimator.py`)

**Queue length** is estimated per `(stop_id, hour)` from TTC ridership CSV (Open Data: `Stop_Boarding_Alighting.csv`). Load once at startup:

```python
import pandas as pd
from collections import deque
import time

class DemandEstimator:
    def __init__(self, stop_id: str, ridership_csv: str):
        df = pd.read_csv(ridership_csv)
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        self._profile = df[df["stop_id"] == stop_id].set_index("hour")

    def queue_length(self, hour: int) -> int:
        # Returns avg boardings for this stop at this hour of day
        if hour in self._profile.index:
            return int(self._profile.loc[hour, "avg_boardings"])
        return 10   # fallback if stop not in CSV
```

**Arrival frequency** — buses per minute in a 5-minute rolling window, measured from live MQTT `delay` messages:

```python
class ArrivalTracker:
    WINDOW_SEC = 300

    def __init__(self):
        self._log: deque = deque()

    def record(self, vehicle_id: str, ts: float):
        self._log.append((ts, vehicle_id))
        cutoff = time.time() - self.WINDOW_SEC
        while self._log and self._log[0][0] < cutoff:
            self._log.popleft()

    def frequency(self) -> float:
        # unique buses seen in window / window_minutes
        unique = len({v for _, v in self._log})
        return unique / (self.WINDOW_SEC / 60.0)
```

**Published on `ttc/fog/{route}/demand`** every 15 s:

```python
payload = {
    "stop_id":      STOP_ID,
    "route_id":     ROUTE_ID,
    "ts":           time.time(),
    "queue_length": demand.queue_length(current_hour),
    "arrival_freq": round(tracker.frequency(), 3),
}
```

---

### Module 2.3 — Congestion Predictor (`congestion_predictor.py`)

**Station-level congestion** is the headway deviation at this stop:

```
congestion_sec = abs(mean_actual_headway_sec - ideal_headway_sec)
```

**Actual headways** computed from sorted arrival timestamps of buses at this station:

```python
import statistics
from collections import deque

class CongestionPredictor:
    def __init__(self, ideal_headway_sec: float):
        self.ideal_hw  = ideal_headway_sec
        self._arrivals: deque = deque(maxlen=20)

    def record_arrival(self, vehicle_id: str, ts: float):
        self._arrivals.append((ts, vehicle_id))

    def congestion(self) -> float:
        if len(self._arrivals) < 2:
            return 0.0
        times = sorted(t for t, _ in self._arrivals)
        headways = [times[i] - times[i-1] for i in range(1, len(times))
                    if 0 < times[i] - times[i-1] < 3600]
        if not headways:
            return 0.0
        return abs(statistics.mean(headways) - self.ideal_hw)

    def mean_headway(self) -> float:
        if len(self._arrivals) < 2:
            return self.ideal_hw
        times = sorted(t for t, _ in self._arrivals)
        hw = [times[i]-times[i-1] for i in range(1,len(times))
              if 0 < times[i]-times[i-1] < 3600]
        return statistics.mean(hw) if hw else self.ideal_hw
```

**Route-wide predicted delay** aggregated from `pred_delay` in telemetry messages across all buses, 10-minute window:

```python
from collections import defaultdict

class RouteDelayAggregator:
    WINDOW_SEC = 600

    def __init__(self):
        self._reports: dict = defaultdict(lambda: deque(maxlen=30))

    def record(self, vehicle_id: str, ts: float, pred_delay: int):
        self._reports[vehicle_id].append((ts, pred_delay))

    def route_mean_pred_delay(self) -> float:
        cutoff = time.time() - self.WINDOW_SEC
        values = [d for records in self._reports.values()
                    for ts, d in records if ts > cutoff]
        return statistics.mean(values) if values else 0.0
```

**Published on `ttc/fog/{route}/congestion`** every 15 s:

```python
payload = {
    "stop_id":               STOP_ID,
    "route_id":              ROUTE_ID,
    "ts":                    time.time(),
    "congestion_sec":        round(cong_pred.congestion(), 1),
    "mean_headway_sec":      round(cong_pred.mean_headway(), 1),
    "ideal_headway_sec":     ideal_hw,
    "route_mean_pred_delay": round(delay_agg.route_mean_pred_delay(), 2),
}
```

---

### Module 2.4 — Choke Detector & Advisory Publisher (`choke_detector.py`)

**Over-supply (choke):** too many buses arriving, demand not keeping up:
```
OVERSUPPLY: arrival_freq > SUPPLY_HIGH  AND  queue_length < DEMAND_LOW
```

**Starvation:** stop is underserved, passengers accumulating, analogous to process starvation:
```
STARVATION: arrival_freq < SUPPLY_LOW  AND  queue_length > DEMAND_HIGH
```

```python
SUPPLY_HIGH = 0.5    # buses/min — tune per route
SUPPLY_LOW  = 0.15   # buses/min
DEMAND_HIGH = 20     # passengers
DEMAND_LOW  = 5      # passengers

def detect_choke(arrival_freq: float, queue_length: int) -> str:
    if arrival_freq > SUPPLY_HIGH and queue_length < DEMAND_LOW:
        return "OVERSUPPLY"
    if arrival_freq < SUPPLY_LOW and queue_length > DEMAND_HIGH:
        return "STARVATION"
    return "NOMINAL"
```

**Advisory mapping:**

| Choke state | Action | Rationale |
|-------------|--------|-----------|
| `OVERSUPPLY` | `SKIP` | Spread out bunched buses |
| `STARVATION` | `STOP` | Every bus must stop; stop is starved |
| `NOMINAL` | `STOP` | Default; stop unless oversupply |

**Advisory published on `ttc/fog/{route}/advisory`** (QoS 1, `retain=True` so buses receive it on connect):

```python
action = "SKIP" if choke_state == "OVERSUPPLY" else "STOP"
advisory = {
    "stop_id":        STOP_ID,
    "route_id":       ROUTE_ID,
    "ts":             time.time(),
    "action":         action,
    "reason":         choke_state,
    "queue_length":   queue_length,
    "arrival_freq":   round(arrival_freq, 3),
    "congestion_sec": congestion_sec,
}
client.publish(f"ttc/fog/{ROUTE_ID}/advisory", json.dumps(advisory), qos=1, retain=True)
```

**Choke state change** also published on `ttc/fog/{route}/choke` (only on transition):

```python
if choke_state != last_choke_state:
    client.publish(f"ttc/fog/{ROUTE_ID}/choke", json.dumps({
        "stop_id":    STOP_ID,
        "ts":         time.time(),
        "choke_type": choke_state,
        "action":     action,
    }), qos=1)
    last_choke_state = choke_state
```

---

### Module 2.5 — Fog Station Main (`fog_station.py`)

**Subscriptions** for this station process:

```python
SUB_DELAY     = f"ttc/edge/{ROUTE_ID}/+/delay"
SUB_TELEMETRY = f"ttc/edge/{ROUTE_ID}/+/telemetry"
```

**Message dispatcher:**

```python
def on_message(client, userdata, msg):
    p   = json.loads(msg.payload)
    vid = p.get("vehicle_id", "")
    ts  = p.get("ts", time.time())

    if msg.topic.endswith("/delay"):
        if p.get("stop_id") == STOP_ID:
            tracker.record(vid, ts)         # arrival freq
            cong_pred.record_arrival(vid, ts)  # headway
        delay_agg.record(vid, ts, p.get("delay_min", 0))

    elif msg.topic.endswith("/telemetry"):
        # Use edge ARIMA prediction for route-wide delay aggregation
        delay_agg.record(vid, ts, p.get("pred_delay", 0))
```

**Evaluation loop** every `EVAL_INTERVAL = 15` s:

```python
def evaluate():
    from datetime import datetime
    hour      = datetime.utcnow().hour
    queue     = demand_est.queue_length(hour)
    freq      = tracker.frequency()
    cong      = cong_pred.congestion()
    choke     = detect_choke(freq, queue)
    publish_demand(queue, freq)
    publish_congestion(cong, delay_agg.route_mean_pred_delay())
    publish_advisory(choke, freq, queue, cong)

client.loop_start()
while True:
    evaluate()
    time.sleep(EVAL_INTERVAL)
```

**All fog-produced topics:**

| Topic | Key fields | Rate |
|-------|-----------|------|
| `ttc/fog/{route}/demand` | `stop_id, queue_length, arrival_freq` | 15 s |
| `ttc/fog/{route}/congestion` | `congestion_sec, mean_headway_sec, route_mean_pred_delay` | 15 s |
| `ttc/fog/{route}/advisory` | `stop_id, action, reason, queue_length` | 15 s, retained |
| `ttc/fog/{route}/choke` | `stop_id, choke_type, action` | on state change only |

---

## Layer 3 — Cloud (AWS EC2, Logging Only)

### Module 3.1 — MQTT Broker (`mosquitto_cloud.conf`)

**Cloud Mosquitto** receives bridged topics from all station fog nodes. No bridge from cloud to fog in this phase:

```
listener 1883
allow_anonymous false
password_file /etc/mosquitto/passwd
```

---

### Module 3.2 — Log Consumer (`cloud_logger.py`)

**All fog and edge topics** subscribed; payloads written raw as JSONB to TimescaleDB with no transformation:

```python
SUB_ALL = [
    ("ttc/fog/+/demand",       1),
    ("ttc/fog/+/congestion",   1),
    ("ttc/fog/+/advisory",     1),
    ("ttc/fog/+/choke",        1),
    ("ttc/edge/+/+/delay",     1),
    ("ttc/edge/+/+/telemetry", 1),
]
```

**Write path** batches inserts every 50 messages or 10 seconds:

```python
buffer = []
last_flush = time.time()

def on_message(client, userdata, msg):
    p = json.loads(msg.payload)
    p["_topic"]     = msg.topic
    p["_ingest_ts"] = time.time()
    buffer.append(p)
    if len(buffer) >= 50 or (time.time() - last_flush) > 10:
        flush()

def flush():
    execute_batch(cur, """
        INSERT INTO raw_events (time, topic, route_id, stop_id, vehicle_id, payload)
        VALUES (to_timestamp(%s), %s, %s, %s, %s, %s)
    """, [(r.get("ts", r["_ingest_ts"]), r["_topic"],
           r.get("route_id"), r.get("stop_id"), r.get("vehicle_id"),
           json.dumps(r)) for r in buffer])
    db.commit()
    buffer.clear()
```

**TimescaleDB schema:**

```sql
CREATE TABLE raw_events (
    time        TIMESTAMPTZ  NOT NULL,
    topic       TEXT,
    route_id    TEXT,
    stop_id     TEXT,
    vehicle_id  TEXT,
    payload     JSONB
);
SELECT create_hypertable('raw_events', 'time', if_not_exists => TRUE);
CREATE INDEX ON raw_events (route_id, time DESC);
CREATE INDEX ON raw_events (stop_id,  time DESC);
```

---

### Module 3.3 — Docker Stack (`docker-compose.yml`)

```yaml
services:
  mosquitto:
    image: eclipse-mosquitto:2.0
    ports: ["1883:1883"]
    volumes:
      - ./mosquitto_cloud.conf:/mosquitto/config/mosquitto.conf

  timescaledb:
    image: timescale/timescaledb:latest-pg15
    environment:
      POSTGRES_DB:       ttc_fleet
      POSTGRES_USER:     ttc
      POSTGRES_PASSWORD: ttc_pass
    ports: ["5432:5432"]

  cloud-logger:
    build: ./cloud/logger
    environment:
      MQTT_BROKER_HOST: mosquitto
      DB_HOST:          timescaledb
    depends_on: [mosquitto, timescaledb]
```

---

## Complete Data Dictionary

| Layer | Topic suffix | Field | Type | Definition |
|-------|-------------|-------|------|------------|
| Edge | `delay` | `delay_min` | `int` | `(actual_arrival_ts - scheduled_arrival_ts) / 60` |
| Edge | `delay` | `stop_id` | `str` | `STOP_TABLE[currentStopIndex].stop_id` at trigger time |
| Edge | `telemetry` | `pred_delay` | `int` | ARIMA(2,1,1) predicted delay at *next* stop (minutes) |
| Edge | `telemetry` | `eta_next_ts` | `uint32` | `scheduledArrival(next) + pred_delay * 60` |
| Edge | `telemetry` | `action` | `str` | Resolved decision: `STOP` or `SKIP` |
| Fog | `demand` | `queue_length` | `int` | Ridership CSV lookup by `(stop_id, hour)` |
| Fog | `demand` | `arrival_freq` | `float` | Unique buses / 5-min window, buses/min |
| Fog | `congestion` | `congestion_sec` | `float` | `abs(mean_actual_headway - ideal_headway_sec)` |
| Fog | `congestion` | `route_mean_pred_delay` | `float` | Mean `pred_delay` across all route buses, 10-min window |
| Fog | `advisory` | `action` | `str` | `STOP` or `SKIP` |
| Fog | `advisory` | `reason` | `str` | `OVERSUPPLY`, `STARVATION`, `NOMINAL` |
| Fog | `choke` | `choke_type` | `str` | `OVERSUPPLY`, `STARVATION`, `NOMINAL` — emitted on change only |

---

## Dependency Summary

| Layer | Package / Tool | Install |
|-------|---------------|---------|
| Edge | `PubSubClient` by Nick O'Leary | Arduino Library Manager |
| Edge | `ArduinoJson` by B. Blanchon | Arduino Library Manager |
| Edge | ESP32 Arduino core | Board Manager (Espressif URL) |
| Fog | `paho-mqtt` | `pip install paho-mqtt` |
| Fog | `pandas` | `pip install pandas` |
| Fog | Mosquitto 2.0 | `apt install mosquitto` |
| Cloud | `paho-mqtt` | `pip install paho-mqtt` |
| Cloud | `psycopg2-binary` | `pip install psycopg2-binary` |
| Cloud | TimescaleDB | Docker `timescale/timescaledb:latest-pg15` |
