// Sentrya - Nodo 3 (Ambiental). ESP32 DevKit V1 + DHT22 + ventilador + valvula (+ buzzer).
//
// - Lee la temperatura del DHT22 cada `readIntervalMs` y hace POST /api/v1/readings al edge.
//   El edge cambia ese intervalo con la orden "sampling" (30 s NORMAL, 5 s WARNING, 1 s CRITICAL;
//   el DHT22 no baja de 2 s).
// - Cada HEARTBEAT_INTERVAL_MS hace POST /api/v1/nodes/node-3/heartbeat con su IP y el estado de
//   ventilador (duty), buzzer y valvula.
// - Atiende POST /command (puerto 80):
//     fan      set {duty 0-100}   (PWM por el transistor)
//     buzzer   on / off           (sin buzzer: lo simula el LED azul)
//     valve    open / close       (rele K1; valvula normalmente cerrada: rele activado = abierta)
//     sampling set {intervalSeconds}
//   y responde { "commandId", "status": "APPLIED"|"FAILED", "simulated": bool }.
// - GET / devuelve el estado del nodo (para probar desde el navegador).
// - Escucha el anuncio UDP del edge (DISCOVERY_PORT) y toma su IP de ahi. EDGE_HOST es el respaldo.
//
// Las reglas viven en el edge; este firmware solo mide y obedece. Proteccion: si pierde contacto
// con el edge, apaga ventilador y buzzer (la valvula no se toca: solo responde a ordenes).
//
// Bibliotecas: "DHT sensor library" (Adafruit) y "ArduinoJson" (v7). Placa: "ESP32 Dev Module".

#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include "soc/gpio_struct.h"
#include "config.h"

WebServer server(80);
DHT dht(PIN_DHT, DHT22);
WiFiUDP discovery;

String edgeHost = EDGE_HOST;   // se actualiza con el anuncio del edge
int edgePort = EDGE_PORT;

int fanDuty = 0;               // 0-100
bool buzzerOn = false;
bool valveOpen = false;
unsigned long readIntervalMs = READ_INTERVAL_MS;
float lastTemperature = NAN;

unsigned long lastReadMs = 0;
unsigned long lastHeartbeatMs = 0;
unsigned long lastEdgeOkMs = 0;

// ---------- Actuadores ----------

bool fanAttached = false;

void writeFan(int duty) {
  if (!HAS_FAN) return;
  if (FAN_ACTIVE_LOW && duty == 0) {
    // Transistor PNP desde 5 V: 3,3 V no lo apaga. Se suelta el pin (como desconectar el cable).
    if (fanAttached) ledcDetach(PIN_FAN);
    fanAttached = false;
    pinMode(PIN_FAN, INPUT);
    return;
  }
  if (!fanAttached) {
    ledcAttach(PIN_FAN, FAN_PWM_FREQ, 8);
    if (FAN_ACTIVE_LOW) {
      ledcOutputInvert(PIN_FAN, true);          // encendido = pin a 0 V
      GPIO.pin[PIN_FAN].pad_driver = 1;         // apagado = pin suelto (drenador abierto)
    }
    fanAttached = true;
  }
  ledcWrite(PIN_FAN, duty * 256 / 100);         // 256 = 100 % con 8 bits
}

void setFan(int duty, const char* reason) {
  duty = constrain(duty, 0, 100);
  if (HAS_FAN && duty > 0 && duty < 100 && fanDuty == 0) {
    writeFan(100);               // un ventilador detenido no arranca con poco duty
    delay(FAN_KICK_MS);
  }
  fanDuty = duty;
  writeFan(duty);
  Serial.printf("[ventilador] %d %% (%s)%s\n", duty, reason, HAS_FAN ? "" : " [simulado]");
}

void setBuzzer(bool on, const char* reason) {
  buzzerOn = on;
  if (HAS_BUZZER && PIN_BUZZER >= 0) digitalWrite(PIN_BUZZER, on ? HIGH : LOW);
  digitalWrite(PIN_LED, on ? HIGH : LOW);
  Serial.printf("[buzzer] %s (%s)%s\n", on ? "ENCENDIDO" : "APAGADO", reason,
                HAS_BUZZER ? "" : " [simulado con LED]");
}

