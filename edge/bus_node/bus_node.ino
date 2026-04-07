// ══════════════════════════════════════════════════════════════════
//  BUS Fleet Edge Node — ESP32 Arduino Sketch
//  Mode: Simulated button press (5–20 sec interval)
// ══════════════════════════════════════════════════════════════════

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

const char* ROUTE_ID    = "29";
<<<<<<< Updated upstream
const char* VEHICLE_ID  = "1402";
const char* WIFI_SSID   = "TECNO POVA 2";
const char* WIFI_PASS   = "WPA2 PSK";
const char* MQTT_BROKER = "192.168.1.100";
=======
const char* VEHICLE_ID  = "1401";
const char* WIFI_SSID   = "5";
const char* WIFI_PASS   = "9122awsd225";
const char* MQTT_BROKER = "10.51.1.61";
>>>>>>> Stashed changes
const int   MQTT_PORT   = 1883;

const int STATUS_LED = 2;

const unsigned long TELEMETRY_MS = 3000UL;

const uint8_t ARIMA_WIN = 5;
const float   ARIMA_LR  = 0.4f;
const int     DELAY_OVERRIDE = 5;

// ── Stop Table ───────────────────────────────────────────────────

struct StopEntry {
  uint8_t  index;
  char     stop_id[8];
  char     stop_name[24];
  uint32_t sched_offset_s;
};

const StopEntry STOP_TABLE[] = {
  { 0, "14S001", "Dufferin Station",    0   },
  { 1, "14S002", "Dufferin & Bloor",    180 },
  { 2, "14S003", "Dufferin & College",  390 },
  { 3, "14S004", "Dufferin & Dundas",   600 },
  { 4, "14S005", "Dufferin & Queen",    810 },
};

const uint8_t NUM_STOPS = sizeof(STOP_TABLE) / sizeof(STOP_TABLE[0]);

uint32_t routeStartEpoch = 0;

uint32_t scheduledArrival(uint8_t i) {
  return routeStartEpoch + STOP_TABLE[i].sched_offset_s;
}

// ── State ────────────────────────────────────────────────────────

uint8_t  currentStopIndex  = 0;
uint32_t lastArrivalTs     = 0;
int32_t  lastDelayMin      = 0;

// Simulation variables
unsigned long nextSimulatedPressMs = 0;

char pendingAction[8]  = "STOP";
char pendingReason[16] = "NOMINAL";

// ARIMA
float   ar[2] = {0.4f, 0.2f};
float   ma[1] = {0.2f};
float   residual = 0.0f;
int32_t obsBuffer[ARIMA_WIN];
float   diffBuffer[ARIMA_WIN];
uint8_t obsHead = 0, obsCount = 0;
uint8_t diffHead = 0, diffCount = 0;
float   arimaMSE = 0.0f;

// Network
WiFiClient   espClient;
PubSubClient mqtt(espClient);
unsigned long lastTelemetryMs = 0;

// ── ARIMA ────────────────────────────────────────────────────────

void arimaUpdate(int32_t delay_min) {
  obsBuffer[obsHead] = delay_min;
  obsHead = (obsHead + 1) % ARIMA_WIN;
  if (obsCount < ARIMA_WIN) obsCount++;
  if (obsCount < 2) return;

  uint8_t prev = (obsHead - 2 + ARIMA_WIN) % ARIMA_WIN;
  uint8_t curr = (obsHead - 1 + ARIMA_WIN) % ARIMA_WIN;
  float diff = (float)(obsBuffer[curr] - obsBuffer[prev]);

  float y1 = (diffCount > 0) ? diffBuffer[(diffHead-1+ARIMA_WIN)%ARIMA_WIN] : 0;
  float y2 = (diffCount > 1) ? diffBuffer[(diffHead-2+ARIMA_WIN)%ARIMA_WIN] : 0;
  float forecast = ar[0]*y1 + ar[1]*y2 + ma[0]*residual;
  float error = diff - forecast;

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

int32_t arimaPredict() {
  if (obsCount < ARIMA_WIN) return lastDelayMin;

  float y1 = diffBuffer[(diffHead-1+ARIMA_WIN)%ARIMA_WIN];
  float y2 = diffBuffer[(diffHead-2+ARIMA_WIN)%ARIMA_WIN];
  float dForecast = ar[0]*y1 + ar[1]*y2 + ma[0]*residual;

  int32_t pred = obsBuffer[(obsHead-1+ARIMA_WIN)%ARIMA_WIN] + (int32_t)roundf(dForecast);
  return constrain(pred, -5, 60);
}

uint32_t etaNextStop() {
  uint8_t next = currentStopIndex % NUM_STOPS;
  return scheduledArrival(next) + (uint32_t)(arimaPredict() * 60);
}

// ── MQTT ─────────────────────────────────────────────────────────

void mqttCallback(char* topic, byte* payload, unsigned int len) {
  StaticJsonDocument<192> doc;
  deserializeJson(doc, payload, len);
  String t(topic);

  if (t.endsWith("/advisory")) {
    strlcpy(pendingAction, doc["action"] | "STOP", sizeof(pendingAction));
    strlcpy(pendingReason, doc["reason"] | "NOMINAL", sizeof(pendingReason));
    currentStopIndex = 0;
  }

  if (t.endsWith("/command") && doc.containsKey("route_start_epoch")) {
    routeStartEpoch = doc["route_start_epoch"].as<uint32_t>();
  }
}

const char* resolveAction() {
  if (strcmp(pendingAction, "SKIP") == 0 && lastDelayMin > DELAY_OVERRIDE)
    return "STOP";
  return pendingAction;
}

void publishDelay() {
  StaticJsonDocument<128> doc;
  doc["vehicle_id"] = VEHICLE_ID;
  doc["route_id"]   = ROUTE_ID;
  doc["stop_id"]    = STOP_TABLE[max(0,(int)currentStopIndex-1)%NUM_STOPS].stop_id;
  doc["ts"]         = lastArrivalTs;
  doc["delay_min"]  = lastDelayMin;

  char buf[128];
  serializeJson(doc, buf);

  String topic = "ttc/edge/" + String(ROUTE_ID) + "/" + VEHICLE_ID + "/delay";
  mqtt.publish(topic.c_str(), buf, false);
}

void publishTelemetry() {
  uint8_t next = currentStopIndex % NUM_STOPS;

  StaticJsonDocument<256> doc;
  doc["vehicle_id"]   = VEHICLE_ID;
  doc["route_id"]     = ROUTE_ID;
  doc["ts"]           = lastArrivalTs;
  doc["stop_id"]      = STOP_TABLE[max(0,(int)currentStopIndex-1)%NUM_STOPS].stop_id;
  doc["delay_min"]    = lastDelayMin;
  doc["next_stop_id"] = STOP_TABLE[next].stop_id;
  doc["eta_next_ts"]  = etaNextStop();
  doc["pred_delay"]   = arimaPredict();
  doc["action"]       = resolveAction();
  doc["arima_mse"]    = arimaMSE;

  char buf[256];
  serializeJson(doc, buf);

  String topic = "ttc/edge/" + String(ROUTE_ID) + "/" + VEHICLE_ID + "/telemetry";
  mqtt.publish(topic.c_str(), buf, false);
}

// ── Simulated Trigger ───────────────────────────────────────────

void handleStopTrigger() {
  unsigned long now = millis();

  if (now >= nextSimulatedPressMs) {

    Serial.println("Simulated Button Press");

    uint32_t triggerTs = now / 1000;
    lastArrivalTs = triggerTs;

    uint32_t sched  = scheduledArrival(currentStopIndex);
    lastDelayMin    = ((int32_t)lastArrivalTs - (int32_t)sched) / 60;

    Serial.printf("[STOP] %s  delay=%d min\n",
      STOP_TABLE[currentStopIndex].stop_id, lastDelayMin);

    publishDelay();
    arimaUpdate(lastDelayMin);

    currentStopIndex = (currentStopIndex + 1) % NUM_STOPS;

    nextSimulatedPressMs = now + random(1000, 7000);
  }
}

// ── Setup ───────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED, OUTPUT);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n[WiFi] Connected: " + WiFi.localIP().toString());

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);

  routeStartEpoch = millis() / 1000;

  // Initialize simulation
  randomSeed(millis());
  nextSimulatedPressMs = millis() + random(1000, 7000);
}

// ── Loop ────────────────────────────────────────────────────────

void loop() {
  if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();

  if (!mqtt.connected()) {
    String cid = "ttc-bus-" + String(VEHICLE_ID);
    String lwt = "ttc/edge/" + String(ROUTE_ID) + "/" + VEHICLE_ID + "/heartbeat";

    if (mqtt.connect(cid.c_str(), "ttc_edge", "edge_secret",
                     lwt.c_str(), 1, true, "{\"status\":\"offline\"}")) {

      mqtt.subscribe(("ttc/fog/" + String(ROUTE_ID) + "/advisory").c_str(), 1);
      mqtt.subscribe(("ttc/fog/" + String(ROUTE_ID) + "/command").c_str(), 1);

      digitalWrite(STATUS_LED, HIGH);
    }
  }

  mqtt.loop();
  handleStopTrigger();

  if (millis() - lastTelemetryMs >= TELEMETRY_MS) {
    if (mqtt.connected()) publishTelemetry();
    lastTelemetryMs = millis();
  }

  delay(50);
}