void setValve(bool open, const char* reason) {
  valveOpen = open;
  if (HAS_VALVE_RELAY) {
    bool level = RELAY_ACTIVE_LOW ? !open : open;
    digitalWrite(PIN_VALVE_RELAY, level ? HIGH : LOW);
  }
  Serial.printf("[valvula] %s (%s)%s\n", open ? "ABIERTA" : "CERRADA", reason,
                HAS_VALVE_RELAY ? "" : " [simulada]");
}

// ---------- Cliente HTTP hacia el edge ----------

int postToEdge(const String& path, const String& body) {
  if (WiFi.status() != WL_CONNECTED) return -1;
  HTTPClient http;
  http.setConnectTimeout(1500);
  http.setTimeout(4000);         // por el hotspot, a veces el edge tarda mas de 2 s
  http.begin(String("http://") + edgeHost + ":" + edgePort + path);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  if (code >= 200 && code < 300) {
    lastEdgeOkMs = millis();
  } else if (code > 0) {
    Serial.printf("[edge] %s -> HTTP %d: %s\n", path.c_str(), code, http.getString().c_str());
  } else {
    Serial.printf("[edge] %s -> sin respuesta (%s)\n", path.c_str(), http.errorToString(code).c_str());
  }
  http.end();
  return code;
}

void sendReading() {
  float t = dht.readTemperature();
  JsonDocument doc;
  doc["node"] = NODE_ID;
  doc["deviceKey"] = "temperature";
  doc["metricType"] = "temperature";
  if (isnan(t)) {
    doc["error"] = "dht22";
  } else {
    lastTemperature = t;
    doc["value"] = roundf(t * 10) / 10.0;
    doc["unit"] = "C";
  }
  String body;
  serializeJson(doc, body);
  int code = postToEdge("/api/v1/readings", body);
  if (isnan(t)) Serial.printf("[dht22] ERROR de lectura -> HTTP %d\n", code);
  else Serial.printf("[dht22] temperatura %.1f C (cada %lu s) -> HTTP %d\n", t, readIntervalMs / 1000, code);
}

void sendHeartbeat() {
  JsonDocument doc;
  doc["ip"] = WiFi.localIP().toString();
  doc["actuators"]["fan"] = fanDuty;
  doc["actuators"]["buzzer"] = buzzerOn ? "on" : "off";
  doc["actuators"]["valve"] = valveOpen ? "open" : "closed";
  doc["uptime"] = millis() / 1000;
  String body;
  serializeJson(doc, body);
  int code = postToEdge(String("/api/v1/nodes/") + NODE_ID + "/heartbeat", body);
  Serial.printf("[heartbeat] ip %s ventilador %d %% buzzer %s valvula %s -> HTTP %d\n",
                WiFi.localIP().toString().c_str(), fanDuty, buzzerOn ? "on" : "off",
                valveOpen ? "abierta" : "cerrada", code);
}

// ---------- Servidor HTTP (ordenes del edge) ----------

void sendJson(int status, JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  server.send(status, "application/json", out);
}

void handleCommand() {
  JsonDocument req, res;
  if (deserializeJson(req, server.arg("plain"))) {
    res["error"] = "json invalido";
    sendJson(400, res);
    return;
  }
  const char* actuator = req["actuator"] | "";
  const char* action = req["action"] | "";
  JsonVariant params = req["params"];
  bool ok = false;
  bool simulated = false;

  if (strcmp(actuator, "fan") == 0 && strcmp(action, "set") == 0 && params["duty"].is<float>()) {
    setFan((int)lroundf(params["duty"].as<float>()), "orden del edge");
    ok = true; simulated = !HAS_FAN;
  } else if (strcmp(actuator, "buzzer") == 0 &&
             (strcmp(action, "on") == 0 || strcmp(action, "off") == 0)) {
    setBuzzer(strcmp(action, "on") == 0, "orden del edge");
    ok = true; simulated = !HAS_BUZZER;
  } else if (strcmp(actuator, "valve") == 0 &&
             (strcmp(action, "open") == 0 || strcmp(action, "close") == 0)) {
    setValve(strcmp(action, "open") == 0, "orden del edge");
    ok = true; simulated = !HAS_VALVE_RELAY;
  } else if (strcmp(actuator, "sampling") == 0 && strcmp(action, "set") == 0 &&
             params["intervalSeconds"].is<float>()) {
    unsigned long ms = (unsigned long)(params["intervalSeconds"].as<float>() * 1000);
    readIntervalMs = max(ms, (unsigned long)SENSOR_MIN_MS);
    Serial.printf("[muestreo] lectura cada %lu s (pedido %.1f s)\n", readIntervalMs / 1000,
                  params["intervalSeconds"].as<float>());
    ok = true;
  }
  if (!ok) Serial.printf("[orden] rechazada: %s %s\n", actuator, action);
  lastEdgeOkMs = millis();
  res["commandId"] = req["commandId"];
  res["status"] = ok ? "APPLIED" : "FAILED";
  res["simulated"] = simulated;
  sendJson(200, res);
}

void handleRoot() {
  JsonDocument doc;
  doc["node"] = NODE_ID;
  if (isnan(lastTemperature)) doc["temperature"] = nullptr; else doc["temperature"] = lastTemperature;
  doc["fan"] = fanDuty;
  doc["buzzer"] = buzzerOn ? "on" : "off";
  doc["valve"] = valveOpen ? "open" : "closed";
  doc["readIntervalSeconds"] = readIntervalMs / 1000;
  doc["uptime"] = millis() / 1000;
  doc["edge"] = edgeHost + ":" + edgePort;
  sendJson(200, doc);
}

// ---------- Descubrimiento del edge ----------

void checkDiscovery() {
  int size = discovery.parsePacket();
  if (size <= 0) return;
  char buf[128];
  int len = discovery.read(buf, sizeof(buf) - 1);
  buf[len > 0 ? len : 0] = '\0';
  JsonDocument doc;
  if (deserializeJson(doc, buf) || strcmp(doc["service"] | "", "sentrya-edge") != 0) return;
  String host = discovery.remoteIP().toString();
  int port = doc["port"] | EDGE_PORT;
  if (host != edgeHost || port != edgePort) {
    Serial.printf("[descubrimiento] edge encontrado en %s:%d (antes %s:%d)\n",
                  host.c_str(), port, edgeHost.c_str(), edgePort);
    edgeHost = host;
    edgePort = port;
    lastHeartbeatMs = millis() - HEARTBEAT_INTERVAL_MS;   // avisarle enseguida nuestra IP
  }
}

// ---------- Wi-Fi ----------

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // responde mas rapido a /command
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] conectando a %s", WIFI_SSID);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[wifi] conectado. IP del nodo: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[wifi] no conecta todavia; se sigue reintentando en segundo plano");
  }
}

// ---------- Arranque y bucle ----------

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== Sentrya " NODE_ID " (Ambiental) ===");

  pinMode(PIN_LED, OUTPUT);
  if (HAS_BUZZER && PIN_BUZZER >= 0) pinMode(PIN_BUZZER, OUTPUT);
  if (HAS_VALVE_RELAY) pinMode(PIN_VALVE_RELAY, OUTPUT);
  // Estado seguro al arrancar; el edge reconcilia con el heartbeat si hace falta otra cosa.
  setValve(false, "arranque");
  setFan(0, "arranque");
  setBuzzer(false, "arranque");

  dht.begin();
  connectWiFi();

  server.on("/command", HTTP_POST, handleCommand);
  server.on("/", HTTP_GET, handleRoot);
  server.begin();
  discovery.begin(DISCOVERY_PORT);
  Serial.println("[http] escuchando POST /command en el puerto 80");

  delay(2000);                         // el DHT22 necesita ~2 s tras encender
  lastEdgeOkMs = millis();
  sendHeartbeat();
  sendReading();
  lastHeartbeatMs = lastReadMs = millis();
}

void loop() {
  server.handleClient();
  checkDiscovery();

  if (millis() - lastReadMs >= readIntervalMs) {
    lastReadMs = millis();
    sendReading();
  }
  if (millis() - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = millis();
    sendHeartbeat();
  }
  if ((fanDuty > 0 || buzzerOn) && millis() - lastEdgeOkMs > EDGE_LOST_OFF_MS) {
    setFan(0, "sin contacto con el edge");
    setBuzzer(false, "sin contacto con el edge");
  }
}
